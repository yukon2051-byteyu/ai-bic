"""AI出力の内容チェック（詳細設計書 1.5〜1.7）。

形式（項目がそろっているか、値が候補の中か）は Pydantic と構造化出力が確認する。
ここでは、それだけでは分からない「内容のルール違反」をプログラムで確認する。

check_* 関数は「違反の一覧」を返す。空のリストなら合格。
違反があれば、呼び出し側（ai_client.py）がAIを1回だけ呼び直す。
"""

import unicodedata

from ai_bic.config import MAX_ISSUES, MAX_PROPOSALS, MIN_QUOTE_CHARS
from ai_bic.models import (
    AnalysisResult,
    Attachment,
    BusinessInfo,
    Framework,
    HearingState,
    ProposalResult,
    QuestionResult,
)

# 引用の前後についていても無視する括弧類（AIが「」で囲んで引用することがあるため）
QUOTE_BRACKETS = "「」『』\"'“”‘’"


def normalize(text: str | None) -> str:
    """照合用に文字をそろえる（決定事項C1）。

    - Unicode正規化（NFKC）で、全角英数字を半角にそろえる（例：「ＥＸＣＥＬ」→「EXCEL」）
    - 空白・改行をすべて取り除く
    それ以外の違い（言い換え・語尾の違いなど）は吸収しない。
    """
    text = unicodedata.normalize("NFKC", text or "")
    return "".join(text.split())


# ---------------------------------------------------------------
# ① 質問生成（F02）
# ---------------------------------------------------------------
def check_question_result(
    result: QuestionResult, state: HearingState, framework: Framework
) -> list[str]:
    """質問生成の結果を確認する（詳細設計 1.5）。"""
    problems: list[str] = []
    item_ids = [item.id for item in framework.hearing_items]

    # 全診断項目の判定が1回ずつそろっているか
    returned_ids = [s.item_id for s in result.item_status]
    if sorted(returned_ids) != sorted(item_ids):
        problems.append(f"充足状況の項目が設定と一致しない: {returned_ids}")

    status_by_id = {s.item_id: s.status for s in result.item_status}
    has_question = bool(result.next_item_id and result.question and result.question.strip())

    # 「まだ聞く」なら、次の質問が必要
    if not result.is_sufficient and not has_question:
        problems.append("ヒアリング継続なのに次の質問がない")

    # 「十分」と言っても未確認の項目があれば続ける。その場合は質問が必要
    if result.is_sufficient and "未確認" in status_by_id.values() and not has_question:
        problems.append("未確認の項目があるのに次の質問がない")

    if result.next_item_id:
        if result.next_item_id not in item_ids:
            problems.append(f"存在しない診断項目を質問しようとした: {result.next_item_id}")
        elif status_by_id.get(result.next_item_id) == "充足":
            # 回答済みの内容を繰り返し聞かない（R3）
            problems.append(f"充足済みの項目を質問しようとした: {result.next_item_id}")

    return problems


# ---------------------------------------------------------------
# ② 課題分析（F03）
# ---------------------------------------------------------------
def check_analysis(result: AnalysisResult, framework: Framework) -> list[str]:
    """課題分析の結果の件数・ID・観点を確認する（詳細設計 1.6）。"""
    problems: list[str] = []
    viewpoint_ids = {v.id for v in framework.viewpoints}

    if len(result.issues) > MAX_ISSUES:
        problems.append(f"課題が{MAX_ISSUES}件を超えている: {len(result.issues)}件")

    issue_ids = [issue.issue_id for issue in result.issues]
    if len(issue_ids) != len(set(issue_ids)):
        problems.append(f"課題IDが重複している: {issue_ids}")

    for issue in result.issues:
        if not issue.viewpoint_ids:
            problems.append(f"{issue.issue_id}: 評価観点がない")
        unknown = set(issue.viewpoint_ids) - viewpoint_ids
        if unknown:
            problems.append(f"{issue.issue_id}: 存在しない評価観点 {sorted(unknown)}")

    unknown = set(result.no_issue_viewpoints) - viewpoint_ids
    if unknown:
        problems.append(f"問題なしの観点に存在しないIDがある: {sorted(unknown)}")

    return problems


def _quote_found(quote: str, texts: list[str]) -> bool:
    """引用が、いずれかの文章に含まれているかを返す。"""
    target = normalize(quote).strip(QUOTE_BRACKETS)
    # 空や短すぎる引用（例：「時間」）は、どこにでも含まれてしまい根拠にならない
    if len(target) < MIN_QUOTE_CHARS:
        return False
    return any(target in normalize(text) for text in texts)


def verify_evidences(
    analysis: AnalysisResult,
    info: BusinessInfo,
    state: HearingState,
    attachments: list[Attachment] | None = None,
) -> tuple[AnalysisResult, list[str]]:
    """「事実」の課題の引用が、利用者の実際の入力に存在するかを照合する（詳細設計 1.6）。

    1つも確認できない課題は「要確認」に格下げする（決定事項B3）。
    戻り値：(格下げを反映した分析結果, 格下げした課題IDの一覧)
    元の analysis は変更しない。
    """
    attachments = attachments or []
    business_texts = [
        info.company_name or "",
        info.department,
        info.task_name,
        info.overview,
        info.pain_points,
    ]
    answers_by_turn = {turn.turn_no: turn.answer or "" for turn in state.turns}

    checked = analysis.model_copy(deep=True)
    downgraded: list[str] = []

    for issue in checked.issues:
        if issue.evidence_type != "事実":
            continue

        verified = False
        for ev in issue.evidences:
            if ev.source == "業務情報":
                texts = business_texts
            elif ev.source == "添付ファイル":
                # 添付ファイルの引用は、指定された番号のファイルとだけ照合する
                valid = ev.file_no is not None and 1 <= ev.file_no <= len(attachments)
                texts = [attachments[ev.file_no - 1].text] if valid else []
            else:
                # 回答の引用は、指定された回の回答とだけ照合する（安全側に倒す）
                texts = [answers_by_turn.get(ev.turn_no, "")]
            if _quote_found(ev.quote, texts):
                verified = True
                break

        if not verified:
            issue.evidence_type = "要確認"
            downgraded.append(issue.issue_id)

    return checked, downgraded


# ---------------------------------------------------------------
# ③ 改善提案（F04）
# ---------------------------------------------------------------
def check_proposals(result: ProposalResult, analysis: AnalysisResult) -> list[str]:
    """改善提案の結果を確認する（詳細設計 1.7）。"""
    problems: list[str] = []
    issue_ids = {issue.issue_id for issue in analysis.issues}

    if not 1 <= len(result.proposals) <= MAX_PROPOSALS:
        problems.append(f"改善案の件数が1〜{MAX_PROPOSALS}件でない: {len(result.proposals)}件")

    for i, p in enumerate(result.proposals, start=1):
        if not p.target_issue_ids:
            problems.append(f"改善案{i}: 対応する課題がない")
        unknown = set(p.target_issue_ids) - issue_ids
        if unknown:
            problems.append(f"改善案{i}: 存在しない課題を指している {sorted(unknown)}")
        # 新規システム導入を安易に推奨しない：より簡単な方法では不十分な理由を必須にする
        if p.category == "新規システム導入" and not (p.why_not_simpler or "").strip():
            problems.append(f"改善案{i}: 新規システム導入なのに、より簡単な方法では不十分な理由がない")

    return problems
