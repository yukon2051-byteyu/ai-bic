"""不足情報の確認で得た情報を記録し、関係する結果だけを更新する（AI-BIC 3.0 第3段階）。AIは使わない。

【数値】
- 確認した数値（月の作業時間の合計・その内訳・月の件数・時間あたり人件費）を、出どころ（本人の申告・添付資料）ごとに持つ
- 申告と資料の値が食い違うときは、どちらも使わず、両方の値と出どころを示して確認を求める
- 計算はプログラムが行う（例：12 ÷ 20 × 100 = 60%）。値が欠けている・分母が0・内訳が合計を上回るときは計算しない
- 試算の元の値（1件あたりの時間・月の件数・人件費）が変わったら、その試算だけを計算し直す。
  利用者が試算の画面で手入力した値は残し、元の値が変わったことを記録する（basis_changes）

【文章の回答】
- 不足情報の key ごとに、回答と出どころを残す。影響する改善案の「前提」の版を上げ、過去のレビューに再確認を求める

【確定した案】
- 確定した時点の改善案・試算を残す。そのあと改善案・前提・試算が変わったら、再確認が必要と示す
"""

import ast
import re
from dataclasses import dataclass

from ai_bic import project as pj
from ai_bic import simulation as sim
from ai_bic.project import DecisionRecord, FactEntry, GapAnswer, Project, Source
from ai_bic.quantities import Calc as QCalc
from ai_bic.quantities import evaluate as evaluate_formula

# 確認する数値：名前 → (画面の名前, 単位, 上限)
FACTS = {
    "total_hours": ("月の作業時間の合計", "時間", 10_000.0),
    "part_hours": ("うち、最も時間がかかる作業の時間", "時間", 10_000.0),
    "cases_per_month": ("月の処理件数（試算の「1件」の数）", "件", 1_000_000.0),
    "hourly_cost": ("時間あたり人件費", "円", 100_000.0),
}


# ---------------------------------------------------------------
# 数値の記録と、使う値の決め方
# ---------------------------------------------------------------
@dataclass
class FactState:
    value: float | None  # 使う値。未確認・食い違いのときは None
    source: Source | None
    entries: list[FactEntry]
    conflict: bool  # 申告と資料の値が食い違っている

    @property
    def conflict_text(self) -> str:
        return "、".join(f"{e.source}：{sim.fmt(e.value)}" + (f"（{e.note}）" if e.note else "") for e in self.entries)


def state_of(project: Project, name: str) -> FactState:
    entries = project.facts.get(name, [])
    if not entries:
        return FactState(None, None, [], False)
    values = {e.value for e in entries}
    if len(values) > 1:
        return FactState(None, None, entries, True)
    # 同じ値なら、資料で確かめたことを優先して示す
    source = "添付資料" if any(e.source == "添付資料" for e in entries) else "本人の申告"
    return FactState(entries[0].value, source, entries, False)


def record_fact(project: Project, name: str, value: float, source: Source, note: str = "") -> bool:
    """数値を1つ記録する（同じ出どころの古い値は置き換える）。使う値が変わったら True。"""
    if name not in FACTS and name not in {q.qid for q in project.diagnosis.analysis.quantities}:
        raise ValueError(f"不明な項目です：{name}")
    if value < 0:
        raise ValueError("0以上の値を入力してください")
    before = state_of(project, name)
    others = [e for e in project.facts.get(name, []) if e.source != source]
    project.facts[name] = others + [FactEntry(value=value, source=source, note=note.strip(), at=pj.now())]
    after = state_of(project, name)
    return (before.value, before.conflict) != (after.value, after.conflict)


# ---------------------------------------------------------------
# 計算（プログラムが行う）
# ---------------------------------------------------------------
@dataclass
class Calc:
    value: float | None
    formula: str  # 例：12 ÷ 20 × 100 = 60%
    message: str  # 計算できないときの理由（要確認として示す）


def ratio(total: float | None, part: float | None) -> Calc:
    """内訳が合計に占める割合（%）。"""
    if total is None or part is None:
        missing = "・".join(n for n, v in [("合計", total), ("内訳", part)] if v is None)
        return Calc(None, "", f"要確認：{missing}が未確認のため、割合は計算していません")
    if total == 0:
        return Calc(None, "", "合計が0のため計算できません（分母が0）。入力を確認してください")
    if part > total:
        return Calc(None, "", f"内訳（{sim.fmt(part)}）が合計（{sim.fmt(total)}）を上回っています。入力を確認してください")
    value = part / total * 100
    return Calc(value, f"{sim.fmt(part)} ÷ {sim.fmt(total)} × 100 = {sim.fmt(value)}%", "")


