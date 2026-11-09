"""業務フローのAI出力の確認と変換（AI-BIC 2.0 機能B）。

AIの出力（models.FlowResult）を、そのまま画面のデータ（flow.Flow）にしない。
1. check_flow_result：形の崩れ（存在しない工程への接続、改善案の数の不一致など）を確かめる。
   違反があれば ai_client が1回だけ呼び直す
2. to_flows：利用者の発言との照合をしてから Flow に変換する
   - 時間の根拠が「ヒアリング」でも、引用が利用者の発言に確認できなければ「AI推定」に格下げする
     （ヒアリングで得ていない数値を、実測値のように見せないため）
   - 現状フローで根拠の引用を確認できない工程は「要確認」にする（不明な工程を確定させない）
"""

import re
from dataclasses import dataclass, field

from pydantic import ValidationError

from ai_bic import config
from ai_bic import project as pj
from ai_bic.errors import AIError
from ai_bic.flow import Flow, FlowEdge, FlowStep
from ai_bic.models import AIFlowEdge, AIFlowStep, BusinessInfo, FlowResult, HearingState
from ai_bic.output_check import _numbers, _quote_found
from ai_bic.project import Project

REF = re.compile(r"^S[1-9][0-9]*$")
MAX_QUESTIONS = 5


def check_flow_result(result: FlowResult, proposal_count: int) -> list[str]:
    """形のルール違反の一覧（空なら合格）。違反の内容に利用者の発言は含めない。"""
    problems = _check_one("現状フロー", result.as_is_steps, result.as_is_edges)
    numbers = sorted(t.proposal_no for t in result.to_be)
    if numbers != list(range(1, proposal_count + 1)):
        problems.append(f"改善後フローは改善案{proposal_count}つに1つずつ必要です（proposal_no：{numbers}）")
    for t in result.to_be:
        problems += _check_one(f"改善後フロー（案{t.proposal_no}）", t.steps, t.edges)
    if not result.case_unit.strip():
        problems.append("「1件」の意味（case_unit）が空です")
    if result.cases_per_month is not None and result.cases_per_month < 0:
        problems.append("月の件数が負の数です")
    if result.cases_per_month is None and result.cases_basis != "不明":
        problems.append(f"月の件数がないのに、根拠が「{result.cases_basis}」です")
    if len(result.questions) > MAX_QUESTIONS:
        problems.append(f"確認したいことは{MAX_QUESTIONS}つまでです")
    return problems


def _check_one(name: str, steps: list[AIFlowStep], edges: list[AIFlowEdge]) -> list[str]:
    problems = []
    if not 1 <= len(steps) <= config.MAX_FLOW_STEPS:
        problems.append(f"{name}：工程は1〜{config.MAX_FLOW_STEPS}個にしてください（{len(steps)}個）")
    refs = [s.ref for s in steps]
    if len(refs) != len(set(refs)):
        problems.append(f"{name}：工程の番号が重複しています")
    bad = [r for r in refs if not REF.match(r)]
    if bad:
        problems.append(f"{name}：工程の番号は S1, S2 … の形にしてください（{'、'.join(bad[:3])}）")
    for s in steps:
        if not s.name.strip():
            problems.append(f"{name}：{s.ref} の工程名が空です")
        if not 0 <= s.occurrence_rate <= 100:
            problems.append(f"{name}：{s.ref} の実施割合は0〜100にしてください")
        if s.minutes_per_case is not None and s.minutes_per_case < 0:
            problems.append(f"{name}：{s.ref} の作業時間が負の数です")
        if s.minutes_per_case is None and s.time_basis != "不明":
            problems.append(f"{name}：{s.ref} は作業時間がないのに、根拠が「{s.time_basis}」です")
    known = set(refs)
    pairs = set()
    for e in edges:
        if e.source not in known or e.target not in known:
            problems.append(f"{name}：存在しない工程への接続があります（{e.source}→{e.target}）")
        elif e.source == e.target:
            problems.append(f"{name}：工程が自分自身に接続しています（{e.source}）")
        elif (e.source, e.target) in pairs:
            problems.append(f"{name}：同じ接続が重複しています（{e.source}→{e.target}）")
        pairs.add((e.source, e.target))
    return problems


@dataclass
class ConvertedFlows:
    as_is: Flow
    to_be: dict[str, Flow]  # 改善案ID（P1…）→ フロー
    kept_controls: dict[str, list[str]]
    setup_tasks: dict[str, list[str]]
    questions: list[str]
    case_unit: str
    cases_per_month: float | None
    cases_source: str | None
    notes: list[str] = field(default_factory=list)  # 格下げなど、プログラムが直したこと（画面に表示する）


def to_flows(result: FlowResult, info: BusinessInfo, state: HearingState, proposal_ids: list[str]) -> ConvertedFlows:
    """利用者の発言と照合してから、画面のデータに変換する。check_flow_result に合格していること。"""
    texts = [info.company_name or "", info.department, info.task_name, info.overview, info.pain_points]
    texts += [turn.answer or "" for turn in state.turns]
    notes: list[str] = []
    as_is = _convert("現状", result.as_is_steps, result.as_is_edges, texts, notes, as_is=True)
    to_be, kept, setup = {}, {}, {}
    for t in sorted(result.to_be, key=lambda t: t.proposal_no):
        pid = proposal_ids[t.proposal_no - 1]
        to_be[pid] = _convert(f"案{t.proposal_no}", t.steps, t.edges, texts, notes, as_is=False)
        kept[pid] = t.kept_controls
        setup[pid] = t.setup_tasks
    # 月の件数も、作業時間と同じく「ヒアリング」なら数を述べた発言を照合する
    cases_source = {"ヒアリング": "ヒアリング", "AI推定": "AI推定", "不明": None}[result.cases_basis]
    if result.cases_per_month is None:
        cases_source = None
    elif cases_source == "ヒアリング":
        quote = result.cases_quote
        if not (quote and _quote_found(quote, texts) and _numbers(quote)):
            cases_source = "AI推定"
            notes.append("AIは月の件数を「ヒアリング」としましたが、その数を述べた発言を確認できなかったため「AI推定」としています。")
    return ConvertedFlows(
        as_is, to_be, kept, setup, result.questions[:MAX_QUESTIONS],
        result.case_unit.strip()[: config.MAX_STEP_TEXT], result.cases_per_month, cases_source, notes,
    )


