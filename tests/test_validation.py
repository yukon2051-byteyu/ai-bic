"""validation.py のテスト（F01 業務情報登録・ヒアリングの回答）。"""

import pytest

from ai_bic.config import ANSWER_MAX_CHARS, FIELD_LIMITS
from ai_bic.validation import validate_answer, validate_business_info


def valid_input() -> dict:
    """正しく入力された業務情報（架空のデータ）。"""
    return {
        "company_name": "株式会社サンプル商事",
        "department": "営業管理部",
        "task_name": "月次売上集計",
        "overview": "各営業所から届くExcelを1つにまとめ、月次の売上報告書を作成する。",
        "pain_points": "転記に時間がかかり、毎月どこかで数字が合わない。",
    }


# --- 正常系 ---


def test_正しい入力は登録できる():
    info, errors = validate_business_info(valid_input())
    assert errors == {}
    assert info.department == "営業管理部"
    assert info.company_name == "株式会社サンプル商事"


def test_企業名は空欄でも登録でき_Noneになる():
    data = valid_input()
    data["company_name"] = ""
    info, errors = validate_business_info(data)
    assert errors == {}
    assert info.company_name is None


def test_前後の空白は取り除いて保存する():
    data = valid_input()
    data["department"] = "　 営業管理部 \n"  # 全角スペース・半角スペース・改行
    info, _ = validate_business_info(data)
    assert info.department == "営業管理部"


def test_文字数がちょうど上限なら登録できる():
    data = valid_input()
    data["overview"] = "あ" * FIELD_LIMITS["overview"]
    info, errors = validate_business_info(data)
    assert errors == {}
    assert info is not None


# --- 異常系 ---


@pytest.mark.parametrize(
    ("field", "label"),
    [
        ("department", "部署名"),
        ("task_name", "業務名"),
        ("overview", "業務概要"),
        ("pain_points", "現在困っていること"),
    ],
)
def test_必須項目が未入力ならエラー(field, label):
    data = valid_input()
    data[field] = ""
    info, errors = validate_business_info(data)
    assert info is None
    assert errors == {field: f"{label}を入力してください"}


def test_空白だけの入力は未入力として扱う():
    data = valid_input()
    data["task_name"] = " 　\n"
    info, errors = validate_business_info(data)
    assert info is None
    assert "task_name" in errors


def test_項目が存在しない_またはNoneでも落ちずにエラーを返す():
    info, errors = validate_business_info({"department": None})
    assert info is None
    assert set(errors) == {"department", "task_name", "overview", "pain_points"}


def test_文字数の上限を超えるとエラーで現在の文字数も示す():
    data = valid_input()
    data["overview"] = "あ" * (FIELD_LIMITS["overview"] + 1)
    info, errors = validate_business_info(data)
    assert info is None
    assert errors["overview"] == "業務概要は1000文字以内で入力してください（現在1001文字）"


def test_任意項目でも文字数の上限は確認する():
    data = valid_input()
    data["company_name"] = "あ" * (FIELD_LIMITS["company_name"] + 1)
    _, errors = validate_business_info(data)
    assert "company_name" in errors


def test_複数のエラーはまとめて返す():
    data = valid_input()
    data["department"] = ""
    data["pain_points"] = ""
    _, errors = validate_business_info(data)
    assert set(errors) == {"department", "pain_points"}


# --- ヒアリングの回答 ---


def test_回答が正しければエラーなし():
    assert validate_answer("毎月3営業日目に2人で半日ほど") is None


@pytest.mark.parametrize("text", ["", "   ", "　", None])
def test_回答が空ならエラー(text):
    assert validate_answer(text) == "回答を入力してください"


def test_回答が上限を超えるとエラー():
    assert "文字以内" in validate_answer("あ" * (ANSWER_MAX_CHARS + 1))
    assert validate_answer("あ" * ANSWER_MAX_CHARS) is None
