"""情報の充足度の判定（AI-BIC 2.0 ヒアリングの改善）。

AI-BIC 2.0 の3機能（業務フロー・改善効果の試算・改善案レビュー）それぞれに、必要な情報がそろっているかを
プログラムで判定する。AIには判定させない。

- 各診断項目（H1〜H6）が「充足」かどうかは、これまでどおり設定ファイルの「充足の条件」でAIが判定した結果を使う
- どの機能にどの項目が必要かは、config.FEATURE_REQUIREMENTS の対応表で決める（明確な評価基準）
- 必須の項目が足りない機能があれば、追加確認（最大 config.MAX_EXTRA_QUESTIONS 問）を提案する。
  追加確認は強制しない（利用者が「このまま分析する」を選べる）
"""

from dataclasses import dataclass, field
from typing import Literal

from ai_bic import config
from ai_bic.item_rules import blocked_reason
from ai_bic.models import Framework, HearingState

Level = Literal["十分", "一部不足", "不足"]
LEVEL_MARK = {"十分": "✓ 十分", "一部不足": "△ 一部不足", "不足": "✕ 不足"}


@dataclass
class FeatureReadiness:
    key: str
    name: str
    level: Level
    missing_required: list[str] = field(default_factory=list)  # 「充足」でない必須の項目
    missing_helpful: list[str] = field(default_factory=list)


def _status(state: HearingState, item_id: str) -> str:
    status = state.item_status.get(item_id)
    return status.status if status else "未確認"


def evaluate(state: HearingState) -> list[FeatureReadiness]:
    """3機能それぞれの充足度。"""
    result = []
    for key, spec in config.FEATURE_REQUIREMENTS.items():
        required = [i for i in spec["required"] if _status(state, i) != "充足"]
        helpful = [i for i in spec["helpful"] if _status(state, i) != "充足"]
        level = "不足" if required else ("一部不足" if helpful else "十分")
        result.append(FeatureReadiness(key, spec["name"], level, required, helpful))
    return result


def extra_targets(state: HearingState, framework: Framework) -> list[str]:
    """追加確認で聞く項目（優先度の高い順）。必須の項目が足りない機能の、まだ質問できる項目だけ。

    「あると精度が上がる」だけの項目のためには追加確認をしない（質問を最小限にする）。
    優先度は、改善案の判断への影響が大きい順（question_system.md の順と同じ）。
    """
    needed = {i for f in evaluate(state) for i in f.missing_required}
    order = [item.id for item in framework.hearing_items]
    priority = ["H5", "H3", "H6", "H2", "H4", "H1"]
    ranked = sorted(needed, key=lambda i: priority.index(i) if i in priority else len(priority) + order.index(i))
    return [i for i in ranked if blocked_reason(state, i) is None]


def extra_count(state: HearingState) -> int:
    return sum(1 for turn in state.turns if turn.extra)


def should_offer_extra(state: HearingState, framework: Framework) -> bool:
    """追加確認を提案するか（基本の質問が終わったあと、1回だけ）。"""
    return not state.extra_started and bool(extra_targets(state, framework))


def reason_for(state: HearingState, framework: Framework, item_id: str) -> str:
    """その項目を追加で確認する理由（どの機能のためか）。追加の質問の画面にも表示する。"""
    names = {item.id: item.name for item in framework.hearing_items}
    features = [spec["name"] for spec in config.FEATURE_REQUIREMENTS.values() if item_id in spec["required"]]
    status = state.item_status.get(item_id)
    lacking = f"（足りないこと：{status.lacking}）" if status and status.lacking else ""
    return f"{'・'.join(features)}に必要な「{names[item_id]}」が{_status(state, item_id)}です{lacking}"


def reasons(state: HearingState, framework: Framework) -> list[str]:
    """追加確認が必要な理由（追加で聞く項目ごと）。追加確認を提案する画面に表示する。"""
    return [reason_for(state, framework, item_id) for item_id in extra_targets(state, framework)]


def assumptions(state: HearingState) -> list[str]:
    """情報が足りないまま分析した場合に、結果に示す仮定条件（必須の項目が足りない機能ごと）。"""
    return [config.FEATURE_ASSUMPTIONS[f.key] for f in evaluate(state) if f.level == "不足"]
