"""改善案チャレンジレビュー（AI-BIC 2.0 機能D）：プログラムの検証・総合判定・改善案の作り直し。

AIの評価だけに頼らない（指示書 5.3）。
1. rule_findings：試算・業務フロー・ヒアリングの充足度などを、プログラムが決まった基準で確かめる
2. レビューのAIには、改善案・試算の結果・改善後フロー・1の結果を渡し、改善案を作ったAIとは別の指示文で批判的に評価させる
3. verify：AIの指摘の根拠（引用）が、入力や渡した資料に実在するかを照合する。確認できなければ「未確認」と表示する
4. final_verdict：AIの総合判定を、1の結果で上書きする（例：元が取れないのに「推奨」なら「条件付き推奨」に下げる）

改善案の作り直しは、利用者が「採用」した指摘だけを反映する。作り直す前の改善案は残し、違いを表示する。
自動でレビューと作り直しを繰り返すことはしない（利用者のボタン操作だけ。回数も上限がある）。
"""

from datetime import datetime

from ai_bic import config
from ai_bic import project as pj
from ai_bic import simulation as sim
from ai_bic.errors import AIError
from ai_bic.flow import compare_flows
from ai_bic.models import Proposal, ReviewFinding, ReviewResult, RevisedProposal
from ai_bic.output_check import _quote_found
from ai_bic.project import Project, ReviewRecord, RuleFinding
from ai_bic.readiness import evaluate

RANK = {"推奨": 0, "条件付き推奨": 1, "再検討": 2}
MAX_LIST_ITEMS = 8  # 指摘・代替案などの件数の上限（読み切れる量にする）
DISCLAIMER = "AIによる参考評価です。正式な導入の承認ではありません。"


# ---------------------------------------------------------------
# 1. プログラムの検証
# ---------------------------------------------------------------
def _proposal(project: Project, pid: str) -> Proposal:
    return project.diagnosis.proposals.proposals[pj.proposal_ids(project).index(pid)]


def rule_findings(project: Project, pid: str) -> list[RuleFinding]:
    """改善案を、決まった基準で確かめる（AIは使わない）。"""
    found: list[RuleFinding] = []

    def add(viewpoint, severity, message):
        found.append(RuleFinding(viewpoint=viewpoint, severity=severity, message=message))

    proposal = _proposal(project, pid)
    inputs = project.simulations.get(pid)
    if inputs is None:
        add("費用対効果", "中", "効果試算をまだ行っていないため、費用対効果を数値で確かめていません。")
    else:
        result = sim.simulate(inputs)
        if result.payback_status == "回収見込みなし":
            add("費用対効果", "高", "効果試算（標準）では、月の純効果が0円以下で、初期投資を回収できません。")
        if result.missing:
            add("費用対効果", "中", f"効果試算に未入力の項目があります（{'、'.join(result.missing)}）。")
        if inputs.source_of("reduction_rate") == "AI推定":
            add("費用対効果", "低", "想定削減率がAI推定の値です。実際に試して確かめる必要があります。")
        pessimistic = sim.simulate(sim.scenario_inputs(inputs, sim.Scenario(name="悲観")))
        if result.payback_status == "計算済み" and pessimistic.payback_status == "回収見込みなし":
            add("費用対効果", "中", "悲観シナリオでは初期投資を回収できません。効果が想定より小さい場合の判断が必要です。")
        setup = project.setup_tasks.get(pid)
        if setup and (inputs.other_initial_cost or 0) + (inputs.initial_cost or 0) == 0:
            add("費用対効果", "中", "初回の準備作業（" + "、".join(setup) + "）があるのに、初期費用が0円です。社内の作業時間も費用として見込んでください。")

    for f in evaluate(project.hearing):
        if f.key == "review" and f.level == "不足":
            add("課題との整合性", "高", "改善案の評価に必要な情報（業務上の問題点・改善の制約条件）が未確認です。")

    issues = {i.issue_id: i for i in project.diagnosis.analysis.issues}
    unverified = [i for i in proposal.target_issue_ids if i in issues and issues[i].evidence_type in ("要確認", "推測")]
    if unverified:
        add("課題との整合性", "中", f"対象の課題（{'、'.join(unverified)}）は根拠を確認できていません（推測・要確認）。課題が実際に起きているかの確認が必要です。")

    to_be = project.to_be.get(pid)
    if to_be is None or not to_be.steps:
        add("業務運用リスク", "中", "改善後の業務フローがないため、運用の変化（担当・手順）を確かめていません。")
    else:
        counts = compare_flows(project.as_is, to_be).counts if project.as_is else {}
        if (counts.get("廃止") or counts.get("自動化")) and not project.kept_controls.get(pid):
            add("セキュリティ・統制", "中", "工程を廃止・自動化しますが、確認や承認など統制のために残す工程が示されていません。")
        if pj.outdated_reasons(project, "to_be", pid):
            add("業務運用リスク", "中", "改善後の業務フローが古くなっています（元の改善案や現状フローが変わりました）。")
    if inputs is not None and pj.outdated_reasons(project, "simulation", pid):
        add("費用対効果", "中", "効果試算が古くなっています（元の改善案や業務フローが変わりました）。")

    if proposal.category == "新規システム導入":
        add("代替案の妥当性", "中", "新規システム導入の案です。既存ツールの活用など、より簡単な方法で足りないかを確かめてください。")
    return found


