"""改善効果シミュレーター（AI-BIC 2.0 機能A）の計算。

指示書 3.3 の計算式をプログラムで行う。AIには計算させない（数値の正確さが必要なため）。

- 入力値ごとに「出どころ」（ユーザー入力・ヒアリング・AI推定・デモ値）を持たせ、画面で区別して表示する
- 未入力の値を使う計算はせず、何が未入力かを返す（根拠のない数値を作らない。B06 の方針を引き継ぐ）
- 削減効果額は「作業時間の金額換算」であり、現金の支出が減るとは限らない（NOTE_TIME_VALUE）
"""

import math
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from ai_bic import config

ValueSource = Literal["ユーザー入力", "ヒアリング", "AI推定", "デモ値"]
ScenarioName = Literal["悲観", "標準", "楽観"]
PaybackStatus = Literal["計算済み", "初期投資なし", "回収見込みなし", "未入力"]

# 入力項目：項目名 → (画面の名前, 単位, 最大値)。並び順は画面・Excel・未入力の案内で共通
NUMBER_FIELDS: dict[str, tuple[str, str, float | None]] = {
    "minutes_per_case": ("1件あたりの作業時間", "分", None),
    "cases_per_month": ("月間処理件数", "件", None),
    "hourly_cost": ("時間あたり人件費", "円", None),
    "reduction_rate": ("想定削減率", "%", 100.0),
    "initial_cost": ("初期導入費用", "円", None),
    "monthly_running_cost": ("月額運用費", "円", None),
    "other_initial_cost": ("その他の初期費用", "円", None),
}

NOTE_TIME_VALUE = (
    "削減効果額は、削減できる作業時間を人件費で金額に換算したものです。"
    "実際に現金の支出が減ることを意味するとは限りません（空いた時間を別の業務に使う場合など）。"
)


def label(name: str) -> str:
    """入力項目の画面の名前（単位つき）。例：「1件あたりの作業時間（分）」"""
    text, unit, _ = NUMBER_FIELDS[name]
    return f"{text}（{unit}）"


def parse_number(value) -> float | None:
    """入力された値を数値にする。空欄は None。数値にできなければ ValueError。

    全角数字・桁区切りのカンマ・前後の空白は受け付ける（例：「１，２００」→ 1200.0）。
    """
    if value is None:
        return None
    if isinstance(value, bool):  # True/False は数値として扱わない
        raise ValueError("数値で入力してください")
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = unicodedata.normalize("NFKC", str(value)).strip().replace(",", "")
        if text == "":
            return None
        try:
            number = float(text)
        except ValueError:
            raise ValueError("数値で入力してください") from None
    if not math.isfinite(number):  # 「nan」「inf」も float() は受け付けてしまうため
        raise ValueError("数値で入力してください")
    return number


def _check_number(value, info):
    """入力項目の値を確かめる（数値であること・0以上・上限以下）。モデルの検証で使う。"""
    number = parse_number(value)
    _, _, max_value = NUMBER_FIELDS[info.field_name]
    if number is not None and number < 0:
        raise ValueError("0以上で入力してください")
    if number is not None and max_value is not None and number > max_value:
        raise ValueError(f"{max_value:g}以下で入力してください")
    return number


class SimulationInputs(BaseModel):
    """1つの改善案の試算に使う入力値。未入力は None。JSON の保存にもこの形を使う。"""

    task_name: str = ""
    minutes_per_case: float | None = None
    cases_per_month: float | None = None
    hourly_cost: float | None = None
    reduction_rate: float | None = None
    initial_cost: float | None = None
    monthly_running_cost: float | None = None
    other_initial_cost: float | None = None
    sources: dict[str, ValueSource] = Field(default_factory=dict)  # 項目名 → 出どころ（入力済みの項目だけ）

    _number = field_validator(*NUMBER_FIELDS, mode="before")(_check_number)

    @field_validator("sources")
    @classmethod
    def _sources(cls, value: dict[str, str]) -> dict[str, str]:
        unknown = [name for name in value if name not in NUMBER_FIELDS]
        if unknown:
            raise ValueError(f"不明な項目の出どころがあります：{'、'.join(unknown)}")
        return value

    def source_of(self, name: str) -> ValueSource | None:
        """項目の出どころ。未入力なら None、入力済みで出どころの記録がなければ「ユーザー入力」。"""
        if getattr(self, name) is None:
            return None
        return self.sources.get(name, "ユーザー入力")


