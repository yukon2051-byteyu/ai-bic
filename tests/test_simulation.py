"""simulation.py のテスト（改善効果シミュレーター：AI-BIC 2.0 機能A）。

AIは呼ばない（料金ゼロ）。指示書 9.2 の計算の境界値と、不正な入力を確かめる。
"""

import pytest
from pydantic import ValidationError

from ai_bic.simulation import (
    NOTE_TIME_VALUE,
    Scenario,
    SimulationInputs,
    auto_value,
    blank_scenarios,
    compare,
    hours_from_calculation,
    label,
    make_inputs,
    parse_number,
    payback_text,
    per_case_from_calculation,
    prefill,
    scenario_inputs,
    simulate,
)
from ai_bic.models import AnalysisResult, Issue

# 30分 × 200件 ＝ 月100時間。削減率40% → 月40時間 → 年480時間 × 3,000円 ＝ 1,440,000円
FULL = SimulationInputs(
    task_name="月次集計", minutes_per_case=30, cases_per_month=200, hourly_cost=3000, reduction_rate=40,
    initial_cost=500_000, monthly_running_cost=20_000, other_initial_cost=100_000,
)


def with_(**changes) -> SimulationInputs:
    return FULL.model_copy(update=changes)


# --- 指示書 3.3 の計算式 ---
def test_指示書の計算式どおりに計算する():
    r = simulate(FULL)
    assert r.monthly_hours == 100
    assert r.monthly_saved_hours == 40
    assert r.after_monthly_hours == 60
    assert r.annual_saved_hours == 480
    assert r.annual_saved_yen == 1_440_000
    assert r.annual_running_cost == 240_000
    assert r.annual_net_yen == 1_200_000
    assert r.initial_investment == 600_000
    assert r.monthly_net_yen == 100_000
    assert r.payback_months == 6  # 600,000円 ÷ 100,000円
    assert r.payback_status == "計算済み"
    assert r.first_year_net_yen == 600_000  # 年間純効果額 − 初期投資額
    assert r.missing == []


def test_計算式に入力値が入り_利用者が確かめられる():
    text = "\n".join(simulate(FULL).formulas)
    assert "30分 × 200件 ÷ 60 ＝ 100時間" in text
    assert "600,000円 ÷ 100,000円（月間純効果額） ＝ 6か月" in text


def test_削減率0パーセントでは削減がなく_回収見込みなし():
    r = simulate(with_(reduction_rate=0))
    assert r.monthly_saved_hours == 0 and r.annual_saved_yen == 0
    assert r.payback_status == "回収見込みなし" and r.payback_months is None
    assert payback_text(r) == "回収見込みなし"


def test_削減率100パーセントでは作業時間がすべて減る():
    r = simulate(with_(reduction_rate=100))
    assert r.monthly_saved_hours == 100 and r.after_monthly_hours == 0


def test_処理件数0件では作業時間も効果も0():
    r = simulate(with_(cases_per_month=0))
    assert r.monthly_hours == 0 and r.annual_saved_yen == 0
    assert r.payback_status == "回収見込みなし"


def test_初期費用0円なら回収期間は0か月():
    r = simulate(with_(initial_cost=0, other_initial_cost=0))
    assert r.payback_status == "初期投資なし" and r.payback_months == 0
    assert r.first_year_net_yen == r.annual_net_yen


def test_月間純効果額がマイナスなら回収見込みなし():
    r = simulate(with_(monthly_running_cost=200_000))  # 削減効果 月120,000円 < 運用費 月200,000円
    assert r.monthly_net_yen == -80_000
    assert r.payback_status == "回収見込みなし"
    assert r.first_year_net_yen < 0
    assert r.notes


def test_月間純効果額が0円ちょうども回収見込みなし():
    r = simulate(with_(monthly_running_cost=120_000))
    assert r.monthly_net_yen == 0 and r.payback_status == "回収見込みなし"


def test_未入力の値を使う計算はしない():
    r = simulate(with_(hourly_cost=None))
    assert r.annual_saved_hours == 480  # 人件費を使わない計算はできる
    assert r.annual_saved_yen is None and r.annual_net_yen is None and r.payback_months is None
    assert r.payback_status == "未入力"
    assert r.missing == [label("hourly_cost")]


