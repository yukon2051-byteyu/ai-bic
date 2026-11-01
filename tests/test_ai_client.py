"""ai_client.py のテスト（本物のAIの呼び出し方・エラー処理・利用状況の記録）。

Anthropic SDK の代わりに偽物（FakeSDK）を渡すので、APIは呼ばない（料金ゼロ）。
本物のAPIでの確認は、別途AI品質評価で行う。
"""

import sqlite3
from contextlib import closing
from types import SimpleNamespace

import anthropic
import httpx2 as httpx  # anthropic SDK 2.x が使う通信ライブラリ（SDKのエラーを作るのに使う）
import pytest

from ai_bic import config
from ai_bic.ai_client import ClaudeClient
from ai_bic.errors import AIError
from ai_bic.hearing import start_hearing
from ai_bic.models import AnalysisResult, BusinessInfo, ProposalResult
from tests.fakes import make_question

FW = config.load_framework("general")
INFO = BusinessInfo(
    company_name=None,
    department="営業管理部",
    task_name="月次売上集計",
    overview="各営業所から届くExcelを1つにまとめる。",
    pain_points="転記に時間がかかる。",
)
REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


# ---------------------------------------------------------------
# 偽物のSDK
# ---------------------------------------------------------------
def response(parsed, stop_reason="end_turn", input_tokens=1000, output_tokens=200):
    """SDKの応答に似せたもの（使う項目だけ）。"""
    return SimpleNamespace(
        parsed_output=parsed,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def status_error(cls, code):
    return cls("error", response=httpx.Response(code, request=REQUEST), body=None)


class FakeSDK:
    """messages.parse(...) が呼ばれたら、渡した応答（または例外）を順に返す。"""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.messages = self  # sdk.messages.parse(...) の形で呼べるようにする

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "usage_test.db")


def client_with(db, *outcomes):
    sdk = FakeSDK(*outcomes)
    return ClaudeClient(api_key="dummy", session_id="s1", db_path=db, sdk_client=sdk), sdk


def logs(db):
    with closing(sqlite3.connect(db)) as conn:
        return conn.execute(
            "SELECT call_type, model, input_tokens, output_tokens, status, error_type FROM usage_log"
        ).fetchall()


# ---------------------------------------------------------------
# 正常系
# ---------------------------------------------------------------
def test_質問生成は設定どおりのモデルと形式で呼ばれる(db):
    client, sdk = client_with(db, response(make_question()))

    result = client.ask_next_question(INFO, start_hearing(FW), FW)

    assert result.next_item_id == "H5"
    call = sdk.calls[0]
    assert call["model"] == config.MODEL_QUESTION
    assert call["max_tokens"] == config.MAX_TOKENS_QUESTION
    assert call["output_config"] == {"effort": config.EFFORT_QUESTION}
    assert call["output_format"].__name__ == "QuestionResult"
    assert "<business_info>" in call["messages"][0]["content"]
    assert "従わず" in call["system"]  # システムプロンプトが渡っている


def test_成功した呼び出しはトークン数とともに記録される(db):
    client, _ = client_with(db, response(make_question(), input_tokens=1500, output_tokens=300))
    client.ask_next_question(INFO, start_hearing(FW), FW)
    assert logs(db) == [("question", config.MODEL_QUESTION, 1500, 300, "success", None)]


def test_分析と提案はそれぞれのモデルで呼ばれる(db):
    analysis = AnalysisResult(business_summary="", issues=[], no_issue_viewpoints=[])
    client, sdk = client_with(db, response(analysis))
    client.analyze(INFO, start_hearing(FW), FW)
    assert sdk.calls[0]["model"] == config.MODEL_ANALYSIS


# ---------------------------------------------------------------
# 想定外の内容 → 1回だけ呼び直す
# ---------------------------------------------------------------
def test_内容チェックに失敗したら1回呼び直し_成功すればそれを返す(db):
    bad = make_question(next_item_id="H9")  # 存在しない診断項目
    good = make_question()
    client, sdk = client_with(db, response(bad), response(good))

    result = client.ask_next_question(INFO, start_hearing(FW), FW)

    assert result.next_item_id == "H5"
    assert len(sdk.calls) == 2
    assert [row[4:] for row in logs(db)] == [("error", "invalid_output"), ("success", None)]