# ---------------------------------------------------------------
# 2. AIの出力の確認と、根拠の照合
# ---------------------------------------------------------------
def check_review(result: ReviewResult) -> list[str]:
    """レビューのAIの出力のルール違反（空なら合格）。違反があれば ai_client が1回だけ呼び直す。"""
    problems = []
    if not result.summary.strip():
        problems.append("評価の要約が空です")
    if not result.findings:
        problems.append("懸念事項が1つもありません。弱点や不足情報を必ず指摘してください")
    for name in ("findings", "strengths", "missing_info", "alternatives", "next_checks"):
        if len(getattr(result, name)) > MAX_LIST_ITEMS:
            problems.append(f"{name} は{MAX_LIST_ITEMS}件までです")
    for no, f in enumerate(result.findings, start=1):
        if not f.title.strip() or not f.suggestion.strip():
            problems.append(f"指摘{no}の題名か改善提案が空です")
    return problems


def evidence_texts(project: Project, pid: str, context: str) -> list[str]:
    """指摘の根拠として認める文章：利用者の入力と、レビューのAIに渡した資料。"""
    info = project.business_info
    texts = [info.company_name or "", info.department, info.task_name, info.overview, info.pain_points]
    texts += [turn.answer or "" for turn in project.hearing.turns]
    texts.append(context)
    return texts


def unverified_findings(result: ReviewResult, texts: list[str]) -> list[int]:
    """根拠の引用がない、または入力・資料に確認できない指摘の番号（0から）。画面では「根拠：未確認」と表示する。"""
    return [no for no, f in enumerate(result.findings) if not (f.evidence and _quote_found(f.evidence, texts))]


# ---------------------------------------------------------------
# 3. 総合判定（プログラムの検証で上書き）
# ---------------------------------------------------------------
def final_verdict(result: ReviewResult, rules: list[RuleFinding]) -> tuple[str, list[str]]:
    """AIの総合判定を、決まったルールで上書きする。戻り値：(判定, 変えた理由)。判定を良くする方向には変えない。"""
    verdict, reasons = result.verdict, []

    def at_least(level: str, reason: str) -> None:
        nonlocal verdict
        if RANK[verdict] < RANK[level]:
            verdict = level
            reasons.append(reason)

    if any(r.severity == "高" for r in rules):
        at_least("条件付き推奨", "プログラムの検証で重要度「高」の問題が見つかったため、「推奨」にはしません。")
    if any(f.severity == "高" for f in result.findings):
        at_least("条件付き推奨", "重要度「高」の懸念があるため、「推奨」にはしません。")
    high = sum(r.severity == "高" for r in rules) + sum(f.severity == "高" for f in result.findings)
    if high >= 3:
        at_least("再検討", f"重要度「高」の問題・懸念が{high}件あるため、改善案の見直しが必要と判断しました。")
    return verdict, reasons


