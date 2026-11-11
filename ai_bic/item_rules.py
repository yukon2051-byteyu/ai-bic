"""診断項目ごとの「まだ質問してよいか」の判定（R3：同じことを聞き続けない）。

質問を作るAI（Haiku）は、これまでの記録から「どの項目を何回聞いたか」を
自分で数えて守ることが苦手だった（本物のAIでの確認で、3回目の質問を繰り返し選んだ）。
そこで、数えるのはプログラムが行い、結果をAIに渡す・チェックに使う・終了の判断に使う。

進行管理（hearing.py）、AI出力のチェック（output_check.py）、AIへの入力（prompt_builder.py）が
同じ判定を使うよう、ここにまとめる。
"""

from ai_bic.config import (
    MAX_QUESTIONS_PER_ITEM,
    PLANNED_ANSWER_PHRASES,
    UNKNOWN_ANSWER_FILLERS,
    UNKNOWN_ANSWER_MAX_REST_CHARS,
    UNKNOWN_ANSWER_WORDS,
)
from ai_bic.models import Framework, HearingState


def question_count(state: HearingState, item_id: str) -> int:
    """その項目をこれまでに何回質問したか。"""
    return sum(1 for turn in state.turns if turn.item_id == item_id)


def is_unknown_answer(answer: str | None) -> bool:
    """「分かりません」のような、情報のない回答かどうか。

    「分からない」という言葉と、「すみません」「その点は」などの決まり文句を取り除いたあと、
    中身がほとんど残らない回答だけを「情報のない回答」とみなす。
    「担当者が休みだと分からないこともあります」のように、「分からない」を含んでいても
    業務の問題点を述べている回答は、情報のある回答として扱う（誤判定の再発防止）。
    """
    text = "".join((answer or "").split())
    if not any(w in text for w in UNKNOWN_ANSWER_WORDS):
        return False
    rest = text
    for word in (*UNKNOWN_ANSWER_WORDS, *UNKNOWN_ANSWER_FILLERS):
        rest = rest.replace(word, "")
    return len(rest) <= UNKNOWN_ANSWER_MAX_REST_CHARS


def is_planned_answer(answer: str | None) -> bool:
    """あとで確かめる予定を述べた回答か（例：未集計なので1か月記録して確かめたい）。決まった言い回しで判定する。"""
    text = "".join((answer or "").split())
    return any(p in text for p in PLANNED_ANSWER_PHRASES)


def planned_turn(state: HearingState, item_id: str):
    """その項目に「確認予定」と答えた回（なければ None）。"""
    return next((t for t in state.turns if t.item_id == item_id and t.answer is not None
                 and (t.kind == "確認予定" or is_planned_answer(t.answer))), None)


def answered_unknown(state: HearingState, item_id: str) -> bool:
    """その項目への質問に、利用者が「分からない」と答えたことがあるか。"""
    return any(turn.item_id == item_id and is_unknown_answer(turn.answer) for turn in state.turns)


def blocked_reason(state: HearingState, item_id: str) -> str | None:
    """その項目をもう質問できない理由。質問できるなら None。"""
    if question_count(state, item_id) >= MAX_QUESTIONS_PER_ITEM:
        return f"質問回数が上限（{MAX_QUESTIONS_PER_ITEM}回）に達した"
    if answered_unknown(state, item_id):
        return "利用者が「分からない」と回答済み"
    if planned_turn(state, item_id) is not None:
        return "利用者が「確認予定（あとで確かめる）」と回答済み"
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
        # 追加確認の間は、機能に必要な情報が足りない項目（readiness.extra_targets）だけを聞く
        and (not state.extra_started or item.id in state.extra_targets)
    ]