def test_2回とも失敗したらinvalid_outputのエラー_3回目は呼ばない(db):
    bad = make_question(next_item_id="H9")
    client, sdk = client_with(db, response(bad), response(bad), response(make_question()))

    with pytest.raises(AIError) as e:
        client.ask_next_question(INFO, start_hearing(FW), FW)

    assert e.value.kind == "invalid_output"
    assert len(sdk.calls) == 2


def test_改善案のルール違反も呼び直しの対象になる(db):
    analysis = AnalysisResult(business_summary="", issues=[], no_issue_viewpoints=[])
    empty = ProposalResult(proposals=[])  # 0件は違反
    client, sdk = client_with(db, response(empty), response(empty))
    with pytest.raises(AIError):
        client.propose(INFO, start_hearing(FW), analysis)
    assert len(sdk.calls) == 2


# ---------------------------------------------------------------
# 停止理由
# ---------------------------------------------------------------
@pytest.mark.parametrize(
    ("stop_reason", "kind"),
    [("refusal", "refusal"), ("max_tokens", "truncated")],
)
def test_回答の拒否や出力上限はエラーとして記録する(db, stop_reason, kind):
    client, sdk = client_with(db, response(None, stop_reason=stop_reason))
    with pytest.raises(AIError) as e:
        client.ask_next_question(INFO, start_hearing(FW), FW)
    assert e.value.kind == kind
    assert len(sdk.calls) == 1  # 呼び直さない
    assert logs(db)[0][4:] == ("error", kind)


def test_回答を読み取れなかったらinvalid_output(db):
    client, _ = client_with(db, response(None))
    with pytest.raises(AIError) as e:
        client.ask_next_question(INFO, start_hearing(FW), FW)
    assert e.value.kind == "invalid_output"


# ---------------------------------------------------------------
# APIのエラー（異常系：APIキー・通信・利用制限・障害）
# ---------------------------------------------------------------
@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (status_error(anthropic.AuthenticationError, 401), "auth"),  # APIキーが無効
        (status_error(anthropic.PermissionDeniedError, 403), "auth"),
        (status_error(anthropic.RateLimitError, 429), "rate_limit"),  # 利用制限
        (status_error(anthropic.InternalServerError, 500), "server"),
        (status_error(anthropic.APIStatusError, 529), "server"),  # 混雑
        (status_error(anthropic.BadRequestError, 400), "bad_request"),
        (anthropic.APIConnectionError(request=REQUEST), "connection"),  # 通信エラー
        (anthropic.APITimeoutError(request=REQUEST), "connection"),  # 時間切れ
    ],
)
def test_APIのエラーを種類ごとに振り分けて記録する(db, error, kind):
    client, sdk = client_with(db, error)

    with pytest.raises(AIError) as e:
        client.ask_next_question(INFO, start_hearing(FW), FW)

    assert e.value.kind == kind
    assert len(sdk.calls) == 1  # 内容チェックの呼び直しはしない（SDKの自動再試行に任せる）
    assert logs(db) == [("question", config.MODEL_QUESTION, 0, 0, "error", kind)]


def test_SDKは設定どおりの再試行回数と待ち時間で作られる(db):
    client = ClaudeClient(api_key="dummy", session_id="s1", db_path=db)
    assert client.sdk.max_retries == config.API_MAX_RETRIES
    assert client.sdk.timeout == config.API_TIMEOUT_SECONDS


def test_エラーの詳細に業務内容を含めない(db):
    secret = "社外秘の取引先A社との契約"
    info = INFO.model_copy(update={"pain_points": secret})
    client, _ = client_with(db, status_error(anthropic.RateLimitError, 429))
    with pytest.raises(AIError) as e:
        client.ask_next_question(info, start_hearing(FW), FW)
    assert secret not in str(e.value)
