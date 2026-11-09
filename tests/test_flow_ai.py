"""flow_ai.py と、業務フローのAI呼び出しのテスト（AI-BIC 2.0 機能B）。

AIは呼ばない（偽物のSDKと、デモの事前作成データを使う。料金ゼロ）。
指示書 9.2 の「AIが不正な構造化データを返した場合」「AI APIのタイムアウト」を含む。
"""

import anthropic
import httpx2 as httpx
import pytest

from ai_bic.config import MAX_FLOW_CALLS, MODEL_FLOW, load_framework
from ai_bic.demo_client import DemoClient, load_scenarios
from ai_bic.errors import AIError
from ai_bic.flow_ai import check_flow_result, flow_calls_left, generate_flows, start_blank, to_flows
from ai_bic.models import (
    AIFlowEdge,
    AIFlowStep,
    AIToBeFlow,
    AIToBeStep,
    BusinessInfo,
    FlowResult,
    HearingState,
    HearingTurn,
)
from ai_bic.project import status
from tests.test_ai_client import INFO, client_with, db, logs, response  # noqa: F401（db はフィクスチャ）
from tests.test_project import make_project

FW = load_framework("general")


def ai_step(ref="S1", quote="各営業所から届くExcelを1つにまとめる", minutes=10.0, basis="AI推定", **kw) -> AIFlowStep:
    data = dict(ref=ref, name=f"工程{ref}", kind="作業", actor="", description="", system="", manual=True,
                minutes_per_case=minutes, time_basis=basis, evidence_quote=quote, occurrence_rate=100.0,
                wait_or_rework="", problem="", needs_confirmation=False)
    data.update(kw)
    return AIFlowStep(**data)


def tb_step(ref="S1", change="変更なし", **kw) -> AIToBeStep:
    return AIToBeStep(**ai_step(ref, **kw).model_dump(), change=change, change_note="")


def result(proposals=1, steps=None, edges=None, to_be=None) -> FlowResult:
    steps = steps if steps is not None else [ai_step("S1"), ai_step("S2")]
    edges = edges if edges is not None else [AIFlowEdge(source="S1", target="S2", label="")]
    if to_be is None:
        to_be = [AIToBeFlow(proposal_no=n, steps=[tb_step("S1"), tb_step("S2", change="自動化")],
                            edges=[AIFlowEdge(source="S1", target="S2", label="")], kept_controls=[])
                 for n in range(1, proposals + 1)]
    return FlowResult(as_is_steps=steps, as_is_edges=edges, questions=[], to_be=to_be)


# --- 形の確認（違反があれば呼び直す） ---
def test_正しい形なら違反なし():
    assert check_flow_result(result(2), 2) == []


@pytest.mark.parametrize(
    "bad, expected",
    [
        (dict(steps=[ai_step("S1"), ai_step("S1")]), "重複"),
        (dict(steps=[ai_step("1")], edges=[]), "S1, S2"),
        (dict(edges=[AIFlowEdge(source="S1", target="S9", label="")]), "存在しない工程"),
        (dict(edges=[AIFlowEdge(source="S1", target="S1", label="")]), "自分自身"),
        (dict(steps=[ai_step("S1", minutes=None, basis="ヒアリング"), ai_step("S2")]), "作業時間がないのに"),
        (dict(steps=[ai_step("S1", occurrence_rate=120), ai_step("S2")]), "実施割合"),
        (dict(steps=[]), "工程は1〜"),
    ],
)
def test_形の崩れを見つける(bad, expected):
    problems = check_flow_result(result(1, **bad), 1)
    assert any(expected in p for p in problems), problems


def test_改善後フローの数が改善案と合わなければ違反():
    assert any("改善案2つ" in p for p in check_flow_result(result(1), 2))


# --- 利用者の発言との照合 ---
STATE = HearingState(turns=[HearingTurn(turn_no=1, item_id="H3", question="q", answer="合計で12時間くらいです。")])


def test_ヒアリングの時間は_数値を含む発言を引用していればそのまま():
    flows = to_flows(result(1, steps=[ai_step("S1", quote="合計で12時間くらいです", minutes=720, basis="ヒアリング"),
                                      ai_step("S2")]), INFO, STATE, ["P1"])
    assert flows.as_is.step("S1").minutes_source == "ヒアリング"
    assert flows.notes == []


@pytest.mark.parametrize("quote", ["転記に時間がかかる", "毎月15時間かけています", None])
def test_ヒアリングの時間でも_発言を確認できなければAI推定に格下げする(quote):
    """ヒアリングで得ていない数値を、実測値のように見せない（指示書 3.2）。"""
    flows = to_flows(result(1, steps=[ai_step("S1", quote=quote, minutes=90, basis="ヒアリング"), ai_step("S2")]),
                     INFO, STATE, ["P1"])
    assert flows.as_is.step("S1").minutes_source == "AI推定"
    assert any("「AI推定」としています" in n for n in flows.notes)


