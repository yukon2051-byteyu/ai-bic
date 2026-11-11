"""「確認予定」（あとで確かめる）の扱いのテスト（AI-BIC 3.2）。AIは呼ばない。

未回答・分からない・確認予定・確認済みを区別する。確認予定は聞き直さず、確認済みの値として計算にも使わず、
いまの判断に残る制約として示し、確定した案の試行で測る項目に引き継げる。
"""

from ai_bic import facts, overview
from ai_bic import trial as tr
from ai_bic.config import load_framework
from ai_bic.hearing import add_answer, start_hearing
from ai_bic.item_rules import blocked_reason, is_planned_answer
from ai_bic.models import HearingTurn
from tests.test_quantities import badge_project

FW = load_framework("general")


def test_あとで確かめると答えた回答は確認予定と判定し_その項目は聞き直さない():
    assert is_planned_answer("発生日数は未集計なので、1か月記録して確かめたいです")
    assert is_planned_answer("情報システム担当に確認してから答えます")
    assert not is_planned_answer("毎日15分ほどかかります")
    state = start_hearing(FW)
    state.turns.append(HearingTurn(turn_no=1, item_id="H3", question="q"))
    add_answer(state, "発生日数は未集計なので、1か月記録して確かめたいです")
    assert state.turns[0].kind == "確認予定"
    assert "確認予定" in blocked_reason(state, "H3")
    state.turns.append(HearingTurn(turn_no=2, item_id="H5", question="q"))
    add_answer(state, "分かりません。")
    assert state.turns[1].kind == "分からない"


def test_確認予定の値は計算に使わず_いまの判断に残る制約として示す():
    project = badge_project()
    facts.answer_gap(project, "fact:Q4", "記入漏れの月間発生日数", "1か月記録して確かめる", "本人の申告", ["案1"], kind="確認予定")
    k = facts.known_values(project)["Q4"]
    assert (k.value, k.status, k.plan, k.base) == (None, "確認予定", "1か月記録して確かめる", "未集計")
    lines = facts.assumption_lines(project)
    assert "記入漏れの月間発生日数は未集計。確認予定：1か月記録して確かめる" in lines
    assert "追加確認を含む総作業時間は、記入漏れの月間発生日数が不明のため未確定" in lines
    need = next(n for n in facts.needs(project) if n.key == "Q4")
    assert need.status == "確認予定" and need.detail == "1か月記録して確かめる"


def test_ヒアリングで確認予定と答えた回を根拠にする値も_確認予定として示す():
    project = badge_project()
    project.hearing.turns.append(HearingTurn(turn_no=1, item_id="H3", question="q", answer="未集計なので1か月記録して確かめたい",
                                             kind="確認予定"))
    q4 = next(q for q in project.diagnosis.analysis.quantities if q.qid == "Q4")
    q4.source, q4.turn_no = "回答", 1
    assert facts.known_values(project)["Q4"].status == "確認予定"


def test_確認予定の不足情報は重要な不足に数えず_確認予定と表示する():
    project = badge_project()
    ov = overview.build(project.diagnosis, project.hearing, FW, {}, None, facts.needs(project))
    important = lambda o: [g.key for g in o.gaps if g.important and not g.answered and not g.planned]  # noqa: E731
    assert "fact:Q4" in important(ov)  # 発生日数（案1の効果の判断に必要）が未確認
    facts.answer_gap(project, "fact:Q4", "q", "1か月記録して確かめる", "本人の申告", ["案1"], kind="確認予定")
    ov = overview.build(project.diagnosis, project.hearing, FW, project.gap_answers, None, facts.needs(project))
    gap = next(g for g in ov.gaps if g.key == "fact:Q4")
    assert gap.planned == "1か月記録して確かめる" and not gap.answered and not gap.important
    assert "fact:Q4" not in important(ov)  # 主ボタンの判断（重要な不足）に数えない


def test_確認予定の値は_試行計画の測定項目に引き継ぎ_同じ項目は重ねない():
    project = badge_project()
    facts.answer_gap(project, "fact:Q4", "q", "1か月記録して確かめる", "本人の申告", ["案1"], kind="確認予定")
    facts.decide(project, "P1")
    plan = tr.new_plan(project)
    names = [m.name for m in plan.metrics]
    assert "記入漏れの月間発生日数" in names
    metric = next(m for m in plan.metrics if m.name == "記入漏れの月間発生日数")
    assert metric.unit == "日" and metric.note == "確認予定：1か月記録して確かめる" and metric.actual is None
    assert not tr.add_metric_unique(plan, tr.TrialMetric(name="記入漏れの月間発生日数（1か月の記録）"))
    assert len(plan.metrics) == len(names)
