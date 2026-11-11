"""数値の一覧（AI-BIC 3.1）のテスト：式の確かめと計算、入力との照合、確認が必要な数値の選び方。AIは呼ばない。"""

import pytest

from ai_bic import facts
from ai_bic.models import AnalysisResult, BusinessInfo, DerivedQuantity, HearingState, HearingTurn, Quantity
from ai_bic.output_check import verify_quantities
from ai_bic.quantities import evaluate, formula_problem
from tests.fakes import make_proposal
from tests.test_project import make_project

INFO = BusinessInfo(company_name=None, department="総務部", task_name="入館証の貸出台帳の照合",
                    overview="毎日夕方に担当者1人が台帳を照合しており、毎日15分ほどかかる（月20営業日）。",
                    pain_points="記入漏れがあると、その日は10分ほど余計にかかる。記入漏れが月に何日あるかは集計していない。")


def q(qid, name, value, unit, status, quote=None, source="業務情報", sim=None):
    return Quantity(qid=qid, name=name, value=value, unit=unit, status=status, source=source if quote else None,
                    turn_no=None, file_no=None, quote=quote, sim_field=sim)


BADGE = [
    q("Q1", "通常の照合の作業時間（1日あたり）", 15, "分", "確認済み", "毎日15分ほどかかる", sim="minutes_per_case"),
    q("Q2", "月の営業日数", 20, "日", "確認済み", "月20営業日", sim="cases_per_month"),
    q("Q3", "記入漏れの日の追加確認の時間（1日あたり）", 10, "分", "確認済み", "その日は10分ほど余計にかかる"),
    q("Q4", "記入漏れの月間発生日数", None, "日", "未集計", "記入漏れが月に何日あるかは集計していない"),
]
DERIVED = [
    DerivedQuantity(qid="D1", name="通常の照合の月間時間", formula="Q1 * Q2 / 60", unit="時間"),
    DerivedQuantity(qid="D2", name="追加確認を含む総作業時間", formula="(Q1 * Q2 + Q3 * Q4) / 60", unit="時間"),
]


def badge_project():
    project = make_project(proposals=2)
    project.diagnosis.analysis = AnalysisResult(business_summary="", issues=[], no_issue_viewpoints=[],
                                                quantities=BADGE, derived=DERIVED)
    project.diagnosis.proposals.proposals = [
        make_proposal(effect_metric="月の記入漏れの日数", effect_inputs=["Q4"]),
        make_proposal(effect_metric="月の照合の時間", effect_inputs=["D1"]),
    ]
    return project


# --- 式 ---
def test_式は番号と数と四則演算だけを受け付ける():
    assert formula_problem("(Q1 * Q2 + Q3) / 60", {"Q1", "Q2", "Q3"}) is None
    assert "存在しない番号" in formula_problem("Q1 * Q9", {"Q1"})
    assert "使えない記号" in formula_problem("__import__('os')", set())
    assert "式として読めません" in formula_problem("Q1 *", {"Q1"})


def test_計算は分かっている値だけで行い_足りない値と0で割る場合は計算しない():
    calc = evaluate("Q1 * Q2 / 60", "時間", {"Q1": 15, "Q2": 20}, {"Q1": "分", "Q2": "日"})
    assert calc.value == 5 and calc.text == "15分 × 20日 ÷ 60 ＝ 5時間"
    assert evaluate("Q1 + Q2", "", {"Q1": 1, "Q2": None}, {}).missing == ["Q2"]
    assert "分母が0" in evaluate("Q1 / Q2", "", {"Q1": 1, "Q2": 0}, {}).error


# --- 入館証のケース（指示書 8.6）：通常の照合の月5時間と、発生日数が未集計の追加確認を区別する ---
def test_通常の照合は月5時間と確定し_追加確認を含む総作業時間は発生日数が分かるまで未確定():
    project = badge_project()
    values = {d.qid: d for d in facts.derived_values(project)}
    assert values["D1"].calc.text == "15分 × 20日 ÷ 60 ＝ 5時間"
    assert values["D2"].calc.value is None and values["D2"].missing_names == ["記入漏れの月間発生日数"]
    assert facts.quantity_basis(project) == {"minutes_per_case": (15, "ヒアリング"), "cases_per_month": (20, "ヒアリング")}


def test_確認が必要なのは_改善案の効果に必要で分からない値と人件費だけ():
    needs = {n.key: n for n in facts.needs(badge_project())}
    assert list(needs) == ["Q4", "hourly_cost"]  # 分かっている15分・20日・10分は聞かない
    assert needs["Q4"].status == "未集計" and needs["Q4"].proposals == ["案1"]
    assert needs["Q4"].blocked == ["追加確認を含む総作業時間"]


def test_発生日数が分かれば総作業時間を計算し_確認が必要な一覧から外れる():
    project = badge_project()
    facts.record_fact(project, "Q4", 3, "本人の申告")
    d2 = {d.qid: d for d in facts.derived_values(project)}["D2"]
    assert d2.calc.text == "（15分 × 20日 ＋ 10分 × 3日） ÷ 60 ＝ 5.5時間"
    assert [n.key for n in facts.needs(project)] == ["hourly_cost"]