# ---------------------------------------------------------------
# 4. レビューの実行（案件への反映）
# ---------------------------------------------------------------
def calls_left(project: Project, kind: str) -> int:
    limit = {"review": config.MAX_REVIEW_CALLS, "revise": config.MAX_REVISE_CALLS}[kind]
    return limit - project.ai_calls.get(kind, 0)


def _count(project: Project, kind: str) -> None:
    if project.mode == "ai":  # デモはAIを使わないので数えない
        project.ai_calls[kind] = project.ai_calls.get(kind, 0) + 1


def review_context(project: Project, pid: str, rules: list[RuleFinding]) -> str:
    """レビューのAIに渡す資料（改善案・試算・改善後フロー・充足度・プログラムの検証結果）。"""
    no = pj.proposal_ids(project).index(pid) + 1
    proposal = _proposal(project, pid)
    lines = [f"## 改善案{no}", proposal.model_dump_json(indent=2), "", "## 効果試算（標準シナリオ）"]
    inputs = project.simulations.get(pid)
    if inputs is None:
        lines.append("（未実施）")
    else:
        for name in sim.NUMBER_FIELDS:
            value = getattr(inputs, name)
            lines.append(f"- {sim.label(name)}：{'未入力' if value is None else sim.fmt(value)}（{inputs.source_of(name) or '未入力'}）")
        result = sim.simulate(inputs)
        lines += [f"- {f}" for f in result.formulas]
        lines.append(f"- 投資回収期間：{sim.payback_text(result)}")
    lines += ["", "## 改善後の業務フロー（工程・変化）"]
    to_be = project.to_be.get(pid)
    if to_be is None or not to_be.steps:
        lines.append("（未作成）")
    else:
        # 時間は「1件あたり × 実施割合」で数える。割合と合計を渡さないと、AIがすべての工程を毎回行うものとして
        # 足し算し、合計を誤った（本物のAIでの確認で見つかった問題）
        unit = f"1件＝{project.case_unit}" if project.case_unit else "1件の意味は未設定"
        lines.append(f"- 工程の時間はすべて1件あたり（{unit}）。合計は「時間×実施割合」を工程ごとに1回ずつ足した値")
        if project.as_is is not None:
            d = compare_flows(project.as_is, to_be)
            lines.append(f"- 1件あたりの合計：現状 {d.as_is_minutes.minutes_per_case:g}分 → 改善後 {d.to_be_minutes.minutes_per_case:g}分"
                         f"（時間が不明な工程：現状{len(d.as_is_minutes.unknown_step_ids)}個・改善後{len(d.to_be_minutes.unknown_step_ids)}個は含まない。"
                         "改善後は廃止の工程を含まない）")
        for s in to_be.steps:
            minutes = f"{s.minutes_per_case:g}分" if s.minutes_per_case is not None else "時間不明"
            rate = "" if s.occurrence_rate == 100 else f"×実施割合{s.occurrence_rate:g}%"
            lines.append(f"- {s.id} {s.name}［{s.change}］{s.change_note}（担当：{s.actor or '不明'}、{minutes}{rate}）")
        lines += [f"- 統制のために残した工程：{k}" for k in project.kept_controls.get(pid, [])]
        lines += [f"- 初回の準備作業：{k}" for k in project.setup_tasks.get(pid, [])]
    lines += ["", "## 情報の充足度（プログラムの判定）"]
    lines += [f"- {f.name}：{f.level}" for f in evaluate(project.hearing)]
    lines += ["", "## プログラムの検証で見つかった問題"]
    lines += [f"- [{r.severity}・{r.viewpoint}] {r.message}" for r in rules] or ["- なし"]
    return "\n".join(lines)