def test_すべて未入力なら何も計算しない():
    r = simulate(SimulationInputs())
    assert r.formulas == [] and len(r.missing) == 7


def test_削減効果額が現金の削減ではないことを明記する():
    assert "現金" in NOTE_TIME_VALUE


# --- 不正な入力 ---
@pytest.mark.parametrize("value", ["abc", "12分", True, "nan", "inf", float("nan")])
def test_数値でない入力は受け付けない(value):
    inputs, errors = make_inputs({"minutes_per_case": value})
    assert inputs is None
    assert errors["minutes_per_case"] == "1件あたりの作業時間（分）：数値で入力してください"


def test_負の数は受け付けない():
    _, errors = make_inputs({"hourly_cost": -1})
    assert errors["hourly_cost"] == "時間あたり人件費（円）：0以上で入力してください"


def test_削減率は100パーセントまで():
    _, errors = make_inputs({"reduction_rate": 100.5})
    assert "100以下" in errors["reduction_rate"]


def test_全角数字と桁区切りは数値として読む():
    assert parse_number("１，２００") == 1200
    assert parse_number(" 30 ") == 30
    assert parse_number("") is None and parse_number(None) is None


def test_複数の誤りをまとめて返す():
    _, errors = make_inputs({"minutes_per_case": "x", "hourly_cost": -5, "reduction_rate": 120})
    assert set(errors) == {"minutes_per_case", "hourly_cost", "reduction_rate"}


def test_出どころは入力済みの項目だけが持つ():
    a = SimulationInputs(minutes_per_case=30, sources={"minutes_per_case": "ヒアリング"})
    assert a.source_of("minutes_per_case") == "ヒアリング"
    assert a.source_of("hourly_cost") is None  # 未入力
    assert SimulationInputs(hourly_cost=3000).source_of("hourly_cost") == "ユーザー入力"


def test_不明な出どころは受け付けない():
    with pytest.raises(ValidationError):
        SimulationInputs(minutes_per_case=30, sources={"minutes_per_case": "実測"})


# --- 3シナリオ ---
def scenario(name, **values) -> Scenario:
    return Scenario(name=name, **values)


def test_空欄のシナリオは標準の値に倍率をかけた自動の値になる():
    pessimistic = scenario_inputs(FULL, scenario("悲観"))
    optimistic = scenario_inputs(FULL, scenario("楽観"))
    assert pessimistic.reduction_rate == 20 and pessimistic.initial_cost == 750_000  # 削減率×0.5・費用×1.5
    assert optimistic.reduction_rate == 60 and optimistic.initial_cost == 500_000  # 削減率×1.5・費用×1.0
    assert [sc.name for sc in blank_scenarios()] == ["悲観", "標準", "楽観"]


def test_標準シナリオは基本の入力値そのもの():
    assert scenario_inputs(FULL, scenario("標準", reduction_rate=90)) == FULL


def test_標準の値を変えると自動の値も追いかける():
    """古い値が残らない：標準の削減率を変えたあとに、悲観が前の削減率のままにならない。"""
    assert scenario_inputs(with_(reduction_rate=80), scenario("悲観")).reduction_rate == 40


def test_楽観でも削減率は100パーセントを超えない():
    assert auto_value(with_(reduction_rate=80), "楽観", "reduction_rate") == 100


def test_標準が未入力なら自動の値も未入力():
    assert scenario_inputs(with_(initial_cost=None), scenario("悲観")).initial_cost is None


def test_シナリオの値を利用者が入れると試算が変わり_出どころはユーザー入力():
    applied = scenario_inputs(FULL, scenario("悲観", reduction_rate=10))
    assert simulate(applied).monthly_saved_hours == 10
    assert applied.source_of("reduction_rate") == "ユーザー入力"
    assert applied.initial_cost == 750_000  # 入れなかった欄は自動のまま