def test_根拠の発言がない現状の工程は要確認にする():
    flows = to_flows(result(1, steps=[ai_step("S1", quote="部長へメールで送付している"), ai_step("S2")]),
                     INFO, STATE, ["P1"])
    assert flows.as_is.step("S1").needs_confirmation
    assert not flows.as_is.step("S2").needs_confirmation  # 業務概要にある文の引用
    assert any("「要確認」としています" in n for n in flows.notes)


def test_改善後の工程は改善案IDに対応づけ_変化を引き継ぐ():
    flows = to_flows(result(2), INFO, STATE, ["P1", "P2"])
    assert set(flows.to_be) == {"P1", "P2"}
    assert flows.to_be["P2"].step("S2").change == "自動化"


# --- 案件への反映と、AIの回数の上限 ---
class Client:
    def __init__(self, outcome):
        self.outcome, self.calls = outcome, 0

    def make_flows(self, info, state, analysis, proposals):
        self.calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def test_業務フローを作ると案件に入り_版が記録される():
    project = make_project(proposals=2)
    generate_flows(Client(result(2)), project)
    assert project.as_is is not None and set(project.to_be) == {"P1", "P2"}
    assert status(project, "to_be", "P1") == "完了"
    assert project.ai_calls["flow"] == 1


def test_AIで作れるのは1案件1回まで():
    project = make_project(proposals=1)
    generate_flows(Client(result(1)), project)
    assert flow_calls_left(project) == MAX_FLOW_CALLS - 1 == 0
    client = Client(result(1))
    with pytest.raises(AIError) as e:
        generate_flows(client, project)
    assert e.value.kind == "limit" and client.calls == 0  # 上限なら呼ばない


def test_失敗した回は数えない():
    project = make_project(proposals=1)
    with pytest.raises(AIError):
        generate_flows(Client(AIError("connection")), project)
    assert flow_calls_left(project) == MAX_FLOW_CALLS and project.as_is is None


def test_デモの案件は回数を数えない():
    project = make_project("demo", proposals=1, demo_scenario_id="sales_report")
    generate_flows(Client(result(1)), project)
    generate_flows(Client(result(1)), project)
    assert project.ai_calls.get("flow", 0) == 0


def test_空のフローから自分で作れる():
    project = make_project(proposals=2)
    start_blank(project)
    assert project.as_is.steps == [] and set(project.to_be) == {"P1", "P2"}
    assert project.ai_calls == {}  # AIは使っていない


# --- 本物のAIの呼び出し方（偽物のSDK） ---
def proposals(n=1):
    from tests.fakes import make_proposal, make_proposal_result

    return make_proposal_result([make_proposal() for _ in range(n)])


def test_業務フローは設定どおりのモデルで1回呼ばれ_記録される(db):
    client, sdk = client_with(db, response(result(1)))
    client.make_flows(INFO, HearingState(), make_project().diagnosis.analysis, proposals(1))
    assert sdk.calls[0]["model"] == MODEL_FLOW
    assert "<proposals>" in sdk.calls[0]["messages"][0]["content"]
    assert logs(db)[0][0] == "flow" and logs(db)[0][4] == "success"


def test_AIが不正な構造の業務フローを返したら1回だけ呼び直す(db):
    bad = result(1, edges=[AIFlowEdge(source="S1", target="S9", label="")])
    client, sdk = client_with(db, response(bad), response(bad))
    with pytest.raises(AIError) as e:
        client.make_flows(INFO, HearingState(), make_project().diagnosis.analysis, proposals(1))
    assert e.value.kind == "invalid_output" and len(sdk.calls) == 2


def test_業務フローの呼び出しが時間切れなら通信エラーとして扱う(db):
    timeout = anthropic.APITimeoutError(request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
    client, _ = client_with(db, timeout)
    with pytest.raises(AIError) as e:
        client.make_flows(INFO, HearingState(), make_project().diagnosis.analysis, proposals(1))
    assert e.value.kind == "connection"


# --- デモの事前作成フロー ---
@pytest.mark.parametrize("scenario", load_scenarios(), ids=lambda s: s["id"])
def test_デモの業務フローは本物と同じ確認と照合を通る(scenario):
    info = BusinessInfo(**scenario["business_info"])
    state = HearingState(turns=[
        HearingTurn(turn_no=no, item_id=t["next_item_id"], question=t["question"], answer=t["demo_answer"])
        for no, t in enumerate(scenario["turns"], start=1)
    ])
    from ai_bic.models import ProposalResult

    proposal_result = ProposalResult.model_validate(scenario["proposals"])
    flows = DemoClient(scenario).make_flows(info, state, None, proposal_result)
    count = len(proposal_result.proposals)
    assert check_flow_result(flows, count) == []
    converted = to_flows(flows, info, state, [f"P{n}" for n in range(1, count + 1)])
    assert len(converted.to_be) == count
    if scenario["id"] == "sales_report":
        # わざと入れた2つの例で、照合の仕組みが働く様子を見せる
        assert converted.as_is.step("S3").minutes_source == "AI推定"
        assert converted.as_is.step("S6").needs_confirmation
