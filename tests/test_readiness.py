"""readiness.py と追加確認の進行のテスト（AI-BIC 2.0 ヒアリングの改善）。

AIは呼ばない（偽物のAIクライアントで、決まった判定と質問を返す）。
"""

import pytest

from ai_bic.config import MAX_EXTRA_QUESTIONS, load_framework
from ai_bic.errors import AIError
from ai_bic.hearing import (
    EXTRA_MAX,
    EXTRA_SUFFICIENT,
    add_answer,
    finish_by_user,
    request_extra_question,
    start_extra,
    start_hearing,
)
from ai_bic.item_rules import askable_item_ids
from ai_bic.models import BusinessInfo, HearingTurn, ItemStatus
from ai_bic.prompt_builder import build_question_input
from ai_bic.readiness import (
    assumptions,
    evaluate,
    extra_count,
    extra_targets,
    reason_for,
    reasons,
    should_offer_extra,
)
from tests.fakes import FakeClient, make_question

FW = load_framework("general")
INFO = BusinessInfo(company_name=None, department="営業管理部", task_name="月次売上集計", overview="o", pain_points="p")
ALL = {i: "充足" for i in ["H1", "H2", "H3", "H4", "H5", "H6"]}


def state_with(statuses: dict, asked: dict | None = None):
    """基本のヒアリングが終わった状態。asked：項目ID → これまでの質問回数。"""
    state = start_hearing(FW)
    for item_id, status in statuses.items():
        state.item_status[item_id] = ItemStatus(item_id=item_id, status=status, known_facts="", lacking="")
    no = 0
    for item_id, count in (asked or {}).items():
        for _ in range(count):
            no += 1
            state.turns.append(HearingTurn(turn_no=no, item_id=item_id, question="q", answer="回答です"))
    state.finished_reason = "質問回数の上限に達したため終了しました"
    state.judged_answers = no
    return state


def levels(state):
    return {f.key: f.level for f in evaluate(state)}


# --- 機能ごとの充足度（明確な基準：config.FEATURE_REQUIREMENTS） ---
def test_すべて充足なら3機能とも十分で_追加確認はしない():
    state = state_with(ALL)
    assert levels(state) == {"flow": "十分", "simulation": "十分", "review": "十分"}
    assert not should_offer_extra(state, FW)
    assert assumptions(state) == []


def test_必須の項目が足りない機能は不足_あると良い項目だけなら一部不足():
    state = state_with({**ALL, "H3": "不十分", "H4": "未確認"})
    assert levels(state) == {"flow": "一部不足", "simulation": "不足", "review": "十分"}


def test_あると良い項目だけが足りないなら追加確認はしない():
    """質問を増やすこと自体を目的にしない（最重要方針）。"""
    state = state_with({**ALL, "H1": "未確認", "H4": "不十分"})
    assert not should_offer_extra(state, FW)


def test_追加で聞く項目は影響の大きい順():
    state = state_with({**ALL, "H2": "未確認", "H3": "不十分", "H5": "未確認"})
    assert extra_targets(state, FW) == ["H5", "H3", "H2"]


def test_分からないと答えた項目や上限まで聞いた項目は追加でも聞かない():
    state = state_with({**ALL, "H3": "不十分", "H6": "不十分"}, asked={"H3": 2})
    state.turns.append(HearingTurn(turn_no=3, item_id="H6", question="q", answer="分かりません。"))
    assert extra_targets(state, FW) == []
    assert not should_offer_extra(state, FW)
    assert assumptions(state)  # 聞けなくても、仮定条件は結果に示す


def test_理由はどの機能に何が足りないかを示す():
    state = state_with({**ALL, "H6": "不十分"})
    state.item_status["H6"].lacking = "予算の目安"
    assert reasons(state, FW) == ["改善案レビューに必要な「改善に向けた制約条件」が不十分です（足りないこと：予算の目安）"]
    assert reason_for(state, FW, "H6") == reasons(state, FW)[0]


def test_不足のまま分析した機能ごとに仮定条件を出す():
    state = state_with({**ALL, "H2": "未確認", "H3": "不十分"})
    texts = assumptions(state)
    assert len(texts) == 2
    assert any("業務フロー" in t for t in texts) and any("効果試算" in t for t in texts)