def minutes_per_case(total_hours: float | None, cases: float | None) -> Calc:
    """1件あたりの作業時間（分）＝月の作業時間の合計 × 60 ÷ 月の件数。"""
    if total_hours is None or cases is None:
        return Calc(None, "", "")  # どちらかが未確認なら、試算の元の値は変えない
    if cases == 0:
        return Calc(None, "", "月の件数が0のため、1件あたりの時間は計算できません（分母が0）。入力を確認してください")
    value = total_hours * 60 / cases
    return Calc(value, f"{sim.fmt(total_hours)}時間 × 60 ÷ {sim.fmt(cases)}件 = {sim.fmt(value)}分", "")


# ---------------------------------------------------------------
# 試算への反映（関係する試算だけ）
# ---------------------------------------------------------------
def basis(project: Project) -> dict[str, tuple[float, str]]:
    """確認した数値から決まる、試算の元の値（項目名 → (値, 出どころ)）。決まらない項目は含めない。

    数値の一覧がある案件（AI-BIC 3.1）は、一覧の値（照合済み）と、利用者が確認した値から決める。
    一覧がない古い案件は、不足情報で確認した4つの数値（合計・内訳・件数・人件費）から決める。
    """
    if project.diagnosis.analysis.quantities:
        return quantity_basis(project)
    states = {name: state_of(project, name) for name in FACTS}
    result: dict[str, tuple[float, Source]] = {}
    total, cases = states["total_hours"], states["cases_per_month"]
    per_case = minutes_per_case(total.value, cases.value)
    if per_case.value is not None:
        weak = "本人の申告" if "本人の申告" in (total.source, cases.source) else "添付資料"
        result["minutes_per_case"] = (round(per_case.value, 1), weak)
    for name in ("cases_per_month", "hourly_cost"):
        if states[name].value is not None:
            result[name] = (states[name].value, states[name].source)
    return result


# ---------------------------------------------------------------
# 数値の一覧（AI-BIC 3.1）：本文・不足情報・試算で、同じ値と出どころを使う
# ---------------------------------------------------------------
SOURCE_LABEL = {"業務情報": "業務情報", "回答": "回答", "添付ファイル": "添付ファイル"}


@dataclass
class Known:
    value: float | None
    status: str  # 確認済み・未集計・未確認・食い違い
    source: str  # 表示用の出どころ（例：業務情報／2問目の回答／本人の申告）
    sim_source: str | None  # 試算の出どころ（ヒアリング・添付資料・本人の申告）


def known_values(project: Project) -> dict[str, Known]:
    """数値の一覧の番号 → 今の値。利用者が結果画面で確認した値があれば、それを使う（AIの値より優先）。"""
    result = {}
    for q in project.diagnosis.analysis.quantities:
        user = state_of(project, q.qid)
        if user.conflict:
            result[q.qid] = Known(None, "食い違い", user.conflict_text, None)
        elif user.value is not None:
            result[q.qid] = Known(user.value, "確認済み", user.source, user.source)
        elif q.status == "確認済み" and q.value is not None:
            answer = "サンプル回答" if project.partial_hearing else "回答"  # デモを途中で終えた場合は、サンプル回答と照合
            where = f"{q.turn_no}問目の{answer}" if q.source == "回答" and q.turn_no else SOURCE_LABEL.get(q.source, "")
            sim_source = "添付資料" if q.source == "添付ファイル" else "ヒアリング"
            result[q.qid] = Known(q.value, "確認済み", where, sim_source)
        else:
            result[q.qid] = Known(None, q.status, "", None)
    return result


@dataclass
class DerivedValue:
    qid: str
    name: str
    unit: str
    calc: QCalc
    missing_names: list[str]
    sim_field: str | None
    sim_source: str | None


def derived_values(project: Project) -> list[DerivedValue]:
    """計算する値（式の順に。前の計算結果も使える）。足りない値があれば、その名前を返す。"""
    analysis = project.diagnosis.analysis
    known = known_values(project)
    values = {k: v.value for k, v in known.items()}
    sources = {k: v.sim_source for k, v in known.items()}
    units = {q.qid: q.unit for q in analysis.quantities}
    names = {q.qid: q.name for q in analysis.quantities}
    result = []
    for d in analysis.derived:
        calc = evaluate_formula(d.formula, d.unit, values, units)
        if calc.value is not None and d.unit == "%" and calc.value > 100:
            # 割合が100%を超えるのは、内訳が合計を上回っているとき。誤った値を表示・使用しない
            calc = QCalc(None, "", [], "割合が100%を超えています（内訳が合計を上回っています）。入力を確認してください")
        used = [n.id for n in _names(d.formula)]
        weak = [sources.get(i) for i in used]
        sim_source = None if calc.value is None else (
            "本人の申告" if "本人の申告" in weak else "ヒアリング" if "ヒアリング" in weak else "添付資料")
        result.append(DerivedValue(d.qid, d.name, d.unit, calc, [names.get(m, m) for m in calc.missing],
                                   d.sim_field, sim_source))
        values[d.qid], units[d.qid], names[d.qid], sources[d.qid] = calc.value, d.unit, d.name, sim_source
    return result


