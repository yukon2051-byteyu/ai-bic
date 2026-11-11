"""単位と集計期間の引き継ぎ（AI-BIC 3.3）のテスト：診断の数値の「単位／何あたり」と出どころを試行計画まで引き継ぎ、
単位がそろって確かめられた値だけで差と判定を計算する。AIは呼ばない。"""

import json
from datetime import datetime
from io import BytesIO

from openpyxl import load_workbook

from ai_bic import facts
from ai_bic import trial as tr
from ai_bic.config import load_framework
from ai_bic.models import AnalysisResult, DerivedQuantity, Quantity
from ai_bic.project import TrialMetric, TrialPlan, load_project, to_json_bytes
from ai_bic.quantities import basis_text, label
from ai_bic.report import build_report
from ai_bic.views.trial import results_table
from tests.fakes import make_proposal
from tests.test_project import make_project
from tests.test_facts import with_simulations


def q(qid, name, value, unit, per, status="確認済み", quote=None, sim=None, turn_no=2):
    return Quantity(qid=qid, name=name, value=value, unit=unit, per=per, status=status,
                    source="回答" if quote else None, turn_no=turn_no if quote else None, file_no=None, quote=quote,
                    sim_field=sim)


# 入館証のケース（本物のAIの出力の形。何あたりを per に分けて持つ）
BADGE = [
    q("Q1", "閉館前の照合時間", 15, "分", "稼働日", quote="毎日15分ほどかかる", sim="minutes_per_case"),
    q("Q2", "照合を行う日数", 20, "日", "月", quote="月20営業日", sim="cases_per_month"),
    q("Q3", "記入漏れがあった日の追加確認の時間", 10, "分", "発生日", quote="その日は10分ほど余計にかかる"),
    q("Q4", "記入漏れの発生日数", None, "日", "月", status="未集計", quote="記入漏れが月に何日あるかは未集計"),
]
DERIVED = [
    DerivedQuantity(qid="D1", name="通常照合の月間時間", formula="Q1 * Q2 / 60", unit="時間", per="月"),
    DerivedQuantity(qid="D2", name="追加確認の月間時間", formula="Q3 * Q4 / 60", unit="時間", per="月"),
]


def badge(effect_metric="記録漏れがあった日数"):
    project = make_project(proposals=2)
    project.diagnosis.analysis = AnalysisResult(business_summary="", issues=[], no_issue_viewpoints=[],
                                                quantities=BADGE, derived=DERIVED)
    project.diagnosis.proposals.proposals = [
        make_proposal(effect_metric=effect_metric, effect_inputs=["Q4"]),
        make_proposal(effect_metric="月の照合の作業時間", effect_inputs=["D1"]),
    ]
    return project


def decided(project, pid="P2"):
    from ai_bic import simulation as sim
    for p in ("P1", "P2"):
        project.simulations[p], _ = sim.make_inputs({"task_name": "照合", "minutes_per_case": 15, "cases_per_month": 20,
                                                     "reduction_rate": 20,
                                                     "sources": {"minutes_per_case": "ヒアリング", "cases_per_month": "ヒアリング"}})
    facts.decide(project, pid)
    return project


# --- 名前の表示 ---
def test_名前は単位と何あたりを付けて示し_名前の中の何あたりは重ねない():
    assert label("閉館前の照合時間", "分", "稼働日") == "閉館前の照合時間（分／稼働日）"
    assert label("通常の照合の作業時間（1日あたり）", "分", "稼働日") == "通常の照合の作業時間（分／稼働日）"
    assert label("照合の担当人数", "人", None) == "照合の担当人数（人）"
    assert basis_text("分", "") == "分" and basis_text("", "月") == ""


# --- 入館証のケース：15分／稼働日・月5時間・10分／発生日を区別し、発生日数が分かるまで追加の月間時間は未確定 ---
def test_入館証のケースは_15分は稼働日あたり_月5時間_10分は発生日あたりと区別し_追加の月間時間は未確定():
    project = decided(badge())
    plan = tr.new_plan(project)
    time = plan.metrics[0]
    assert tr.metric_label(time) == "閉館前の照合時間（分／稼働日）"
    assert (time.baseline, time.source, time.checked_basis) == (15, "2問目の回答", "分／稼働日")
    assert time.estimate == 12  # 試算（15分・20%削減）と同じ単位なので試算値を入れる
    lines = plan.assumptions
    assert "閉館前の照合時間（分／稼働日）：15分（2問目の回答）" in lines
    assert "記入漏れがあった日の追加確認の時間（分／発生日）：10分（2問目の回答）" in lines
    assert "通常照合の月間時間（時間／月）は5時間（15分 × 20日 ÷ 60 ＝ 5時間）" in lines
    assert "追加確認の月間時間（時間／月）は、記入漏れの発生日数が不明のため未確定" in lines
    assert not any("追加確認の月間時間（時間／月）は" in x and "時間（" in x.split("は", 1)[1] for x in lines)
    assert not any("1件あたり" in x for x in lines)  # 試算の「1件」の名前で言い換えない


def test_試算の値が診断の値と違うときは_試算値を入れず理由を残す():
    project = decided(badge())
    project.decision.simulation = project.decision.simulation.model_copy(update={"minutes_per_case": 30.0})
    time = tr.new_plan(project).metrics[0]
    assert time.baseline == 15 and time.estimate is None and "試算値は入れていません" in time.note