# --- 追加確認の進行 ---
def test_追加確認の間は足りない項目しか質問しない():
    state = state_with({**ALL, "H3": "不十分", "H1": "未確認"})
    start_extra(state, FW)
    assert state.extra_targets == ["H3"]
    assert askable_item_ids(state, FW) == ["H3"]  # H1 はあると良い項目なので聞かない
    assert "追加確認" in build_question_input(INFO, state, FW)


def test_追加の質問に答えて情報がそろえば_上限の前でも終える():
    state = state_with({**ALL, "H3": "不十分"})
    start_extra(state, FW)
    client = FakeClient(questions=[
        make_question({**ALL, "H3": "不十分"}, next_item_id="H3", question="内訳を教えてください"),
        make_question(ALL, next_item_id=None, is_sufficient=True),
    ])
    turn = request_extra_question(client, INFO, state, FW)
    assert turn.extra and turn.item_id == "H3"
    add_answer(state, "転記に8時間です")
    assert request_extra_question(client, INFO, state, FW) is None
    assert state.extra_finished_reason == EXTRA_SUFFICIENT
    assert extra_count(state) == 1


def test_追加確認は最大3問まで():
    state = state_with({**ALL, "H2": "未確認", "H3": "不十分", "H5": "未確認"})
    start_extra(state, FW)
    statuses = {**ALL, "H2": "未確認", "H3": "不十分", "H5": "未確認"}
    client = FakeClient(questions=[make_question(statuses, next_item_id=i) for i in ["H5", "H3", "H2"]])
    for _ in range(MAX_EXTRA_QUESTIONS):
        assert request_extra_question(client, INFO, state, FW) is not None
        add_answer(state, "まだはっきりしません")
    assert request_extra_question(client, INFO, state, FW) is None
    assert state.extra_finished_reason == EXTRA_MAX
    assert client.calls["question"] == 3  # 上限ではAIを呼ばない


def test_回答前に同じ要求が来ても_AIを呼び直さない():
    state = state_with({**ALL, "H3": "不十分"})
    start_extra(state, FW)
    client = FakeClient(questions=[make_question({**ALL, "H3": "不十分"}, next_item_id="H3")])
    first = request_extra_question(client, INFO, state, FW)
    assert request_extra_question(client, INFO, state, FW) is first
    assert client.calls["question"] == 1


def test_AIのエラーでは追加確認の記録が消えない():
    state = state_with({**ALL, "H3": "不十分"})
    start_extra(state, FW)
    with pytest.raises(AIError):
        request_extra_question(FakeClient(questions=[AIError("connection")]), INFO, state, FW)
    assert state.extra_started and state.extra_finished_reason is None


def test_追加確認の途中で分析に進める():
    state = state_with({**ALL, "H3": "不十分"})
    start_extra(state, FW)
    finish_by_user(state)
    assert state.extra_finished_reason is not None
    assert request_extra_question(FakeClient(), INFO, state, FW) is None


def test_基本のヒアリングが終わる前には追加確認を始めない():
    state = start_hearing(FW)
    with pytest.raises(ValueError):
        start_extra(state, FW)



def test_不足したまま聞ける項目がなくなったら_そろったとは表示しない():
    """作業時間（H3）が上限まで聞いて不足のまま、制約条件（H6）だけが追加確認でそろった場合（本物のAIでの確認で発見）。"""
    from ai_bic.hearing import EXTRA_NO_ITEMS

    state = state_with({**ALL, "H3": "不十分", "H6": "不十分"}, asked={"H3": 2})
    start_extra(state, FW)
    assert state.extra_targets == ["H6"]
    client = FakeClient(questions=[
        make_question({**ALL, "H3": "不十分", "H6": "不十分"}, next_item_id="H6"),
        make_question({**ALL, "H3": "不十分"}, next_item_id=None, is_sufficient=True),
    ])
    request_extra_question(client, INFO, state, FW)
    add_answer(state, "表計算ソフトは全員使えます")
    assert request_extra_question(client, INFO, state, FW) is None
    assert state.extra_finished_reason == EXTRA_NO_ITEMS  # 「必要な情報がそろいました」ではない
