"""診断項目ごとの「まだ質問してよいか」の判定（R3：同じことを聞き続けない）。

質問を作るAI（Haiku）は、これまでの記録から「どの項目を何回聞いたか」を
自分で数えて守ることが苦手だった（本物のAIでの確認で、3回目の質問を繰り返し選んだ）。
そこで、数えるのはプログラムが行い、結果をAIに渡す・チェックに使う・終了の判断に使う。

進行管理（hearing.py）、AI出力のチェック（output_check.py）、AIへの入力（prompt_builder.py）が
同じ判定を使うよう、ここにまとめる。
"""

from ai_bic.config import MAX_QUESTIONS_PER_ITEM, UNKNOWN_ANSWER_MAX_CHARS, UNKNOWN_ANSWER_WORDS
from ai_bic.models import Framework, HearingState


def question_count(state: HearingState, item_id: str) -> int:
    """その項目をこれまでに何回質問したか。"""
    return sum(1 for turn in state.turns if turn.item_id == item_id)


def is_unknown_answer(answer: str | None) -> bool:
    """「分かりません」のような、情報のない短い回答かどうか。

    長い回答の中に「分からない」が含まれる場合（例：「誰が担当か分からないことが多い」）は、
    業務の問題点を述べている可能性があるため、情報のない回答とはみなさない。
    """
    text = "".join((answer or "").split())
    return len(text) <= UNKNOWN_ANSWER_MAX_CHARS and any(w in text for w in UNKNOWN_ANSWER_WORDS)


def answered_unknown(state: HearingState, item_id: str) -> bool:
    """その項目への質問に、利用者が「分からない」と答えたことがあるか。"""
    return any(turn.item_id == item_id and is_unknown_answer(turn.answer) for turn in state.turns)


def blocked_reason(state: HearingState, item_id: str) -> str | None:
    """その項目をもう質問できない理由。質問できるなら None。"""
    if question_count(state, item_id) >= MAX_QUESTIONS_PER_ITEM:
        return f"質問回数が上限（{MAX_QUESTIONS_PER_ITEM}回）に達した"
    if answered_unknown(state, item_id):
        return "利用者が「分からない」と回答済み"
    return None


def askable_item_ids(
    state: HearingState, framework: Framework, statuses: dict[str, str] | None = None
) -> list[str]:
    """まだ質問してよい項目（「充足」でなく、上限にも達しておらず、「分からない」とも言われていない）。

    statuses は項目ID → 充足状況。省略時はヒアリング記録の最新の状況を使う。
    """
    if statuses is None:
        statuses = {i: s.status for i, s in state.item_status.items()}
    return [
        item.id
        for item in framework.hearing_items
        if statuses.get(item.id, "未確認") != "充足" and blocked_reason(state, item.id) is None
    ]
