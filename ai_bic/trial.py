"""確定した案を小さく試し、測った結果から次の判断をする（AI-BIC 3.0 第4段階）。AIは使わない。

- 計画：確定した案の想定リスクと、確定した時点の試算の前提を引き継ぎ、試行範囲・担当・期限・測定項目を決める
- 測定：数値の項目は「改善前の値・試算値・目標値・実測値」を別々に持ち、差と合格条件への到達をプログラムが計算する。
  確認の項目（例外処理の成功・失敗など）は、合格条件の文と結果を持つ。未測定は未測定と示す
- 判断：目安（すべて合格→展開する、一部だけ合格→修正して再試行、合格なし→見送る）を示し、利用者が選んで理由を残す。
  必須の測定が未入力なら「判定保留」とし、判断を記録させない
"""

from dataclasses import dataclass
from datetime import timedelta

from ai_bic import project as pj
from ai_bic import simulation as sim
from ai_bic.project import Project, TrialMetric, TrialPlan

DECISIONS = ["展開する", "修正して再試行", "見送る"]
HOLD = "判定保留"


# ---------------------------------------------------------------
# 計画
# ---------------------------------------------------------------
def assumptions_of(inputs: sim.SimulationInputs | None) -> list[str]:
    """試算の前提（確定した時点の値と出どころ）。"""
    if inputs is None:
        return []
    lines = []
    for name in ("minutes_per_case", "cases_per_month", "hourly_cost", "reduction_rate"):
        value = getattr(inputs, name)
        if value is not None:
            lines.append(f"{sim.label(name)}：{sim.fmt(value)}（{inputs.source_of(name)}）")
    return lines


def default_metrics(inputs: sim.SimulationInputs | None, effect_metric: str | None = None) -> list[TrialMetric]:
    """最初の測定項目：その案の効果を測る指標（あれば）、1件あたりの作業時間（改善前・試算値つき）、誤りの件数、例外処理の確認。

    作業時間の短縮を直接の目的としない案（例：記入漏れを減らす）は、その案の指標を先頭の必須の項目にし、
    作業時間は参考の項目にする（AI-BIC 3.1）。
    """
    minutes = inputs.minutes_per_case if inputs else None
    rate = inputs.reduction_rate if inputs else None
    estimate = round(minutes * (1 - rate / 100), 1) if minutes is not None and rate is not None else None
    time_metric = TrialMetric(name="1件あたりの作業時間", unit="分", baseline=minutes, estimate=estimate, target=estimate)
    rest = [TrialMetric(name="誤り・差異の件数", unit="件", target=0),
            TrialMetric(name="例外の処理（想定外の形式など）", kind="確認", check_target="すべて処理できる")]
    metric = (effect_metric or "").strip()
    if not metric or "作業時間" in metric:
        return [time_metric] + rest
    # その案の指標を必須に、作業時間は参考にする（誤りの件数の一般的な項目は、指標と重なりやすいので入れない）
    return [TrialMetric(name=metric), time_metric.model_copy(update={"required": False}), rest[1]]


def new_plan(project: Project) -> TrialPlan:
    """確定した案の試行計画を作る（確定した時点の改善案・試算から引き継ぐ）。"""
    d = project.decision
    pid = project.selected_proposal
    if pid is None:
        raise ValueError("改善案が確定していません")
    proposal = d.proposal if d else project.diagnosis.proposals.proposals[pj.proposal_ids(project).index(pid)]
    inputs = d.simulation if d else project.simulations.get(pid)
    return TrialPlan(pid=pid, risks=list(proposal.risks), assumptions=assumptions_of(inputs),
                     metrics=default_metrics(inputs, proposal.effect_metric), created_at=pj.now())


def demo_plan(project: Project, sample: dict) -> TrialPlan:
    """デモの試行のサンプル（シナリオの trial_sample。数値はデモ用）。"""
    plan = new_plan(project)
    plan.scope, plan.owner = sample["scope"], sample["owner"]
    plan.due = pj.now().date() + timedelta(days=sample.get("due_days", 14))
    plan.metrics = [TrialMetric(**m) for m in sample["metrics"]]
    plan.sample = True
    return plan


# ---------------------------------------------------------------
# 測定の評価
# ---------------------------------------------------------------
@dataclass
class MetricResult:
    status: str  # 未測定・達成・未達・目標未設定
    diff: float | None = None  # 実測値 − 改善前の値
    diff_text: str = ""

    @property
    def mark(self) -> str:  # 色だけで区別しないよう記号も付ける
        return {"達成": "✓", "未達": "✕", "未測定": "？", "目標未設定": "－"}[self.status]


def evaluate(m: TrialMetric) -> MetricResult:
    if m.kind == "確認":
        if m.check_result is None:
            return MetricResult("未測定")
        return MetricResult("達成" if m.check_result == "成功" else "未達")
    if m.actual is None:
        return MetricResult("未測定")
    diff, text = None, ""
    if m.baseline is not None:
        diff = m.actual - m.baseline
        text = f"{sim.fmt(m.actual)} − {sim.fmt(m.baseline)} = {'+' if diff > 0 else ''}{sim.fmt(diff)}{m.unit}"
        if m.baseline != 0:
            text += f"（{'+' if diff > 0 else ''}{sim.fmt(diff / m.baseline * 100)}%）"
    if m.target is None:
        return MetricResult("目標未設定", diff, text)
    ok = m.actual <= m.target if m.direction == "以下" else m.actual >= m.target
    return MetricResult("達成" if ok else "未達", diff, text)


@dataclass
class Guide:
    label: str  # 判定保留・展開する・修正して再試行・見送る
    reason: str

    @property
    def hold(self) -> bool:
        return self.label == HOLD


def guide(plan: TrialPlan) -> Guide:
    """判定の目安（決めるのは利用者）。"""
    results = [(m, evaluate(m)) for m in plan.metrics]
    missing = [m.name for m, r in results if m.required and r.status in ("未測定", "目標未設定")]
    if not plan.metrics:
        return Guide(HOLD, "測定項目がありません。測定項目を追加してください")
    if missing:
        return Guide(HOLD, f"必須の測定が未入力です（{'・'.join(missing)}）")
    judged = [(m, r) for m, r in results if r.status in ("達成", "未達")]
    passed = [m.name for m, r in judged if r.status == "達成"]
    failed = [m.name for m, r in judged if r.status == "未達"]
    if not failed:
        return Guide("展開する", "すべての合格条件を満たしました")
    if passed:
        return Guide("修正して再試行", f"{'・'.join(passed)}は合格しましたが、{'・'.join(failed)}が合格条件に届いていません")
    return Guide("見送る", f"合格した項目がありません（{'・'.join(failed)}）。案そのものを見直すことも検討してください")


def record_decision(plan: TrialPlan, decision: str, reason: str) -> None:
    """利用者の判断と理由を記録する。必須の測定が未入力（判定保留）なら記録しない。"""
    if decision not in DECISIONS:
        raise ValueError("判断を選んでください")
    if guide(plan).hold:
        raise ValueError("必須の測定が未入力のため、判定保留です。測定結果を入力してから判断してください")
    if not reason.strip():
        raise ValueError("判断の理由を入力してください")
    plan.decision, plan.reason, plan.decided_at = decision, reason.strip(), pj.now()