def test_何あたりが分からない古い数値は_単位を確認するまで判定に使わない():
    project = badge()
    project.diagnosis.analysis.quantities = [x.model_copy(update={"per": None}) for x in BADGE]
    decided(project)
    time = tr.new_plan(project).metrics[0]
    assert time.checked_basis is None
    assert tr.evaluate(time.model_copy(update={"actual": 10})).status == tr.UNCONFIRMED


# --- 差と判定：単位と期間がそろい、確かめた値だけ ---
def test_単位を確かめていない値は_入力を残したまま差も判定も出さない():
    m = TrialMetric(name="照合時間", unit="分", per="稼働日", baseline=15, target=12, actual=10)
    r = tr.evaluate(m)
    assert r.status == "単位を確認" and r.diff is None and "単位を確認してください" in r.diff_text
    assert (m.baseline, m.target, m.actual) == (15, 12, 10)  # 入力値は残す
    ok = tr.evaluate(m.model_copy(update={"checked_basis": "分／稼働日"}))
    assert ok.status == "達成" and ok.diff_text == "10 − 15 = -5分／稼働日（-33.3%）"


def test_確かめた後に単位を変えると_再確認が必要と示す():
    m = TrialMetric(name="照合時間", unit="分", per="件", baseline=15, actual=10, target=12, checked_basis="分／稼働日")
    r = tr.evaluate(m)
    assert r.status == "単位を確認" and "分／稼働日 → 分／件" in r.diff_text and "再確認が必要" in r.diff_text
    assert "単位が未設定" in tr.evaluate(TrialMetric(name="記録漏れの日数", actual=2, target=0)).diff_text


def test_必須の項目の単位が未確認なら判定保留にし_その項目を挙げる_参考の項目は判定に使わない():
    plan = TrialPlan(pid="P1", created_at=datetime(2026, 11, 11), metrics=[
        TrialMetric(name="記録漏れがあった日数", unit="日", per="月", target=0, actual=0, checked_basis="日／月"),
        TrialMetric(name="照合時間", unit="分", per="稼働日", target=12, actual=10),
    ])
    g = tr.guide(plan)
    assert g.hold and g.reason == "単位の確認が必要です（照合時間（分／稼働日））"
    plan.metrics[1].required = False  # 参考の項目なら、単位が未確認でも判定を止めない（判定にも使わない）
    assert tr.guide(plan).label == "展開する"


def test_何あたりが違う同じ名前の項目は_別の項目として足す():
    plan = TrialPlan(pid="P1", created_at=datetime(2026, 11, 11),
                     metrics=[TrialMetric(name="記入漏れの日数", unit="日", per="月")])
    assert tr.add_metric_unique(plan, TrialMetric(name="記入漏れの日数", unit="日", per="週"))
    assert not tr.add_metric_unique(plan, TrialMetric(name="記入漏れの日数", unit="日", per="月"))


# --- 保存・旧JSON・Excel：画面と同じ名前・単位・確認状態 ---
def test_旧JSONの測定項目は読み込め_単位を確認してくださいと示して判定に使わない():
    project = decided(with_simulations(), "P2")
    project.trial = tr.new_plan(project)
    raw = json.loads(to_json_bytes(project))
    for m in raw["trial"]["metrics"]:  # 3.2 までの形（per・source・checked_basis がない）
        for k in ("per", "source", "checked_basis"):
            m.pop(k, None)
    raw["trial"]["metrics"][0]["actual"] = 200
    loaded = load_project(json.dumps(raw, ensure_ascii=False).encode("utf-8"))
    m = loaded.trial.metrics[0]
    assert m.actual == 200 and tr.evaluate(m).status == "単位を確認"
    assert "単位を確認してください" in tr.evaluate(m).diff_text
    assert tr.guide(loaded.trial).hold


def test_保存して読み込んでも_単位と出どころと確かめた単位は同じ():
    project = decided(badge())
    project.trial = tr.new_plan(project)
    loaded = load_project(to_json_bytes(project))
    assert loaded.trial.metrics == project.trial.metrics


def test_Excelは画面と同じ名前_単位_確認状態を出す():
    project = decided(badge())
    project.trial = tr.new_plan(project)
    project.trial.metrics.append(TrialMetric(name="照合時間（試し）", unit="分", per="件", actual=3, target=5))
    html = results_table(project.trial.metrics, False)
    assert "閉館前の照合時間（分／稼働日）" in html and "15分（2問目の回答）" in html
    assert "計算しない（単位を確認）" in html and "照合時間（試し）（分／件）" in html
    wb = load_workbook(BytesIO(build_report(project.business_info, project.hearing, project.diagnosis,
                                            load_framework("general"), project.created_at, project=project)))
    ws = wb["試行・判定"]
    rows = [[c.value for c in r] for r in ws.iter_rows()]
    header = next(r for r in rows if r[0] == "測定項目")
    data = {r[0]: dict(zip(header, r)) for r in rows[rows.index(header) + 1:] if r[0]}
    time = data["閉館前の照合時間（分／稼働日）"]
    assert time["単位の確認"] == "確認済み" and time["改善前の出どころ"] == "2問目の回答"
    trial_row = data["照合時間（試し）（分／件）"]
    assert trial_row["状態"] == "単位を確認" and trial_row["差"] == "計算しない（単位を確認）"
    assert "単位を確認してください" in trial_row["単位の確認"]
    summary = "\n".join(str(c.value) for r in ws.iter_rows() for c in r if c.value)
    assert "閉館前の照合時間（分／稼働日）：15分（2問目の回答）" in summary
