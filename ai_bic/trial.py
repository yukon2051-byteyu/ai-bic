"""確定した案を小さく試し、測った結果から次の判断をする（AI-BIC 3.0 第4段階）。AIは使わない。

- 計画：確定した案の想定リスクと、確定した時点の試算の前提を引き継ぎ、試行範囲・担当・期限・測定項目を決める
- 測定：数値の項目は「改善前の値・試算値・目標値・実測値」を別々に持ち、差と合格条件への到達をプログラムが計算する。
  確認の項目（例外処理の成功・失敗など）は、合格条件の文と結果を持つ。未測定は未測定と示す
- 判断：目安（すべて合格→展開する、一部だけ合格→修正して再試行、合格なし→見送る）を示し、利用者が選んで理由を残す。
  必須の測定が未入力なら「判定保留」とし、判断を記録させない
- 単位（AI-BIC 3.3）：数値の項目は「単位／何あたり」（例：分／稼働日）と出どころを持つ。診断の数値の一覧から
  引き継いだ単位を使い、単位が未設定・未確認（古い案件など）・確かめた後に変わったときは、差も判定も計算せず理由を示す
"""

from dataclasses import dataclass
from datetime import timedelta

from ai_bic import facts
from ai_bic import project as pj
from ai_bic import simulation as sim
from ai_bic.output_check import similarity
from ai_bic.overview import signature
from ai_bic.project import Project, TrialMetric, TrialPlan
from ai_bic.quantities import basis_text
from ai_bic.quantities import label as qlabel

DECISIONS = ["展開する", "修正して再試行", "見送る"]
HOLD = "判定保留"
UNCONFIRMED = "単位を確認"


# ---------------------------------------------------------------
# 単位（AI-BIC 3.3）
# ---------------------------------------------------------------
def basis_of(m: TrialMetric) -> str:
    """測定項目の今の「単位／何あたり」（例：分／稼働日）。"""
    return basis_text(m.unit, m.per)


def metric_label(m: TrialMetric) -> str:
    """画面・Excel で使う名前。例：閉館前の照合時間（分／稼働日）。確認の項目は名前だけ。"""
    return qlabel(m.name, m.unit, m.per) if m.kind == "数値" else m.name


def basis_issue(m: TrialMetric) -> str:
    """単位の問題（差や判定を計算しない理由）。問題がなければ空。"""
    if m.kind != "数値":
        return ""
    if not m.unit.strip():
        return "単位を確認してください（単位が未設定です）"
    if m.checked_basis is None:
        return "単位を確認してください（値の単位と集計期間を確かめていません）"
    if m.checked_basis != basis_of(m):
        return f"単位が変わりました（{m.checked_basis} → {basis_of(m)}）。値が今の単位で正しいか再確認が必要です"
    return ""


def has_values(m: TrialMetric) -> bool:
    return any(v is not None for v in (m.baseline, m.estimate, m.target, m.actual))


# ---------------------------------------------------------------
# 計画
# ---------------------------------------------------------------
@dataclass
class SourceValue:
    """試算の元の値に当たる、診断の数値（名前・単位・何あたり・値・出どころ）。"""

    name: str
    unit: str
    per: str
    value: float | None
    source: str
    status: str


def source_value(project: Project | None, field: str) -> SourceValue | None:
    """試算の入力値（例：minutes_per_case）の元になった、数値の一覧の値。一覧がない古い案件は None。"""
    if project is None or not project.diagnosis.analysis.quantities:
        return None
    known = facts.known_values(project)
    for q in project.diagnosis.analysis.quantities:
        if q.sim_field == field:
            k = known[q.qid]
            return SourceValue(q.name, q.unit, q.per or "", k.value, k.source, k.status)
    for d in facts.derived_values(project):
        if d.sim_field == field:
            ok = d.calc.value is not None
            return SourceValue(d.name, d.unit, d.per or "", round(d.calc.value, 1) if ok else None,
                               f"計算：{d.calc.text}" if ok else "", "確認済み" if ok else "未確定")
    return None


