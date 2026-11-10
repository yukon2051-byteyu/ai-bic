"""facts.py のテスト（不足情報の確認で得た情報の記録と、関係する結果だけの更新。AI-BIC 3.0 第3段階）。AIは呼ばない。"""

import pytest

from ai_bic import facts
from ai_bic import project as pj
from ai_bic import review as rv
from ai_bic import simulation as sim
from tests.test_project import make_project


def with_simulations(mode="ai"):
    project = make_project(mode, proposals=3, **({"demo_scenario_id": "sales_report"} if mode == "demo" else {}))
    for pid in pj.proposal_ids(project):
        project.simulations[pid] = sim.SimulationInputs(
            task_name="集計", minutes_per_case=300.0, cases_per_month=4.0, hourly_cost=3000.0, reduction_rate=20.0,
            sources={"minutes_per_case": "デモ値", "cases_per_month": "デモ値", "hourly_cost": "デモ値",
                     "reduction_rate": "デモ値"})
        pj.mark_updated(project, "simulation", pid)
    return project


# --- 計算（指示書 6章 3） ---
def test_月20時間のうち転記12時間なら_割合は60パーセントで式も示す():
    calc = facts.ratio(20, 12)
    assert calc.value == 60
    assert calc.formula == "12 ÷ 20 × 100 = 60%"


@pytest.mark.parametrize(("total", "part", "message"), [
    (None, 12, "要確認：合計が未確認"),
    (20, None, "要確認：内訳が未確認"),
    (0, 0, "分母が0"),
    (20, 25, "内訳（25）が合計（20）を上回っています"),
])
def test_片方が空欄_分母が0_内訳が合計を超えるときは計算しない(total, part, message):
    calc = facts.ratio(total, part)
    assert calc.value is None and calc.formula == "" and message in calc.message


def test_1件あたりの時間は_月の時間と件数から計算し_件数が0なら計算しない():
    assert facts.minutes_per_case(20, 4).value == 300
    assert facts.minutes_per_case(20, 4).formula == "20時間 × 60 ÷ 4件 = 300分"
    assert facts.minutes_per_case(20, 0).value is None and "分母が0" in facts.minutes_per_case(20, 0).message
    assert facts.minutes_per_case(None, 4).value is None


# --- 出どころと食い違い ---
def test_申告と資料が同じ値なら資料で確かめたことを示し_食い違えば使わずに両方を示す():
    project = make_project()
    facts.record_fact(project, "total_hours", 20, "本人の申告")
    facts.record_fact(project, "total_hours", 20, "添付資料", "実績表")
    state = facts.state_of(project, "total_hours")
    assert (state.value, state.source, state.conflict) == (20, "添付資料", False)
    facts.record_fact(project, "total_hours", 24, "添付資料", "実績表")
    state = facts.state_of(project, "total_hours")
    assert state.value is None and state.conflict
    assert state.conflict_text == "本人の申告：20、添付資料：24（実績表）"
    assert facts.numbers_summary(project) is None  # 食い違いがあれば「要確認」のまま


def test_同じ出どころの値は置き換え_負の値は受け付けない():
    project = make_project()
    facts.record_fact(project, "hourly_cost", 3000, "本人の申告")
    assert facts.record_fact(project, "hourly_cost", 3500, "本人の申告") is True
    assert [e.value for e in project.facts["hourly_cost"]] == [3500]
    with pytest.raises(ValueError):
        facts.record_fact(project, "hourly_cost", -1, "本人の申告")


# --- 試算への反映（指示書 6章 4） ---
def test_確認した数値は_試算の元の値を更新し_その試算だけ計算し直す():
    project = with_simulations()
    before = {pid: pj.version(project, "simulation", pid) for pid in pj.proposal_ids(project)}
    facts.record_fact(project, "total_hours", 24, "本人の申告")
    facts.record_fact(project, "cases_per_month", 4, "本人の申告")
    changes = facts.apply_to_simulations(project)
    assert {(c.pid, c.field) for c in changes} == {(pid, "minutes_per_case") for pid in ("P1", "P2", "P3")} | {
        (pid, "cases_per_month") for pid in ("P1", "P2", "P3")}
    p1 = project.simulations["P1"]
    assert p1.minutes_per_case == 360 and p1.source_of("minutes_per_case") == "本人の申告"
    assert p1.hourly_cost == 3000 and p1.source_of("hourly_cost") == "デモ値"  # 確認していない値は変えない
    assert all(pj.version(project, "simulation", pid) == before[pid] + 1 for pid in before)
    assert facts.apply_to_simulations(project) == []  # もう一度反映しても、何も変わらない（版も上げない）


