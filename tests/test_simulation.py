"""simulation.py のテスト（改善効果シミュレーター：AI-BIC 2.0 機能A）。

AIは呼ばない（料金ゼロ）。指示書 9.2 の計算の境界値と、不正な入力を確かめる。
"""

import pytest
from pydantic import ValidationError

from ai_bic.simulation import (
    NOTE_TIME_VALUE,
    Scenario,
    SimulationInputs,
    apply_scenario,
    compare,
    default_scenarios,
    label,
    make_inputs,
    parse_number,
    payback_text,
    simulate,
)

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
def test_悲観と楽観は削減率と費用を仮の幅で動かす():
    pessimistic, standard, optimistic = default_scenarios(FULL)
    assert [pessimistic.name, standard.name, optimistic.name] == ["悲観", "標準", "楽観"]
    assert pessimistic.reduction_rate == 20 and pessimistic.initial_cost == 750_000
    assert optimistic.reduction_rate == 60 and optimistic.initial_cost == 500_000
    assert standard.reduction_rate is None  # 標準は基本の入力値のまま


def test_楽観でも削減率は100パーセントを超えない():
    assert default_scenarios(with_(reduction_rate=80))[2].reduction_rate == 100


def test_シナリオの値を利用者が変えると試算が変わる():
    pessimistic = Scenario(name="悲観", reduction_rate=10)
    applied = apply_scenario(FULL, pessimistic)
    assert simulate(applied).monthly_saved_hours == 10
    assert applied.initial_cost == FULL.initial_cost  # 変えなかった値は標準のまま
    assert applied.source_of("reduction_rate") == "ユーザー入力"


def test_シナリオにも不正な値は入らない():
    with pytest.raises(ValidationError):
        Scenario(name="楽観", reduction_rate=150)


# --- 改善案どうしの比較 ---
def test_前提がそろっていれば違いはない():
    result = compare([("案1", FULL), ("案2", with_(reduction_rate=60))])
    assert result.differences == []
    assert result.rows[1][1].monthly_saved_hours == 60


def test_前提条件が違えば比較の前に知らせる():
    result = compare([("案1", FULL), ("案2", with_(cases_per_month=300))])
    assert len(result.differences) == 1
    assert "月間処理件数（件）が改善案によって異なります（案1：200、案2：300）" == result.differences[0]