def assumptions_of(inputs: sim.SimulationInputs | None, project: Project | None = None) -> list[str]:
    """試算の前提（値・単位・何あたり・出どころ）。

    数値の一覧がある案件は、一覧の名前と単位で示し（例：閉館前の照合時間（分／稼働日）：15分（2問目の回答））、
    計算した値と未確定の値も同じ文で示す。一覧にない試算の値（人件費・削減率など）は、試算の名前で示す。
    """
    lines: list[str] = []
    covered: dict[str, float] = {}
    if project is not None and project.diagnosis.analysis.quantities:
        known = facts.known_values(project)
        for q in project.diagnosis.analysis.quantities:
            k = known[q.qid]
            name = facts.prose_name(q.name, q.unit, q.per)
            if k.value is not None:
                lines.append(f"{name}：{sim.fmt(k.value)}{q.unit}（{k.source}）")
            elif k.status != "確認予定":  # 確認予定は、下の計算の前提の文に含まれる
                lines.append(f"{name}：{k.status}")
        lines += facts.assumption_lines(project)
        covered = {f: v for f, (v, _) in facts.quantity_basis(project).items()}
    if inputs is None:
        return lines
    for name in ("minutes_per_case", "cases_per_month", "hourly_cost", "reduction_rate"):
        value = getattr(inputs, name)
        if value is not None and covered.get(name) != value:
            lines.append(f"{sim.label(name)}：{sim.fmt(value)}（{inputs.source_of(name)}）")
    return lines


def time_metric(project: Project | None, inputs: sim.SimulationInputs | None) -> TrialMetric:
    """作業時間の測定項目。診断の数値（試算の1件あたりの時間の元）の名前・単位・何あたり・出どころを引き継ぐ。

    例：閉館前の照合時間（分／稼働日）・改善前 15分・2問目の回答。数値の一覧がない古い案件は、試算の「1件あたり」。
    何あたりが分からない数値（3.2以前のAIの出力）は、単位を確かめるまで判定に使わない。
    """
    minutes = inputs.minutes_per_case if inputs else None
    rate = inputs.reduction_rate if inputs else None
    src = source_value(project, "minutes_per_case")
    if src is None:
        estimate = round(minutes * (1 - rate / 100), 1) if minutes is not None and rate is not None else None
        return TrialMetric(name="1件あたりの作業時間", unit="分", per="件", baseline=minutes, estimate=estimate,
                           target=estimate, source=inputs.source_of("minutes_per_case") if minutes is not None else "",
                           checked_basis="分／件")
    m = TrialMetric(name=src.name, unit=src.unit, per=src.per, baseline=src.value, source=src.source,
                    checked_basis=basis_text(src.unit, src.per) if src.per else None)
    if src.value is None:
        m.note = f"改善前の値は{src.status}です"
    elif rate is not None and minutes is not None:
        if minutes == src.value:  # 試算の値が診断の値と同じ（同じ単位）ときだけ、試算値を入れる
            m.estimate = m.target = round(minutes * (1 - rate / 100), 1)
        else:
            m.note = f"試算の1件あたりの時間（{sim.fmt(minutes)}分）は改善前の値と違うため、試算値は入れていません"
    return m


def default_metrics(project: Project | None, inputs: sim.SimulationInputs | None,
                    effect_metric: str | None = None) -> list[TrialMetric]:
    """最初の測定項目：その案の効果を測る指標（あれば）、作業時間（改善前・試算値つき）、誤りの件数、例外処理の確認。

    作業時間の短縮を直接の目的としない案（例：記入漏れを減らす）は、その案の指標を先頭の必須の項目にし、
    作業時間は参考の項目にする（AI-BIC 3.1）。
    """
    time = time_metric(project, inputs)
    rest = [TrialMetric(name="誤り・差異の件数", unit="件", per="試行期間", target=0, checked_basis="件／試行期間"),
            TrialMetric(name="例外の処理（想定外の形式など）", kind="確認", check_target="すべて処理できる")]
    metric = (effect_metric or "").strip()
    if not metric or "作業時間" in metric:
        return [time] + rest
    # その案の指標を必須に、作業時間は参考にする（誤りの件数の一般的な項目は、指標と重なりやすいので入れない）
    return [TrialMetric(name=metric), time.model_copy(update={"required": False}), rest[1]]