def test_割合が100パーセントを超えるときは_内訳が合計を上回るとして使わない():
    project = make_project(proposals=1)
    project.diagnosis.analysis = AnalysisResult(
        business_summary="", issues=[], no_issue_viewpoints=[],
        quantities=[q("Q1", "合計", 20, "時間", "確認済み", "20時間"), q("Q2", "転記", 25, "時間", "確認済み", "25時間")],
        derived=[DerivedQuantity(qid="D1", name="転記の割合", formula="Q2 / Q1 * 100", unit="%")])
    d1 = facts.derived_values(project)[0]
    assert d1.calc.value is None and "内訳が合計を上回っています" in d1.calc.error


# --- 入力との照合 ---
def test_引用や数値を入力の中に確認できない値は_未確認にして使わない():
    analysis = AnalysisResult(business_summary="", issues=[], no_issue_viewpoints=[], quantities=[
        q("Q1", "照合の時間", 15, "分", "確認済み", "毎日15分ほどかかる"),
        q("Q2", "照合の時間（引用にない数値）", 30, "分", "確認済み", "毎日15分ほどかかる"),
        q("Q3", "営業日数（入力にない引用）", 22, "日", "確認済み", "月22営業日"),
        q("Q4", "発生日数", 5, "日", "未集計", "記入漏れが月に何日あるかは集計していない"),
        q("Q5", "人件費", 3000, "円", "未確認"),
    ], derived=[DerivedQuantity(qid="D1", name="不正な式", formula="Q1 * Q9", unit="分")])
    checked, notes = verify_quantities(analysis, INFO, HearingState())
    status = {x.qid: (x.status, x.value) for x in checked.quantities}
    assert status == {"Q1": ("確認済み", 15), "Q2": ("未確認", None), "Q3": ("未確認", None),
                      "Q4": ("未集計", None), "Q5": ("未確認", None)}
    assert "引用の中に、その数値がなかった" in notes["Q2"] and "確認できなかった" in notes["Q3"]
    assert checked.derived == [] and "存在しない番号" in notes["D1"]


def test_回答からの引用は_その回の回答とだけ照合する():
    state = HearingState(turns=[HearingTurn(turn_no=1, item_id="H3", question="q", answer="記入漏れは月に3日ほどです")])
    analysis = AnalysisResult(business_summary="", issues=[], no_issue_viewpoints=[], quantities=[
        Quantity(qid="Q1", name="発生日数", value=3, unit="日", status="確認済み", source="回答", turn_no=1, file_no=None,
                 quote="記入漏れは月に3日ほどです"),
        Quantity(qid="Q2", name="発生日数（違う回）", value=3, unit="日", status="確認済み", source="回答", turn_no=2,
                 file_no=None, quote="記入漏れは月に3日ほどです")])
    checked, _ = verify_quantities(analysis, INFO, state)
    assert [x.status for x in checked.quantities] == ["確認済み", "未確認"]


@pytest.mark.parametrize("scenario_id", ["production_report", "sales_report", "repair_reception", "travel_expense"])
def test_デモの数値の一覧は_サンプル回答とすべて照合できる(scenario_id):
    from ai_bic.config import load_framework
    from ai_bic.demo_client import DemoClient, demo_attachments, load_scenarios, sample_hearing
    from ai_bic.hearing import run_diagnosis

    fw = load_framework("general")
    scenario = next(s for s in load_scenarios() if s["id"] == scenario_id)
    info, state = sample_hearing(scenario, fw)
    result = run_diagnosis(DemoClient(scenario), info, state, fw, demo_attachments(scenario))
    assert result.quantity_notes == {}  # 照合で下げた数値・使えない式はない
    assert len(result.analysis.quantities) >= 3
    ids = {x.qid for x in result.analysis.quantities} | {d.qid for d in result.analysis.derived}
    for p in result.proposals.proposals:
        assert p.effect_metric and set(p.effect_inputs) <= ids


# --- 確認済みの範囲と仮定条件（AI-BIC 3.2） ---
def test_仮定条件は_計算できた値と_欠けている値とその影響だけを示す():
    from ai_bic import overview
    from ai_bic.config import load_framework
    from ai_bic.models import ItemStatus

    project = badge_project()
    lines = facts.assumption_lines(project)
    assert lines == ["通常の照合の月間時間は5時間（15分 × 20日 ÷ 60 ＝ 5時間）",
                     "追加確認を含む総作業時間は、記入漏れの月間発生日数が不明のため未確定"]
    hearing = HearingState(item_status={i: ItemStatus(item_id=i, status="充足", known_facts="", lacking="")
                                        for i in ["H1", "H2", "H4", "H5", "H6"]}
                           | {"H3": ItemStatus(item_id="H3", status="不十分", known_facts="", lacking="")})
    texts = overview.open_assumptions(hearing, {}, lines)
    assert texts == ["試算の前提：" + "。".join(lines) + "。"]
    assert all("作業時間・頻度" not in x for x in texts)  # 作業時間・頻度が未確認とまとめて言わない
    # 不足情報欄でも、作業時間・頻度の項目をまとめて聞かない（欠けている数値だけを聞く）
    project.hearing = hearing
    ov = overview.build(project.diagnosis, hearing, load_framework("general"), {}, None, facts.needs(project))
    assert "item:H3" not in [g.key for g in ov.gaps]
    assert "fact:Q4" in [g.key for g in ov.gaps]
