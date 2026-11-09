"""effect.py のテスト（改善効果の試算：外部評価 B06）。

AIは呼ばない（料金ゼロ）。計算と、計算式からの現状時間の読み取りを確かめる。
"""

import pytest

from ai_bic.effect import (
    LABEL_CURRENT,
    LABEL_HOURLY,
    LABEL_RATE,
    LABEL_RUNNING,
    LABEL_SETUP,
    EffectAssumptions,
    default_current_hours,
    estimate,
    hours_from_calculation,
)
from ai_bic.models import AnalysisResult, Issue

FULL = EffectAssumptions(
    current_hours=30, reduction_rate=40, hourly_cost=3000, setup_hours=20, running_cost=6000
)


def issue(issue_id="I1", evidence_type="計算結果", calculation="90分×20日÷60＝30時間") -> Issue:
    return Issue(
        issue_id=issue_id, title="", summary="", viewpoint_ids=["V1"], evidence_type=evidence_type,
        evidences=[], calculation=calculation, assumed_cause="", impact="", severity="中",
        severity_reason="", followup_questions=[],
    )


def analysis(*issues: Issue) -> AnalysisResult:
    return AnalysisResult(business_summary="", issues=list(issues), no_issue_viewpoints=[])


# --- 計算（正常系） ---
def test_all_assumptions_give_every_result():
    r = estimate(FULL)
    assert r.saved_hours == 12  # 30時間 × 40%
    assert r.saved_yen == 36000  # 12時間 × 3,000円
    assert r.setup_yen == 60000  # 20時間 × 3,000円
    assert r.net_yen == 30000  # 36,000円 − 6,000円
    assert r.payback_months == 2  # 60,000円 ÷ 30,000円
    assert r.missing == [] and r.notes == []


def test_formulas_show_the_assumptions():
    """利用者が仮定と結果の関係を確かめられるよう、計算式に入力した値が出る。"""
    text = "\n".join(estimate(FULL).formulas)
    assert "30時間 × 40% ＝ 月12時間" in text
    assert "12時間 × 3,000円 ＝ 月36,000円" in text
    assert "60,000円 ÷ 30,000円 ＝ 約2か月" in text


def test_changing_an_assumption_changes_the_result():
    """完了の確認（B06）：仮定を変えると試算が変わる。"""
    a = estimate(FULL)
    b = estimate(EffectAssumptions(**{**FULL.__dict__, "reduction_rate": 20}))
    assert (a.saved_hours, b.saved_hours) == (12, 6)
    assert b.net_yen == 12000 and b.payback_months == 5


def test_current_hours_are_not_shown_as_saved_hours():
    """完了の確認（B06）：現状の月30時間を、そのまま削減時間にしない。"""
    r = estimate(EffectAssumptions(current_hours=30))
    assert r.saved_hours is None
    assert LABEL_RATE in r.missing
    assert r.formulas == []


# --- 未入力の仮定 ---
def test_nothing_entered_gives_no_numbers():
    r = estimate(EffectAssumptions())
    assert (r.saved_hours, r.saved_yen, r.setup_yen, r.net_yen, r.payback_months) == (None,) * 5
    assert set(r.missing) == {LABEL_CURRENT, LABEL_RATE, LABEL_HOURLY, LABEL_SETUP, LABEL_RUNNING}


def test_hours_without_hourly_cost():
    """単価がなくても、削減できる時間までは計算する。金額は出さない。"""
    r = estimate(EffectAssumptions(current_hours=30, reduction_rate=50))
    assert r.saved_hours == 15
    assert r.saved_yen is None and r.setup_yen is None
    assert LABEL_HOURLY in r.missing


def test_missing_names_are_not_repeated():
    r = estimate(EffectAssumptions(current_hours=30, reduction_rate=50))
    assert r.missing.count(LABEL_HOURLY) == 1


def test_without_setup_hours_no_payback():
    r = estimate(EffectAssumptions(current_hours=30, reduction_rate=40, hourly_cost=3000, running_cost=0))
    assert r.net_yen == 36000
    assert r.payback_months is None
    assert LABEL_SETUP in r.missing


def test_zero_running_cost_is_a_valid_input():
    """「運用費0円」は未入力ではなく、入力として扱う。"""
    r = estimate(EffectAssumptions(**{**FULL.__dict__, "running_cost": 0}))
    assert r.net_yen == 36000
    assert LABEL_RUNNING not in r.missing


def test_zero_setup_hours_means_paid_back_immediately():
    r = estimate(EffectAssumptions(**{**FULL.__dict__, "setup_hours": 0}))
    assert r.payback_months == 0


# --- 元が取れない場合・入力の範囲 ---
@pytest.mark.parametrize("running_cost", [36000, 50000])
def test_running_cost_not_below_saving_means_no_payback(running_cost):
    r = estimate(EffectAssumptions(**{**FULL.__dict__, "running_cost": running_cost}))
    assert r.payback_months is None
    assert r.notes == ["運用費が削減できる人件費以上のため、この仮定では元が取れません"]


@pytest.mark.parametrize(
    "field, value, message",
    [
        ("current_hours", -1, "0以上"),
        ("reduction_rate", -5, "0以上"),
        ("reduction_rate", 101, "100以下"),
        ("hourly_cost", -1, "0以上"),
        ("setup_hours", -1, "0以上"),
        ("running_cost", -1, "0以上"),
    ],
)
def test_out_of_range_inputs_are_rejected(field, value, message):
    with pytest.raises(ValueError, match=message):
        estimate(EffectAssumptions(**{**FULL.__dict__, field: value}))


def test_decimal_values_are_shown_with_one_digit():
    r = estimate(EffectAssumptions(current_hours=12.5, reduction_rate=30))
    assert r.saved_hours == pytest.approx(3.75)
    assert "12.5時間 × 30% ＝ 月3.8時間" in r.formulas[0]


# --- 計算式からの現状時間の読み取り ---
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
def test_hours_from_calculation(calculation, hours):
    assert hours_from_calculation(calculation) == hours


@pytest.mark.parametrize("calculation", [None, "", "90分×20日", "20件×3人＝60件"])
def test_unreadable_calculation_gives_none(calculation):
    assert hours_from_calculation(calculation) is None


def test_default_uses_the_first_calculated_issue():
    a = analysis(issue("I1", "本人の申告", None), issue("I2"), issue("I3", calculation="10分×6日＝60分"))
    hours, source = default_current_hours(a)
    assert hours == 30
    assert source == "課題 I2 の計算式：90分×20日÷60＝30時間"


def test_default_ignores_downgraded_issues():
    """引用の照合で「要確認」に格下げされた課題の計算式は、初期値に使わない。"""
    hours, source = default_current_hours(analysis(issue("I1", "要確認")))
    assert (hours, source) == (None, None)


def test_default_skips_unreadable_calculation():
    a = analysis(issue("I1", calculation="20件×3人＝60件"), issue("I2", calculation="45分×20日＝900分"))
    assert default_current_hours(a)[0] == 15