def _convert(name, steps, edges, texts, notes, as_is: bool) -> Flow:
    converted = []
    for s in steps:
        quote_ok = bool(s.evidence_quote) and _quote_found(s.evidence_quote, texts)
        source = {"ヒアリング": "ヒアリング", "AI推定": "AI推定", "不明": None}[s.time_basis]
        minutes = s.minutes_per_case
        if source == "ヒアリング" and not (quote_ok and _numbers(s.evidence_quote)):
            source = "AI推定"
            notes.append(
                f"{name}の {s.ref}「{s.name}」：AIは作業時間を「ヒアリング」としましたが、"
                "その時間を述べた発言を確認できなかったため「AI推定」としています。"
            )
        needs_confirmation = s.needs_confirmation
        if as_is and not quote_ok and not needs_confirmation:
            needs_confirmation = True
            notes.append(f"{name}の {s.ref}「{s.name}」：根拠となる発言を確認できなかったため「要確認」としています。")
        converted.append(FlowStep(
            id=s.ref, name=s.name[: config.MAX_STEP_TEXT], kind=s.kind,
            actor=s.actor[: config.MAX_STEP_TEXT], description=s.description[: config.MAX_STEP_NOTE],
            system=s.system[: config.MAX_STEP_TEXT], manual=s.manual,
            minutes_per_case=minutes, minutes_source=source if minutes is not None else None,
            occurrence_rate=s.occurrence_rate, wait_or_rework=s.wait_or_rework[: config.MAX_STEP_NOTE],
            problem=s.problem[: config.MAX_STEP_NOTE], needs_confirmation=needs_confirmation,
            change=getattr(s, "change", "変更なし"), change_note=getattr(s, "change_note", "")[: config.MAX_STEP_NOTE],
        ))
    flow_edges = [FlowEdge(source=e.source, target=e.target, label=e.label[: config.MAX_STEP_TEXT]) for e in edges]
    return Flow(steps=converted, edges=flow_edges)


# ---------------------------------------------------------------
# 案件への反映
# ---------------------------------------------------------------
def flow_calls_left(project: Project) -> int:
    return config.MAX_FLOW_CALLS - project.ai_calls.get("flow", 0)


def generate_flows(client, project: Project) -> None:
    """AI（デモでは固定データ）で業務フローを作り、案件に入れる。

    失敗したら AIError（回数は数えない）。AI診断では1案件あたり config.MAX_FLOW_CALLS 回まで。
    """
    proposals = project.diagnosis.proposals
    if proposals is None:
        raise ValueError("改善案がないため、業務フローは作れません")
    if project.mode == "ai" and flow_calls_left(project) <= 0:
        raise AIError("limit", "flow")
    result = client.make_flows(project.business_info, project.hearing, project.diagnosis.analysis, proposals)
    try:
        converted = to_flows(result, project.business_info, project.hearing, pj.proposal_ids(project))
    except (ValidationError, ValueError, IndexError) as e:  # 確認をすり抜けた崩れ（通常は起きない）
        raise AIError("invalid_output", type(e).__name__) from e
    project.as_is = converted.as_is
    project.to_be = converted.to_be
    project.kept_controls = converted.kept_controls
    project.setup_tasks = converted.setup_tasks
    project.case_unit = converted.case_unit
    project.cases_per_month = converted.cases_per_month
    project.cases_source = converted.cases_source
    project.flow_questions = converted.questions
    project.flow_notes = converted.notes
    if project.mode == "ai":
        project.ai_calls["flow"] = project.ai_calls.get("flow", 0) + 1
    _stamp_new_flows(project, list(converted.to_be))


def start_blank(project: Project) -> None:
    """AIを使わずに、空の業務フローから自分で作る（改善後フローは、画面で現状フローを写して始められる）。"""
    project.as_is = Flow()
    for pid in pj.proposal_ids(project):
        project.to_be[pid] = Flow()
    _stamp_new_flows(project, pj.proposal_ids(project))


def _stamp_new_flows(project: Project, pids: list[str]) -> None:
    """業務フローを作ったことを記録する。

    初めて作ったときは、それまでの試算を「再計算が必要」にしない。試算は業務フローより前に作られ、
    業務フローを使っていないため（画面を撮影して見つけた誤表示）。作り直したときは、これまでどおり古い扱いにする。
    """
    first = "as_is" not in project.stamps
    fresh = [pid for pid in pids if first and pj.status(project, "simulation", pid) == "完了"]
    pj.mark_updated(project, "as_is")
    for pid in pids:
        pj.mark_updated(project, "to_be", pid)
    for pid in fresh:
        project.stamps[pj.key("simulation", pid)].based_on = {
            pj.key(dep, pid): pj.version(project, dep, pid) for dep in pj.DEPENDS["simulation"]
        }