def make_inputs(raw: dict) -> tuple[SimulationInputs | None, dict[str, str]]:
    """画面や読み込んだファイルの値から入力値を作る。

    戻り値：(入力値, 項目名 → エラーメッセージ)。エラーがあれば入力値は None。
    """
    try:
        return SimulationInputs.model_validate(raw), {}
    except ValidationError as e:
        errors = {}
        for err in e.errors():
            name = str(err["loc"][0]) if err["loc"] else ""
            prefix = label(name) if name in NUMBER_FIELDS else name
            message = str(err.get("ctx", {}).get("error", err["msg"]))
            errors[name] = f"{prefix}：{message}"
        return None, errors


@dataclass
class SimulationResult:
    """試算の結果。計算できなかった値は None。"""

    monthly_hours: float | None = None  # 月間作業時間（現状）
    after_monthly_hours: float | None = None  # 改善後の月間作業時間（グラフ用）
    monthly_saved_hours: float | None = None
    annual_saved_hours: float | None = None
    annual_saved_yen: float | None = None  # 年間削減効果額（時間の金額換算）
    annual_running_cost: float | None = None
    annual_net_yen: float | None = None  # 年間純効果額
    initial_investment: float | None = None
    monthly_net_yen: float | None = None  # 月間純効果額
    payback_months: float | None = None
    payback_status: PaybackStatus = "未入力"
    first_year_net_yen: float | None = None  # 初年度純効果額
    formulas: list[str] = field(default_factory=list)  # 画面に表示する計算式（入力値の入ったもの）
    missing: list[str] = field(default_factory=list)  # 未入力のため計算に使えなかった項目の名前
    notes: list[str] = field(default_factory=list)


def fmt(value: float) -> str:
    """表示用の数値（小数は1桁まで、桁区切りあり）。"""
    rounded = round(value, 1)
    return f"{rounded:,.0f}" if float(rounded).is_integer() else f"{rounded:,.1f}"


def simulate(a: SimulationInputs) -> SimulationResult:
    """指示書 3.3 の計算式で試算する。"""
    r = SimulationResult(missing=[label(name) for name in NUMBER_FIELDS if getattr(a, name) is None])
    m, c, h = a.minutes_per_case, a.cases_per_month, a.hourly_cost
    rate, running = a.reduction_rate, a.monthly_running_cost

    if m is not None and c is not None:
        r.monthly_hours = m * c / 60
        r.formulas.append(f"月間作業時間：{fmt(m)}分 × {fmt(c)}件 ÷ 60 ＝ {fmt(r.monthly_hours)}時間")
    if r.monthly_hours is not None and rate is not None:
        r.monthly_saved_hours = r.monthly_hours * rate / 100
        r.after_monthly_hours = r.monthly_hours - r.monthly_saved_hours
        r.annual_saved_hours = r.monthly_saved_hours * 12
        r.formulas.append(
            f"月間削減時間：{fmt(r.monthly_hours)}時間 × {fmt(rate)}% ＝ {fmt(r.monthly_saved_hours)}時間"
        )
        r.formulas.append(f"年間削減時間：{fmt(r.monthly_saved_hours)}時間 × 12 ＝ {fmt(r.annual_saved_hours)}時間")
    if r.annual_saved_hours is not None and h is not None:
        r.annual_saved_yen = r.annual_saved_hours * h
        r.formulas.append(
            f"年間削減効果額：{fmt(r.annual_saved_hours)}時間 × {fmt(h)}円 ＝ {fmt(r.annual_saved_yen)}円"
        )
    if running is not None:
        r.annual_running_cost = running * 12
        r.formulas.append(f"年間運用費：{fmt(running)}円 × 12 ＝ {fmt(r.annual_running_cost)}円")
    if r.annual_saved_yen is not None and r.annual_running_cost is not None:
        r.annual_net_yen = r.annual_saved_yen - r.annual_running_cost
        r.monthly_net_yen = r.annual_net_yen / 12
        r.formulas.append(
            f"年間純効果額：{fmt(r.annual_saved_yen)}円 − {fmt(r.annual_running_cost)}円 ＝ {fmt(r.annual_net_yen)}円"
        )
    if a.initial_cost is not None and a.other_initial_cost is not None:
        r.initial_investment = a.initial_cost + a.other_initial_cost
        r.formulas.append(
            f"初期投資額：{fmt(a.initial_cost)}円 ＋ {fmt(a.other_initial_cost)}円 ＝ {fmt(r.initial_investment)}円"
        )

    if r.monthly_net_yen is not None and r.initial_investment is not None:
        r.first_year_net_yen = r.annual_net_yen - r.initial_investment
        r.formulas.append(
            f"初年度純効果額：{fmt(r.annual_net_yen)}円 − {fmt(r.initial_investment)}円 ＝ {fmt(r.first_year_net_yen)}円"
        )
        if r.monthly_net_yen <= 0:
            r.payback_status = "回収見込みなし"
            r.notes.append("月間純効果額が0円以下のため、この条件では初期投資を回収できません")
        elif r.initial_investment == 0:
            r.payback_status = "初期投資なし"
            r.payback_months = 0.0
        else:
            r.payback_status = "計算済み"
            r.payback_months = r.initial_investment / r.monthly_net_yen
            r.formulas.append(
                f"投資回収期間：{fmt(r.initial_investment)}円 ÷ {fmt(r.monthly_net_yen)}円（月間純効果額）"
                f" ＝ {fmt(r.payback_months)}か月"
            )
    return r