def _names(formula: str):
    return [n for n in ast.walk(ast.parse(formula, mode="eval")) if isinstance(n, ast.Name)]


def quantity_basis(project: Project) -> dict[str, tuple[float, str]]:
    """数値の一覧から決まる、試算の元の値。人件費は、一覧になくても結果画面で確認した値を使う。"""
    result: dict[str, tuple[float, str]] = {}
    known = known_values(project)
    for q in project.diagnosis.analysis.quantities:
        k = known[q.qid]
        if q.sim_field and k.value is not None:
            result[q.sim_field] = (k.value, k.sim_source)
    for d in derived_values(project):
        if d.sim_field and d.calc.value is not None:
            result[d.sim_field] = (round(d.calc.value, 1), d.sim_source)
    hourly = state_of(project, "hourly_cost")
    if "hourly_cost" not in result and hourly.value is not None:
        result["hourly_cost"] = (hourly.value, hourly.source)
    return result


@dataclass
class Need:
    """不足情報欄で確認する数値（改善案の効果の判断や試算に必要で、まだ分からないもの）。"""

    key: str  # Q の番号、または hourly_cost
    name: str
    unit: str
    status: str  # 未集計・未確認・食い違い
    detail: str  # 食い違いの内容など
    proposals: list[str]  # この値を使う改善案（案1 など）
    blocked: list[str]  # この値が分かるまで未確定の、計算する値の名前
    for_simulation: bool  # 試算の入力値に使う


def needs(project: Project) -> list[Need]:
    """確認が必要な数値。全案件に同じ項目を求めず、改善案の効果や試算に必要な値だけを挙げる。"""
    analysis = project.diagnosis.analysis
    if not analysis.quantities:
        return []
    proposals = project.diagnosis.proposals.proposals if project.diagnosis.proposals else []
    formulas = {d.qid: {n.id for n in _names(d.formula)} for d in analysis.derived}

    def expand(ids: set[str]) -> set[str]:
        out = set(ids)
        for i in ids:
            out |= expand(formulas[i]) if i in formulas else set()
        return out

    used_by: dict[str, list[str]] = {}
    for no, p in enumerate(proposals, start=1):
        for i in expand(set(p.effect_inputs)):
            used_by.setdefault(i, []).append(f"案{no}")
    sim_ids = expand({q.qid for q in analysis.quantities if q.sim_field} | {d.qid for d in analysis.derived if d.sim_field})
    known = known_values(project)
    blocked_by: dict[str, list[str]] = {}
    for d in derived_values(project):
        for name in d.missing_names:
            blocked_by.setdefault(name, []).append(d.name)
    result = []
    for q in analysis.quantities:
        k = known[q.qid]
        if k.value is not None or (q.qid not in used_by and q.qid not in sim_ids):
            continue
        result.append(Need(q.qid, q.name, q.unit, k.status, k.source if k.status == "食い違い" else "",
                           used_by.get(q.qid, []), blocked_by.get(q.name, []), q.qid in sim_ids))
    if "hourly_cost" not in quantity_basis(project):
        result.append(Need("hourly_cost", "時間あたり人件費", "円", "未確認", "", [], [], True))
    return result


@dataclass
class Change:
    pid: str
    field: str
    value: float
    applied: bool  # False：手入力の値を残した（元の値が変わったことだけ記録）