def generate_review(client, project: Project, pid: str) -> ReviewRecord:
    """改善案をレビューし、案件に記録する。失敗したら AIError（回数は数えない）。"""
    if project.mode == "ai" and calls_left(project, "review") <= 0:
        raise AIError("limit", "review")
    rules = rule_findings(project, pid)
    context = review_context(project, pid, rules)
    no = pj.proposal_ids(project).index(pid) + 1
    result = client.review(project.business_info, project.hearing, project.diagnosis.analysis, no, context)
    verdict, reasons = final_verdict(result, rules)
    record = ReviewRecord(
        result=result, unverified=unverified_findings(result, evidence_texts(project, pid, context)),
        rule_findings=rules, ai_verdict=result.verdict, verdict=verdict, override_reasons=reasons,
        created_at=datetime.now(config.JST),
    )
    project.reviews[pid] = record
    _count(project, "review")
    pj.mark_updated(project, "review", pid)
    return record


# ---------------------------------------------------------------
# 5. 改善案の作り直し（採用した指摘だけを反映）
# ---------------------------------------------------------------
def adopted_findings(project: Project, pid: str) -> list[ReviewFinding]:
    record = project.reviews.get(pid)
    if record is None:
        return []
    return [f for no, f in enumerate(record.result.findings) if record.decisions.get(no) == "採用"]


def check_revision(revised: RevisedProposal, original: Proposal, analysis) -> list[str]:
    """作り直した改善案のルール違反（空なら合格）。元の業務課題から外れていないかを確かめる。"""
    problems = []
    known = {i.issue_id for i in analysis.issues}
    p = revised.proposal
    unknown = [i for i in p.target_issue_ids if i not in known]
    if unknown:
        problems.append(f"存在しない課題を対象にしています（{'、'.join(unknown)}）")
    if not set(original.target_issue_ids) & set(p.target_issue_ids):
        problems.append("元の改善案が対象にしていた課題が、1つも残っていません（無関係な変更をしない）")
    if p.category == "新規システム導入" and not (p.why_not_simpler or "").strip():
        problems.append("新規システム導入なのに、より簡単な方法では足りない理由がありません")
    if not 1 <= len(p.first_steps) <= config.MAX_FIRST_STEPS:
        problems.append(f"最初の一歩は1〜{config.MAX_FIRST_STEPS}個にしてください")
    if not revised.changes:
        problems.append("何を変えたか（changes）が空です")
    return problems


def revise(client, project: Project, pid: str) -> None:
    """採用した指摘を反映して改善案を作り直す。改善後フロー・試算・レビューは「要更新」になる。"""
    adopted = adopted_findings(project, pid)
    if not adopted:
        raise ValueError("採用した指摘がありません")
    if project.mode == "ai" and calls_left(project, "revise") <= 0:
        raise AIError("limit", "revise")
    original = _proposal(project, pid)
    no = pj.proposal_ids(project).index(pid) + 1
    revised = client.revise(project.business_info, project.hearing, project.diagnosis.analysis, no, original, adopted)
    project.proposal_history.setdefault(pid, []).append(original)
    project.diagnosis.proposals.proposals[no - 1] = revised.proposal
    project.revision_notes[pid] = revised.changes
    _count(project, "revise")
    pj.mark_updated(project, "proposal", pid)


DIFF_FIELDS = {
    "title": "改善案", "category": "種類", "description": "内容", "expected_effect": "期待する効果",
    "difficulty": "導入難易度", "risks": "リスク", "prerequisites": "前提条件", "before": "導入前", "after": "導入後",
    "first_steps": "最初の一歩", "why_not_simpler": "より簡単な方法では足りない理由",
}


def diff(before: Proposal, after: Proposal) -> list[tuple[str, str, str]]:
    """作り直す前後で変わった項目（項目名, 変更前, 変更後）。"""
    def text(value) -> str:
        if isinstance(value, list):
            return "\n".join(f"・{v}" for v in value) or "（なし）"
        return value or "（なし）"

    return [
        (name, text(getattr(before, field)), text(getattr(after, field)))
        for field, name in DIFF_FIELDS.items()
        if getattr(before, field) != getattr(after, field)
    ]
