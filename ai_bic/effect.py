"""改善効果の試算（外部評価 B06）。

現状の作業時間をそのまま「削減効果」として見せないために、
削減できる割合・人件費の単価・導入にかかる時間・運用費を利用者が仮定として入力し、
その仮定から効果を計算する。

- 計算はすべてこのプログラムで行い、AIには計算させない（数値の正確さが必要なため）
- 仮定が未入力の場合、その仮定を使う計算はせず、何が未入力かを返す（根拠のない数値を作らない）
- 画面（app.py）とExcelのレポート（report.py）が同じ結果を使う
"""

import re
import unicodedata
from dataclasses import dataclass, field

from ai_bic.models import AnalysisResult

# 入力欄の名前（画面・レポート・未入力の案内で共通）
LABEL_CURRENT = "現状の作業時間（時間/月）"
LABEL_RATE = "削減できる割合（%）"
LABEL_HOURLY = "人件費の単価（円/時間）"
LABEL_SETUP = "導入にかかる時間（時間）"
LABEL_RUNNING = "運用費（円/月）"


@dataclass(frozen=True)
class EffectAssumptions:
    """利用者が入力する仮定。未入力は None。"""

    current_hours: float | None = None  # 現状の作業時間（時間/月）
    reduction_rate: float | None = None  # 削減できる割合（%、0〜100）
    hourly_cost: float | None = None  # 人件費の単価（円/時間）
    setup_hours: float | None = None  # 導入にかかる時間（時間。1回だけ）
    running_cost: float | None = None  # 運用費（円/月。新しくかかる費用。なければ0）


@dataclass
class EffectEstimate:
    """試算の結果。計算できなかった値は None。"""

    saved_hours: float | None = None  # 削減できる時間（時間/月）
    saved_yen: float | None = None  # 削減できる人件費（円/月）
    setup_yen: float | None = None  # 導入にかかる費用（円。導入の時間×単価）
    net_yen: float | None = None  # 運用費を引いた効果（円/月）
    payback_months: float | None = None  # 元が取れるまでの月数
    formulas: list[str] = field(default_factory=list)  # 画面に表示する計算式
    missing: list[str] = field(default_factory=list)  # 未入力のため計算できなかった仮定の名前
    notes: list[str] = field(default_factory=list)  # 補足（元が取れない場合など）


def _named(a: EffectAssumptions) -> list[tuple[str, float | None]]:
    """仮定の名前と値の組（入力欄の並び順）。"""
    return [
        (LABEL_CURRENT, a.current_hours), (LABEL_RATE, a.reduction_rate), (LABEL_HOURLY, a.hourly_cost),
        (LABEL_SETUP, a.setup_hours), (LABEL_RUNNING, a.running_cost),
    ]


def _check(a: EffectAssumptions) -> None:
    """入力の範囲を確かめる。画面の入力欄でも制限するが、部品としても守る。"""
    for name, value in _named(a):
        if value is not None and value < 0:
            raise ValueError(f"{name}は0以上で入力してください")
    if a.reduction_rate is not None and a.reduction_rate > 100:
        raise ValueError(f"{LABEL_RATE}は100以下で入力してください")


def format_number(value: float) -> str:
    """表示用の数値（小数は1桁まで、桁区切りあり）。"""
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.1f}"


def estimate(a: EffectAssumptions) -> EffectEstimate:
    """仮定から効果を計算する。"""
    _check(a)
    # 未入力の仮定は、計算の途中ではなく最初にすべて記録する
    # （途中で記録すると、前の段階が計算できないときに後の仮定の入力漏れを案内できない）
    r = EffectEstimate(missing=[name for name, value in _named(a) if value is None])

    def need(*pairs: tuple[str, float | None]) -> bool:
        """計算に必要な仮定がそろっているか。"""
        return all(value is not None for _, value in pairs)

    if need((LABEL_CURRENT, a.current_hours), (LABEL_RATE, a.reduction_rate)):
        r.saved_hours = a.current_hours * a.reduction_rate / 100
        r.formulas.append(
            f"削減できる時間：{format_number(a.current_hours)}時間 × {format_number(a.reduction_rate)}% ＝ 月{format_number(r.saved_hours)}時間"
        )
    if r.saved_hours is not None and need((LABEL_HOURLY, a.hourly_cost)):
        r.saved_yen = r.saved_hours * a.hourly_cost
        r.formulas.append(
            f"削減できる人件費：{format_number(r.saved_hours)}時間 × {format_number(a.hourly_cost)}円 ＝ 月{format_number(r.saved_yen)}円"
        )
    if need((LABEL_SETUP, a.setup_hours), (LABEL_HOURLY, a.hourly_cost)):
        r.setup_yen = a.setup_hours * a.hourly_cost
        r.formulas.append(
            f"導入にかかる費用：{format_number(a.setup_hours)}時間 × {format_number(a.hourly_cost)}円 ＝ {format_number(r.setup_yen)}円"
        )
    if r.saved_yen is not None and need((LABEL_RUNNING, a.running_cost)):
        r.net_yen = r.saved_yen - a.running_cost
        r.formulas.append(
            f"運用費を引いた効果：{format_number(r.saved_yen)}円 − {format_number(a.running_cost)}円 ＝ 月{format_number(r.net_yen)}円"
        )
    if r.net_yen is not None and r.setup_yen is not None:
        if r.net_yen > 0:
            r.payback_months = r.setup_yen / r.net_yen
            r.formulas.append(
                f"元が取れるまで：{format_number(r.setup_yen)}円 ÷ {format_number(r.net_yen)}円 ＝ 約{format_number(r.payback_months)}か月"
            )
        else:
            r.notes.append("運用費が削減できる人件費以上のため、この仮定では元が取れません")
    return r


# ---------------------------------------------------------------
# 現状の作業時間の初期値（課題分析の「計算結果」から読み取る）
# ---------------------------------------------------------------
_RESULT = re.compile(r"=\s*約?\s*(\d+(?:,\d{3})*(?:\.\d+)?)\s*(時間|分)")


def hours_from_calculation(calculation: str | None) -> float | None:
    """計算式の答え（「＝」の右）を時間で返す。読み取れなければ None。

    例：「90分×20日÷60＝30時間」→ 30.0、「45分×20日＝900分」→ 15.0
    """
    text = unicodedata.normalize("NFKC", calculation or "")
    matches = _RESULT.findall(text)
    if not matches:
        return None
    number, unit = matches[-1]  # 「＝」が複数あるときは最後の答え
    value = float(number.replace(",", ""))
    return value / 60 if unit == "分" else value


def default_current_hours(analysis: AnalysisResult) -> tuple[float | None, str | None]:
    """現状の作業時間の初期値と、その出どころ（例：「課題 I2 の計算式：…」）。

    引用の照合（output_check.py）を通った「計算結果」の課題だけを使う。
    格下げされた課題は「要確認」になっているため使わない。
    """
    for issue in analysis.issues:
        if issue.evidence_type != "計算結果":
            continue
        hours = hours_from_calculation(issue.calculation)
        if hours is not None:
            return hours, f"課題 {issue.issue_id} の計算式：{issue.calculation}"
    return None, None