def same_metric(a: str, b: str) -> bool:
    """測定項目の名前が同じものを指すか（重複して追加しないため）。

    文がほぼ同じか、単位（日・件など）と期間（月など）が同じで内容の言葉が重なれば同じ
    （例：「月の記入漏れの日数」と「返却時刻の記入漏れの月間発生日数」。不足情報の統合と同じ考え方）。
    """
    x, y = "".join(a.split()), "".join(b.split())
    ua, pa, wa = signature(x)
    ub, pb, wb = signature(y)
    if ua and ub and ua != ub:
        return False  # 単位が違えば、文が似ていても別の項目（例：日数と件数）
    if x in y or y in x or similarity(x, y) >= 0.5:
        return True
    return bool(ua) and ua == ub and pa == pb and any(s in t or t in s for s in wa for t in wb)


def add_metric_unique(plan: TrialPlan, metric: TrialMetric) -> bool:
    """測定項目を追加する。既にある項目と同じなら追加せず、確認予定の内容と単位だけを書き足して False。

    何あたりが両方にあって違う（例：日／月 と 日／週）なら、別の項目として追加する。
    """
    twin = next((m for m in plan.metrics if same_metric(m.name, metric.name)
                 and not (m.per and metric.per and m.per != metric.per)), None)
    if twin is None:
        plan.metrics.append(metric)
        return True
    i = plan.metrics.index(twin)
    note = twin.note if not metric.note or metric.note in twin.note else "　".join(x for x in (twin.note, metric.note) if x)
    update: dict = {"note": note}
    if not twin.unit and metric.unit:  # 単位のない項目に、確認予定の値の単位と何あたりを引き継ぐ
        update.update(unit=metric.unit, per=twin.per or metric.per,
                      checked_basis=twin.checked_basis if has_values(twin) else metric.checked_basis)
    plan.metrics[i] = twin.model_copy(update=update)
    return False


def planned_metrics(project: Project) -> list[TrialMetric]:
    """「確認予定」の値を、試行で測る項目にする（例：記入漏れの月間発生日数を、1か月記録して確かめる。AI-BIC 3.2）。"""
    metrics = [TrialMetric(name=n.name, unit=n.unit, per=n.per or "", note=f"確認予定：{n.detail}",
                           checked_basis=basis_text(n.unit, n.per) if n.per else None)
               for n in facts.needs(project) if n.status == "確認予定"]
    metrics += [TrialMetric(name=a.question, kind="確認", check_target="確かめた結果を記録する", note=f"確認予定：{a.answer}")
                for key, a in project.gap_answers.items() if a.kind == "確認予定" and not key.startswith("fact:")]
    return metrics


def new_plan(project: Project) -> TrialPlan:
    """確定した案の試行計画を作る（確定した時点の改善案・試算から引き継ぐ）。"""
    d = project.decision
    pid = project.selected_proposal
    if pid is None:
        raise ValueError("改善案が確定していません")
    proposal = d.proposal if d else project.diagnosis.proposals.proposals[pj.proposal_ids(project).index(pid)]
    inputs = d.simulation if d else project.simulations.get(pid)
    plan = TrialPlan(pid=pid, risks=list(proposal.risks), assumptions=assumptions_of(inputs, project),
                     metrics=default_metrics(project, inputs, proposal.effect_metric), created_at=pj.now())
    for metric in planned_metrics(project):  # 確認予定の値も、試行で測る（既にある項目とは重ねない）
        add_metric_unique(plan, metric)
    return plan


