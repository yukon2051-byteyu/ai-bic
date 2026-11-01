"""item_rules.py のテスト（診断項目ごとに、まだ質問してよいかの判定）と、
その判定を使う AIへの入力・チェック・ヒアリングの終了。

本物のAIで起きた問題（同じ項目を3回目も質問しようとして停止した）の再発防止。
"""

import pytest

from ai_bic.config import load_framework
from ai_bic.hearing import FINISH_NO_ITEMS, request_next_question, start_hearing
from ai_bic.item_rules import (
    answered_unknown,
    askable_item_ids,
    blocked_reason,
    is_unknown_answer,
    question_count,
)
from ai_bic.models import BusinessInfo, HearingState, HearingTurn, ItemStatus
from ai_bic.output_check import check_question_result
from ai_bic.prompt_builder import build_question_input
from tests.fakes import FakeClient, make_question

FW = load_framework("general")
ALL = ["H1", "H2", "H3", "H4", "H5", "H6"]
INFO = BusinessInfo(
    company_name=None, department="お客様相談室", task_name="修理依頼の受付",
    overview="電話で修理依頼を受け、業者に手配する。", pain_points="状況をすぐ答えられない。",
)


def state_with(*turns: tuple[str, str], statuses: dict | None = None) -> HearingState:
    """(項目ID, 回答) の並びから、ヒアリングの記録を作る。"""
    state = start_hearing(FW)
    for n, (item_id, answer) in enumerate(turns, start=1):
        state.turns.append(HearingTurn(turn_no=n, item_id=item_id, question="質問", answer=answer))
    for item_id, status in (statuses or {}).items():
        state.item_status[item_id] = ItemStatus(item_id=item_id, status=status, known_facts="")
    return state


# ---------------------------------------------------------------
# 「分からない」という回答の判定
# ---------------------------------------------------------------
@pytest.mark.parametrize(
    "answer",
    ["すみません、その点は分かりません。", "わかりません", "不明です", "把握していません", "知りません。"],
)
def test_情報のない短い回答は分からないとみなす(answer):
    assert is_unknown_answer(answer)


@pytest.mark.parametrize(
    "answer",
    [
        "1日に40件ほどを4人で受けています。",  # 普通の回答
        "担当者が休みだと、誰が対応したのか分からないことがよくあり、お客様を待たせてしまいます。",  # 問題点の説明
        "",
        None,
    ],
)
def test_情報のある回答や長い説明は分からないとみなさない(answer):
    assert not is_unknown_answer(answer)


# ---------------------------------------------------------------
# 質問回数と、もう質問できない理由
# ---------------------------------------------------------------
def test_項目ごとの質問回数を数える():
    state = state_with(("H3", "多いです"), ("H5", "回答"), ("H3", "40件"))
    assert question_count(state, "H3") == 2
    assert question_count(state, "H5") == 1
    assert question_count(state, "H1") == 0


def test_2回質問した項目はもう質問できない():
    state = state_with(("H3", "多いです"), ("H3", "40件ほど"))
    assert "上限" in blocked_reason(state, "H3")


def test_分からないと答えた項目は1回目でももう質問できない():
    state = state_with(("H1", "すみません、その点は分かりません。"))
    assert answered_unknown(state, "H1")
    assert "分からない" in blocked_reason(state, "H1")


def test_まだ質問してよい項目は_充足と上限と分からないを除く():
    state = state_with(
        ("H3", "多いです"), ("H3", "40件ほど"), ("H1", "分かりません"),
        statuses={"H2": "充足", "H4": "不十分"},
    )
    assert askable_item_ids(state, FW) == ["H4", "H5", "H6"]


# ---------------------------------------------------------------
# AIへの入力：プログラムが数えた結果を渡す
# ---------------------------------------------------------------
def test_AIへの入力に質問回数と_もう質問できない印と_質問してよい項目が入る():
    state = state_with(("H3", "多いです"), ("H3", "40件ほど"), ("H1", "分かりません"))
    text = build_question_input(INFO, state, FW)
    assert "これまでの質問回数：2回" in text
    assert "※この項目はもう質問できません（質問回数が上限（2回）に達した）" in text
    assert "※この項目はもう質問できません（利用者が「分からない」と回答済み）" in text
    assert "質問してよい項目：H2、H4、H5、H6" in text


def test_質問してよい項目がなければ_なしと伝える():
    statuses = {i: "充足" for i in ["H1", "H2", "H4", "H5"]}
    state = state_with(("H3", "多い"), ("H3", "40件"), ("H6", "分かりません"), statuses=statuses)
    assert "質問してよい項目：なし" in build_question_input(INFO, state, FW)


# ---------------------------------------------------------------
# チェック：質問できる項目がなければ、質問がなくても違反にしない
# ---------------------------------------------------------------
def test_質問してよい項目がなければ_質問なしでも違反にしない():
    # 本物のAIで止まった状況：残りが上限到達・分からない回答済みの項目だけ
    state = state_with(("H3", "多い"), ("H3", "40件"), ("H6", "分かりません"))
    result = make_question(
        {"H1": "充足", "H2": "充足", "H4": "充足", "H5": "充足", "H3": "不十分", "H6": "未確認"},
        next_item_id=None, is_sufficient=False,
    )
    assert check_question_result(result, state, FW) == []


def test_質問してよい項目が残っていれば_質問なしは違反のまま():
    state = state_with(("H3", "多い"), ("H3", "40件"))
    result = make_question({"H3": "不十分"}, next_item_id=None, is_sufficient=False)
    assert check_question_result(result, state, FW)


# ---------------------------------------------------------------
# ヒアリングの終了：質問できる項目がなくなったらエラーにせず終わる
# ---------------------------------------------------------------
def test_質問してよい項目がなくなったら_ヒアリングを終了する():
    state = state_with(("H3", "多い"), ("H3", "40件"), ("H6", "分かりません"))
    result = make_question(
        {"H1": "充足", "H2": "充足", "H4": "充足", "H5": "充足", "H3": "不十分", "H6": "不十分"},
        next_item_id=None, is_sufficient=False,
    )
    client = FakeClient(questions=[result])

    assert request_next_question(client, INFO, state, FW) is None
    assert state.finished_reason == FINISH_NO_ITEMS