def apply_to_simulations(project: Project) -> list[Change]:
    """確認した数値を試算の元の値に反映する。値が変わった案の試算だけ計算し直す（版を上げる）。"""
    changes: list[Change] = []
    targets = basis(project)
    for pid in pj.proposal_ids(project):
        current = project.simulations.get(pid)
        if current is None:
            continue
        updates, sources = {}, dict(current.sources)
        notes = project.basis_changes.setdefault(pid, {})
        for field, (value, source) in targets.items():
            now, now_source = getattr(current, field), current.source_of(field)
            if now_source == "ユーザー入力" and now is not None:
                # 利用者が試算で手入力した値は残す。元の値が変わったことだけを記録する
                if now != value and notes.get(field) != value:
                    notes[field] = value
                    changes.append(Change(pid, field, value, applied=False))
                elif now == value:
                    notes.pop(field, None)
                continue
            if now != value or now_source != source:
                updates[field], sources[field] = value, source
                notes.pop(field, None)
                changes.append(Change(pid, field, value, applied=True))
        if not notes:
            project.basis_changes.pop(pid, None)
        if updates:
            new, errors = sim.make_inputs({**current.model_dump(), **updates, "sources": sources})
            if errors:  # 上限を超えるなど。試算は変えずに知らせる
                raise ValueError("、".join(errors.values()))
            project.simulations[pid] = new
            pj.mark_updated(project, "simulation", pid)
    return changes


def restore_basis(project: Project, pid: str, field: str) -> None:
    """手入力の値をやめて、確認した値に戻す。"""
    value = project.basis_changes.get(pid, {}).pop(field)
    _, source = basis(project)[field]
    current = project.simulations[pid]
    new, errors = sim.make_inputs({**current.model_dump(), field: value,
                                   "sources": {**current.sources, field: source}})
    if errors:
        raise ValueError("、".join(errors.values()))
    project.simulations[pid] = new
    if not project.basis_changes[pid]:
        project.basis_changes.pop(pid)
    pj.mark_updated(project, "simulation", pid)


def mark_premise(project: Project, pids: list[str]) -> None:
    """改善案の前提（確認した情報）が変わったことを記録する。過去のレビューは「再レビューが必要」になる。"""
    for pid in pids:
        pj.mark_updated(project, "premise", pid)


# ---------------------------------------------------------------
# 文章の回答
# ---------------------------------------------------------------
def affected_pids(project: Project, affects: list[str]) -> list[str]:
    """影響するものの一覧（「案2」など）から改善案IDを取り出す。案の名前がなければ、すべての案。"""
    ids = pj.proposal_ids(project)
    named = [f"P{m}" for a in affects for m in re.findall(r"^案(\d+)$", a)]
    return [pid for pid in ids if pid in named] or ids


def answer_gap(project: Project, key: str, question: str, answer: str, source: Source,
               affects: list[str], note: str = "") -> list[str]:
    """不足情報に回答する。影響する改善案の前提を更新し、その改善案IDを返す。"""
    text = answer.strip()
    if not text:
        raise ValueError("回答を入力してください")
    project.gap_answers[key] = GapAnswer(question=question, answer=text, source=source, note=note.strip(),
                                         affects=affects, at=pj.now())
    pids = affected_pids(project, affects)
    mark_premise(project, pids)
    return pids


# ---------------------------------------------------------------
# 確定した案
# ---------------------------------------------------------------
DECISION_KINDS = {"proposal": "改善案", "premise": "前提", "simulation": "試算"}


def decide(project: Project, pid: str) -> None:
    """改善案を確定する。確定した時点の内容と版を残す。"""
    no = pj.proposal_ids(project).index(pid)
    project.selected_proposal = pid
    project.decision = DecisionRecord(
        pid=pid, proposal=project.diagnosis.proposals.proposals[no].model_copy(deep=True),
        simulation=project.simulations[pid].model_copy(deep=True) if pid in project.simulations else None,
        versions={kind: pj.version(project, kind, pid) for kind in DECISION_KINDS}, decided_at=pj.now())


def decision_changes(project: Project) -> list[str]:
    """確定した後に変わったもの（「前提」など）。変わっていなければ空。確定した時点の記録がなければ空。"""
    d = project.decision
    if d is None:
        return []
    return [name for kind, name in DECISION_KINDS.items() if pj.version(project, kind, d.pid) != d.versions.get(kind, 0)]


def numbers_summary(project: Project) -> str | None:
    """不足情報の一覧に出す、確認した数値の要約（「出どころ：内容」）。未確認・食い違いがあれば None（要確認のまま）。"""
    states = {name: state_of(project, name) for name in FACTS}
    known = {n: s for n, s in states.items() if s.value is not None}
    if not known or any(s.conflict for s in states.values()):
        return None
    source = "本人の申告" if any(s.source == "本人の申告" for s in known.values()) else "添付資料"
    parts = [f"{FACTS[n][0]} {sim.fmt(s.value)}{FACTS[n][1]}" for n, s in known.items()]
    share = ratio(states["total_hours"].value, states["part_hours"].value)
    if share.value is not None:
        parts.append(f"割合 {sim.fmt(share.value)}%")
    return f"{source}：" + "、".join(parts)