def payback_text(r: SimulationResult) -> str:
    """投資回収期間の表示用の文字。"""
    if r.payback_status == "計算済み":
        return f"{fmt(r.payback_months)}か月"
    if r.payback_status == "初期投資なし":
        return "初期投資なし（すぐに効果）"
    if r.payback_status == "回収見込みなし":
        return "回収見込みなし"
    return "未入力の項目があるため計算できません"


# ---------------------------------------------------------------
# 悲観・標準・楽観の3シナリオ（指示書 3.4）
# ---------------------------------------------------------------
SCENARIO_FIELDS = ("reduction_rate", "initial_cost", "monthly_running_cost", "other_initial_cost")


class Scenario(BaseModel):
    """シナリオごとに変えられる値。None は「標準と同じ値を使う」。"""

    name: ScenarioName
    reduction_rate: float | None = None
    initial_cost: float | None = None
    monthly_running_cost: float | None = None
    other_initial_cost: float | None = None

    _number = field_validator(*SCENARIO_FIELDS, mode="before")(_check_number)


def default_scenarios(base: SimulationInputs) -> list[Scenario]:
    """3シナリオの初期値。標準は基本の入力値のまま。

    悲観・楽観は「削減率と費用を一定の倍率で動かした仮の幅」で、根拠のある予測ではない
    （倍率は config.SCENARIO_FACTORS。画面にもそう表示し、利用者が自由に変えられるようにする）。
    """
    scenarios = [Scenario(name="標準")]
    for name in ("悲観", "楽観"):
        rate_factor, cost_factor = config.SCENARIO_FACTORS[name]
        values = {}
        if base.reduction_rate is not None:
            values["reduction_rate"] = min(100.0, round(base.reduction_rate * rate_factor, 1))
        for cost in ("initial_cost", "monthly_running_cost", "other_initial_cost"):
            if getattr(base, cost) is not None:
                values[cost] = round(getattr(base, cost) * cost_factor)
        scenarios.append(Scenario(name=name, **values))
    order = {"悲観": 0, "標準": 1, "楽観": 2}
    return sorted(scenarios, key=lambda sc: order[sc.name])


def apply_scenario(base: SimulationInputs, scenario: Scenario) -> SimulationInputs:
    """基本の入力値にシナリオの値を重ねる。シナリオで変えた値の出どころは「ユーザー入力」とする。"""
    changes = {name: getattr(scenario, name) for name in SCENARIO_FIELDS if getattr(scenario, name) is not None}
    sources = {**base.sources, **{name: "ユーザー入力" for name in changes}}
    return base.model_copy(update={**changes, "sources": sources})


# ---------------------------------------------------------------
# 改善案どうしの比較（指示書 3.5）
# ---------------------------------------------------------------
# 現状の業務を表す項目。改善案どうしでここが違うと、効果を同じ物差しで比べられない
BASELINE_FIELDS = ("task_name", "minutes_per_case", "cases_per_month", "hourly_cost")


@dataclass
class Comparison:
    rows: list[tuple[str, SimulationResult]]  # (改善案の名前, 試算結果)
    differences: list[str]  # 前提条件の違い（空なら同じ前提で比べている）


def compare(items: list[tuple[str, SimulationInputs]]) -> Comparison:
    """複数の改善案の試算を並べ、前提条件（現状の業務の値）の違いを示す。"""
    differences = []
    for name in BASELINE_FIELDS:
        values = [(title, getattr(inputs, name)) for title, inputs in items]
        if len({v for _, v in values}) > 1:
            shown = "、".join(f"{title}：{_show(name, v)}" for title, v in values)
            title_of_field = "業務名" if name == "task_name" else label(name)
            differences.append(f"{title_of_field}が改善案によって異なります（{shown}）")
    return Comparison(rows=[(title, simulate(inputs)) for title, inputs in items], differences=differences)


def _show(name: str, value) -> str:
    if value is None:
        return "未入力"
    return str(value) if name == "task_name" else fmt(value)
