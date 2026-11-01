"""入力チェック（F01・ヒアリングの回答）。

AIを呼ぶ前にここで確認し、入力ミスのままAIを呼んで料金がかかるのを防ぐ（K04）。
エラーは項目ごとに返し、画面では該当する入力欄の近くに表示する。
"""

from ai_bic.config import ANSWER_MAX_CHARS, FIELD_LIMITS
from ai_bic.models import BusinessInfo

# 画面に表示する項目名
FIELD_LABELS = {
    "company_name": "企業名",
    "department": "部署名",
    "task_name": "業務名",
    "overview": "業務概要",
    "pain_points": "現在困っていること",
}

# 必須項目（企業名だけが任意）
REQUIRED_FIELDS = ["department", "task_name", "overview", "pain_points"]


def validate_business_info(raw: dict) -> tuple[BusinessInfo | None, dict[str, str]]:
    """業務情報を確認する。

    戻り値：(問題なければ BusinessInfo／問題があれば None, 項目名 → エラーメッセージ)
    エラーは最初の1つで止めず、すべての項目をまとめて返す（利用者が一度で直せるように）。
    """
    # 前後の空白（全角スペース・改行を含む）を取り除く。None は空文字として扱う
    cleaned = {field: str(raw.get(field) or "").strip() for field in FIELD_LIMITS}

    errors: dict[str, str] = {}
    for field, limit in FIELD_LIMITS.items():
        label = FIELD_LABELS[field]
        value = cleaned[field]
        if field in REQUIRED_FIELDS and not value:
            errors[field] = f"{label}を入力してください"
        elif len(value) > limit:
            errors[field] = f"{label}は{limit}文字以内で入力してください（現在{len(value)}文字）"

    if errors:
        return None, errors

    info = BusinessInfo(
        company_name=cleaned["company_name"] or None,  # 任意項目の未入力は None
        department=cleaned["department"],
        task_name=cleaned["task_name"],
        overview=cleaned["overview"],
        pain_points=cleaned["pain_points"],
    )
    return info, {}


def validate_answer(text: str | None) -> str | None:
    """ヒアリングの回答を確認する。問題があればエラーメッセージ、なければ None を返す。"""
    value = (text or "").strip()
    if not value:
        return "回答を入力してください"
    if len(value) > ANSWER_MAX_CHARS:
        return f"回答は{ANSWER_MAX_CHARS}文字以内で入力してください（現在{len(value)}文字）"
    return None