def test_試算で手入力した値は残し_元の値が変わったことだけを記録する():
    project = with_simulations()
    project.simulations["P2"] = project.simulations["P2"].model_copy(update={
        "minutes_per_case": 200.0, "sources": {**project.simulations["P2"].sources, "minutes_per_case": "ユーザー入力"}})
    version = pj.version(project, "simulation", "P2")
    facts.record_fact(project, "total_hours", 24, "本人の申告")
    facts.record_fact(project, "cases_per_month", 4, "本人の申告")
    changes = facts.apply_to_simulations(project)
    kept = [c for c in changes if not c.applied]
    assert [(c.pid, c.field, c.value) for c in kept] == [("P2", "minutes_per_case", 360)]
    assert project.simulations["P2"].minutes_per_case == 200  # 手入力の値は残る
    assert project.basis_changes == {"P2": {"minutes_per_case": 360}}
    assert pj.version(project, "simulation", "P2") == version + 1  # 月の件数は反映したので計算し直す
    facts.restore_basis(project, "P2", "minutes_per_case")
    assert project.simulations["P2"].minutes_per_case == 360 and project.basis_changes == {}


def test_割合だけを確認しても_試算の元の値は変えない():
    project = with_simulations()
    facts.record_fact(project, "total_hours", 20, "本人の申告")
    facts.record_fact(project, "part_hours", 12, "本人の申告")
    assert facts.apply_to_simulations(project) == []  # 件数が分からないと1件あたりの時間は決まらない
    assert facts.numbers_summary(project) == "本人の申告：月の作業時間の合計 20時間、うち、最も時間がかかる作業の時間 12時間、割合 60%"


# --- 文章の回答と、レビュー・確定した案への影響 ---
def test_回答は影響する改善案の前提だけを更新し_その案の過去のレビューは再レビューが必要になる():
    project = with_simulations()
    for pid in ("P1", "P2"):
        project.reviews[pid] = make_review()
        pj.mark_updated(project, "review", pid)
    pids = facts.answer_gap(project, "issue:I2", "誤りは月に何件？", " 月3件です ", "本人の申告", ["課題「誤り」", "案2"])
    assert pids == ["P2"]
    assert project.gap_answers["issue:I2"].answer == "月3件です"
    assert pj.status(project, "review", "P2") == "再レビューが必要"
    assert "前提が変更されました" in pj.outdated_reasons(project, "review", "P2")
    assert pj.status(project, "review", "P1") == "完了"  # 関係のない案は変えない
    with pytest.raises(ValueError):
        facts.answer_gap(project, "issue:I2", "q", "  ", "本人の申告", [])


def test_案の名前がない回答は_すべての案に影響する():
    project = with_simulations()
    assert facts.affected_pids(project, ["改善効果の試算"]) == ["P1", "P2", "P3"]


def test_確定した案は確定時点の内容を残し_前提や試算が変わると再確認を求める():
    project = with_simulations()
    facts.decide(project, "P1")
    title = project.decision.proposal.title
    assert project.selected_proposal == "P1" and facts.decision_changes(project) == []
    facts.answer_gap(project, "item:H6", "制約は？", "社外に出せない", "本人の申告", ["改善案レビュー"])
    facts.record_fact(project, "total_hours", 24, "本人の申告")
    facts.record_fact(project, "cases_per_month", 4, "本人の申告")
    facts.apply_to_simulations(project)
    assert facts.decision_changes(project) == ["前提", "試算"]
    assert project.decision.proposal.title == title and project.decision.simulation.minutes_per_case == 300
    facts.decide(project, "P1")  # 今の内容で確定し直す
    assert facts.decision_changes(project) == []


def test_回答は作り直しに渡せ_根拠として認める():
    project = with_simulations()
    facts.answer_gap(project, "issue:I2", "誤りは月に何件？", "月3件です", "本人の申告", ["案2"])
    found = rv.answer_findings(project, "P2")
    assert len(found) == 1 and found[0].evidence == "月3件です"
    assert rv.answer_findings(project, "P1") == []
    assert "月3件です" in rv.evidence_texts(project, "P2", "")


def test_版1で保存したファイルも_新しい項目を空にして読み込める():
    import json

    project = with_simulations()
    raw = json.loads(pj.to_json_bytes(project))
    for name in ("decision", "facts", "gap_answers", "basis_changes", "partial_hearing"):
        raw.pop(name)
    raw["schema_version"] = 1
    loaded = pj.load_project(json.dumps(raw).encode("utf-8"))
    assert loaded.schema_version == 2
    assert (loaded.facts, loaded.gap_answers, loaded.decision, loaded.partial_hearing) == ({}, {}, None, None)


def make_review():
    from datetime import datetime

    from ai_bic.models import ReviewResult
    from ai_bic.project import ReviewRecord

    result = ReviewResult(verdict="推奨", summary="s", findings=[], strengths=[], missing_info=[], alternatives=[],
                          next_checks=[])
    return ReviewRecord(result=result, ai_verdict="推奨", verdict="推奨", created_at=datetime(2026, 11, 11))