def demo_plan(project: Project, sample: dict) -> TrialPlan:
    """デモの試行のサンプル（シナリオの trial_sample。数値はデモ用）。"""
    plan = new_plan(project)
    plan.scope, plan.owner = sample["scope"], sample["owner"]
    plan.due = pj.now().date() + timedelta(days=sample.get("due_days", 14))
    plan.metrics = [TrialMetric(**m) for m in sample["metrics"]]
    for m in plan.metrics:  # デモのサンプルの単位は、確かめた値として扱う
        if m.kind == "数値" and m.unit and m.checked_basis is None:
            m.checked_basis = basis_of(m)
    plan.sample = True
    return plan


# ---------------------------------------------------------------
# 測定の評価
# ---------------------------------------------------------------
@dataclass
class MetricResult:
    status: str  # 未測定・達成・未達・目標未設定・単位を確認
    diff: float | None = None  # 実測値 − 改善前の値
    diff_text: str = ""  # 差の式。単位を確認するときは、その理由

    @property
    def mark(self) -> str:  # 色だけで区別しないよう記号も付ける
        return {"達成": "✓", "未達": "✕", "未測定": "？", "目標未設定": "－", UNCONFIRMED: "！"}[self.status]


def evaluate(m: TrialMetric) -> MetricResult:
    if m.kind == "確認":
        if m.check_result is None:
            return MetricResult("未測定")
        return MetricResult("達成" if m.check_result == "成功" else "未達")
    if issue := basis_issue(m):  # 単位がそろっていない・確かめていない値で、差や判定を出さない
        return MetricResult(UNCONFIRMED, None, issue)
    if m.actual is None:
        return MetricResult("未測定")
    diff, text = None, ""
    if m.baseline is not None:
        diff = m.actual - m.baseline
        text = f"{sim.fmt(m.actual)} − {sim.fmt(m.baseline)} = {'+' if diff > 0 else ''}{sim.fmt(diff)}{basis_of(m)}"
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
    """判定の目安（決めるのは利用者）。必須の項目が未入力・単位が未確認なら、その項目を挙げて判定保留にする。"""
    results = [(m, evaluate(m)) for m in plan.metrics]
    missing = [metric_label(m) for m, r in results if m.required and r.status in ("未測定", "目標未設定")]
    unconfirmed = [metric_label(m) for m, r in results if m.required and r.status == UNCONFIRMED]
    if not plan.metrics:
        return Guide(HOLD, "測定項目がありません。測定項目を追加してください")
    if missing or unconfirmed:
        parts = [f"必須の測定が未入力です（{'・'.join(missing)}）"] if missing else []
        parts += [f"単位の確認が必要です（{'・'.join(unconfirmed)}）"] if unconfirmed else []
        return Guide(HOLD, "。".join(parts))
    judged = [(m, r) for m, r in results if r.status in ("達成", "未達")]
    passed = [metric_label(m) for m, r in judged if r.status == "達成"]
    failed = [metric_label(m) for m, r in judged if r.status == "未達"]
    if not failed:
        return Guide("展開する", "すべての合格条件を満たしました")
    if passed:
        return Guide("修正して再試行", f"{'・'.join(passed)}は合格しましたが、{'・'.join(failed)}が合格条件に届いていません")
    return Guide("見送る", f"合格した項目がありません（{'・'.join(failed)}）。案そのものを見直すことも検討してください")


def record_decision(plan: TrialPlan, decision: str, reason: str) -> None:
    """利用者の判断と理由を記録する。必須の測定が未入力・単位が未確認（判定保留）なら記録しない。"""
    if decision not in DECISIONS:
        raise ValueError("判断を選んでください")
    if (g := guide(plan)).hold:
        raise ValueError(f"判定保留です（{g.reason}）。測定結果と単位を確かめてから判断してください")
    if not reason.strip():
        raise ValueError("判断の理由を入力してください")
    plan.decision, plan.reason, plan.decided_at = decision, reason.strip(), pj.now()
