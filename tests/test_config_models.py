"""config.py と models.py のテスト（設定の読み込み・データの形）。"""

import pytest
from pydantic import ValidationError

from ai_bic import config
from ai_bic.models import AnalysisResult, Issue, Proposal, ProposalResult, QuestionResult

# --- 診断フレームワークの読み込み ---


def test_汎用の診断フレームワークを読み込める():
    fw = config.load_framework("general")
    assert [item.id for item in fw.hearing_items] == ["H1", "H2", "H3", "H4", "H5", "H6"]
    assert len(fw.viewpoints) == 7
    # 判断のぶれを減らすための「充足の条件」が全項目に書かれている
    assert all(item.sufficient_when for item in fw.hearing_items)


def test_存在しないフレームワークはエラー():
    with pytest.raises(FileNotFoundError):
        config.load_framework("not_exists")


# --- Secrets の読み込み ---


def test_Secretsがなければ環境変数から読む(monkeypatch):
    monkeypatch.setenv("AI_BIC_TEST_SECRET", "from-env")
    assert config.get_secret("AI_BIC_TEST_SECRET") == "from-env"


def test_どこにも設定がなければNone(monkeypatch):
    monkeypatch.delenv("AI_BIC_TEST_SECRET", raising=False)
    assert config.get_secret("AI_BIC_TEST_SECRET") is None


def test_空文字は未設定として扱う(monkeypatch):
    monkeypatch.setenv("AI_BIC_TEST_SECRET", "")
    assert config.get_secret("AI_BIC_TEST_SECRET") is None


# --- データの形（AIの想定外の値をはじけるか） ---


def issue_data(**override) -> dict:
    data = {
        "issue_id": "I1",
        "title": "転記作業の負担",
        "summary": "Excelからの転記に時間がかかる",
        "viewpoint_ids": ["V1"],
        "evidence_type": "事実",
        "evidences": [{"source": "業務情報", "turn_no": None, "file_no": None, "quote": "転記に時間がかかり"}],
        "assumed_cause": "フォーマットが統一されていない",
        "impact": "月末の残業",
        "severity": "高",
        "severity_reason": "月の作業時間が大きい",
        "followup_questions": [],
    }
    data.update(override)
    return data


def test_正しい課題データは受け付ける():
    assert Issue.model_validate(issue_data()).severity == "高"


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("severity", "やや高"),  # 重要度は 高/中/低 のみ
        ("evidence_type", "たぶん事実"),  # 根拠の種別は 事実/推測/要確認 のみ
    ],
)
def test_候補にない値ははじく(field, bad_value):
    with pytest.raises(ValidationError):
        Issue.model_validate(issue_data(**{field: bad_value}))


def test_必須項目が欠けた課題ははじく():
    data = issue_data()
    del data["evidences"]
    with pytest.raises(ValidationError):
        Issue.model_validate(data)


def test_改善の種類は5種類以外をはじく():
    with pytest.raises(ValidationError):
        Proposal.model_validate(
            {
                "title": "RPA導入",
                "category": "RPA",  # 候補にない
                "description": "",
                "target_issue_ids": ["I1"],
                "expected_effect": "",
                "difficulty": "中",
                "risks": [],
                "prerequisites": [],
                "reason": "",
                "why_not_simpler": None,
            }
        )


@pytest.mark.parametrize("model", [QuestionResult, AnalysisResult, ProposalResult])
def test_AIに渡す回答の形_JSONスキーマ_を作れる(model):
    # 構造化出力では、このスキーマがAPIに渡される
    schema = model.model_json_schema()
    assert schema["type"] == "object"