def test_自動の値は標準の値の出どころを引き継ぐ():
    base = with_(sources={"reduction_rate": "AI推定"})
    assert scenario_inputs(base, scenario("楽観")).source_of("reduction_rate") == "AI推定"


def test_シナリオにも不正な値は入らない():
    with pytest.raises(ValidationError):
        Scenario(name="楽観", reduction_rate=150)
    with pytest.raises(ValidationError):
        Scenario(name="悲観", initial_cost="abc")


# --- 改善案どうしの比較 ---
def test_前提がそろっていれば違いはない():
    result = compare([("案1", FULL), ("案2", with_(reduction_rate=60))])
    assert result.differences == []
    assert result.rows[1][1].monthly_saved_hours == 60


def test_前提条件が違えば比較の前に知らせる():
    result = compare([("案1", FULL), ("案2", with_(cases_per_month=300))])
    assert len(result.differences) == 1
    assert "月間処理件数（件）が改善案によって異なります（案1：200、案2：300）" == result.differences[0]


# --- 初期値（課題分析の計算式から。B06 から移した） ---
def issue(issue_id="I1", evidence_type="計算結果", calculation="90分×20日÷60＝30時間") -> Issue:
    return Issue(
        issue_id=issue_id, title="", summary="", viewpoint_ids=["V1"], evidence_type=evidence_type,
        evidences=[], calculation=calculation, assumed_cause="", impact="", severity="中",
        severity_reason="", followup_questions=[],
    )


def analysis(*issues: Issue) -> AnalysisResult:
    return AnalysisResult(business_summary="", issues=list(issues), no_issue_viewpoints=[])


@pytest.mark.parametrize(
    "calculation, hours",
    [
        ("90分×20日÷60＝30時間", 30),
        ("90分×20日÷60=30時間", 30),
        ("45分×20日＝900分", 15),
        ("1日90分×20日＝1,800分＝30時間", 30),  # 「＝」が複数なら最後の答え
        ("12時間×1回＝約12時間", 12),
        ("９０分×２０日÷６０＝３０時間", 30),  # 全角の数字
    ],
)
def test_計算式の答えを時間で読む(calculation, hours):
    assert hours_from_calculation(calculation) == hours


@pytest.mark.parametrize("calculation", [None, "", "90分×20日", "20件×3人＝60件"])
def test_答えのない計算式は読まない(calculation):
    assert hours_from_calculation(calculation) is None


@pytest.mark.parametrize(
    "calculation, pair",
    [
        ("90分×20日÷60＝30時間", (90, 20)),
        ("15分×1,200件＝18,000分", (15, 1200)),
        ("１０分×３回＝３０分", (10, 3)),
        ("12時間×1回＝約12時間", None),  # 1件あたりの分が分からない
        ("20件×3人＝60件", None),
        (None, None),
    ],
)
def test_計算式から1件あたりの分と件数を読む(calculation, pair):
    assert per_case_from_calculation(calculation) == pair


def test_初期値は計算結果の課題から入れ_出どころはヒアリング():
    p = prefill("月次集計", analysis(issue("I1", "本人の申告", None), issue("I2")))
    assert p.inputs.minutes_per_case == 90 and p.inputs.cases_per_month == 20
    assert p.inputs.source_of("minutes_per_case") == "ヒアリング"
    assert p.inputs.task_name == "月次集計"
    assert "課題 I2 の計算式" in p.notes[0]


def test_ヒアリングで得ていない値は初期値に入れない():
    p = prefill("月次集計", analysis(issue()))
    for name in ("hourly_cost", "reduction_rate", "initial_cost", "monthly_running_cost", "other_initial_cost"):
        assert getattr(p.inputs, name) is None


def test_格下げされた課題の計算式は使わない():
    p = prefill("月次集計", analysis(issue("I1", "要確認")))
    assert p.inputs.minutes_per_case is None
    assert "読み取れなかった" in p.notes[0]


def test_合計の時間しか分からなければ_分けて入力するよう案内する():
    p = prefill("月次集計", analysis(issue(calculation="12時間×1回＝約12時間")))
    assert p.inputs.minutes_per_case is None
    assert "約12時間" in p.notes[0] and "分けて入力" in p.notes[0]
