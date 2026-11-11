"""改善案ごとの測定項目（AI-BIC 3.3 段階2）のテスト：案に合う項目だけを入れ、確認予定の値は重ねずに再利用し、
成否を左右する例外の確認だけを必須にする。必須が欠けたら、その項目を挙げて判定保留にする。AIは呼ばない。"""

from ai_bic import facts
from ai_bic import trial as tr
from ai_bic.demo_client import load_scenarios
from ai_bic.models import Proposal, TrialMetricPlan
from tests.fakes import make_proposal
from tests.test_units import badge, decided


def c(name, kind="数値", unit=None, per=None, required=True, purpose="確かめる", cond="", target=None, ref=None):
    return TrialMetricPlan(name=name, kind=kind, unit=unit, per=per, required=required, purpose=purpose,
                           pass_condition=cond, target=target, quantity_ref=ref)


# 本物のAIが入館証の「受け取ったその場で記入するルール」の案に書く形
BADGE_RULE = [
    c("記録漏れがあった日数", unit="日", per="月", purpose="ルールで記録漏れが減るかを確かめる", cond="0日", target=0, ref="Q4"),
    c("混雑時でも未記入の入館証を専用トレイで区別できたか", kind="確認", purpose="混雑時もルールが守れるかを確かめる",
      cond="すべて区別できる"),
]


def badge_rule_project(planned=True):
    project = badge()
    project.diagnosis.proposals.proposals[0] = make_proposal(effect_metric="記録漏れがあった日数", effect_inputs=["Q4"],
                                                             trial_metrics=BADGE_RULE)
    if planned:  # 不足情報で、発生日数を「1か月記録して確かめる（確認予定）」にした
        facts.answer_gap(project, "fact:Q4", "q", "1か月記録して確かめる", "本人の申告", ["案1"], kind="確認予定")
    return decided(project, "P1")


# --- 入館証のケース ---
def test_入館証のルールの案は_案に合う2項目だけで_発生日数の項目は1つだけ():
    plan = tr.new_plan(badge_rule_project())
    labels = [tr.metric_label(m) for m in plan.metrics]
    assert labels == ["記録漏れがあった日数（日／月）", "混雑時でも未記入の入館証を専用トレイで区別できたか"]
    days = plan.metrics[0]
    # 確認予定の「記入漏れの発生日数（Q4）」は同じ数値なので、新しく足さずに内容を書き足す
    assert days.ref == "Q4" and "確認予定：1か月記録して確かめる" in days.note
    assert days.baseline is None and "改善前の値は" in days.note  # 未集計の値を推測で入れない
    assert days.purpose == "ルールで記録漏れが減るかを確かめる" and days.checked_basis == "日／月"
    # 一般的な項目（誤りの件数・例外の処理・作業時間）が判定を止めない
    assert not any("例外の処理" in x or "誤り・差異" in x for x in labels)


def test_必須の項目が欠けていると_その項目を挙げて判定保留にする():
    plan = tr.new_plan(badge_rule_project())
    g = tr.guide(plan)
    assert g.hold and "記録漏れがあった日数（日／月）" in g.reason and "専用トレイで区別できたか" in g.reason
    plan.metrics[0].actual = 0
    plan.metrics[1].check_result = "成功"
    assert tr.guide(plan).label == "展開する"


def test_確認予定が先に引き継がれていても_同じ数値を測る候補は1つにまとめる():
    plan = tr.new_plan(badge_rule_project())
    assert not tr.add_metric_unique(plan, tr.TrialMetric(name="返却時刻の記入漏れの月間発生日数", unit="日", per="月", ref="Q4"))
    assert sum(1 for m in plan.metrics if m.ref == "Q4") == 1


def test_一覧にない番号や_単位の違う番号の改善前の値は使わない():
    project = badge()
    time = tr.time_metric(project, None)
    wrong = tr.from_candidate(project, c("照合時間", unit="分", per="月", ref="Q1"), time)
    assert wrong.baseline is None and "単位か何あたりが違う" in wrong.note
    missing = tr.from_candidate(project, c("照合時間", unit="分", per="稼働日", ref="Q9"), time)
    assert missing.ref is None and missing.baseline is None
    ok = tr.from_candidate(project, c("閉館前の照合時間", unit="分", per="稼働日", ref="Q1"), time)
    assert (ok.baseline, ok.source, ok.checked_basis) == (15, "2問目の回答", "分／稼働日")
    no_unit = tr.from_candidate(project, c("照合時間", ref="Q1"), time)  # 単位を書かなかった候補は一覧の単位を使う
    assert tr.basis_of(no_unit) == "分／稼働日" and no_unit.baseline == 15


def test_単位か何あたりのない数値の候補は_単位を確かめるまで判定に使わない():
    m = tr.from_candidate(None, c("記録漏れの日数", unit="日", target=0), tr.time_metric(None, None))
    m.actual = 0
    assert tr.evaluate(m).status == "単位を確認"


# --- 製造部デモ ---
def test_製造部デモの案2は_形式の違うCSVを取り込めるかを必須の確認にできる():
    scenario = next(s for s in load_scenarios() if s["id"] == "production_report")
    from tests.test_trial import decided as demo_decided

    project = demo_decided("P2")
    project.decision.proposal = Proposal(**scenario["proposals"]["proposals"][1])
    plan = tr.new_plan(project)
    check = next(m for m in plan.metrics if m.kind == "確認")
    assert check.name == "日付の書き方の違いを取り込めるか" and check.required
    assert "成否を左右する" in check.purpose
    assert [tr.metric_label(m) for m in plan.metrics if m.kind == "数値"] == [
        "1台分の転記・照合の時間（分／台（月次））", "手作業の集計との数量差（件／台（10月分））"]
    sample = tr.demo_plan(project, scenario["trial_sample"])  # サンプルにも「確かめること」を引き継ぐ
    assert all(m.purpose for m in sample.metrics)
    assert tr.guide(sample).label == "修正して再試行"


# --- 候補がない古いデータ ---
def test_候補がない古い案は_案の指標1件と参考の作業時間だけ():
    project = badge()
    project.diagnosis.proposals.proposals[0] = make_proposal(effect_metric="記録漏れがあった日数", effect_inputs=["Q4"])
    plan = tr.new_plan(decided(project, "P1"))
    assert [m.name for m in plan.metrics][:2] == ["記録漏れがあった日数", "閉館前の照合時間"]
    assert plan.metrics[0].required and not plan.metrics[1].required
    assert not any(m.kind == "確認" for m in plan.metrics)
