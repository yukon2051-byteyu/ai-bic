"""AI出力の内容チェック（詳細設計書 1.5〜1.7）。

形式（項目がそろっているか、値が候補の中か）は Pydantic と構造化出力が確認する。
ここでは、それだけでは分からない「内容のルール違反」をプログラムで確認する。

check_* 関数は「違反の一覧」を返す。空のリストなら合格。
違反があれば、呼び出し側（ai_client.py）がAIを1回だけ呼び直す。
"""

import re
import unicodedata

from ai_bic.config import (
    CALCULATION_CONSTANTS,
    MAX_FIRST_STEPS,
    MAX_ISSUES,
    MAX_PROPOSALS,
    MAX_QUESTIONS_PER_ITEM,
    MIN_QUOTE_CHARS,
    NEGATION_SUFFIXES,
)
from ai_bic.item_rules import askable_item_ids, is_unknown_answer
from ai_bic.models import (
    AnalysisResult,
    Attachment,
    BusinessInfo,
    Framework,
    HearingState,
    Issue,
    ProposalResult,
    QuestionResult,
)

# 引用の前後についていても無視する括弧類（AIが「」で囲んで引用することがあるため）
QUOTE_BRACKETS = "「」『』\"'“”‘’"

# 格下げの理由（画面とレポートに表示する。B04）
REASON_NOT_FOUND = "根拠となる発言を入力内容の中に確認できなかった"
REASON_NEGATED = "引用した発言のすぐ後に否定の言葉（「ない」など）が続き、発言の意味と逆になっているおそれがある"
REASON_NO_CALCULATION = "計算式が示されていない"


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
    result: QuestionResult, state: HearingState, framework: Framework, final: bool = False
) -> list[str]:
    """質問生成の結果を確認する（詳細設計 1.5）。

    final=True（ヒアリング終了後の、判定だけの依頼）は、判定がそろっているかだけを確認する。
    質問が付いていても使わないので、質問の内容は確認しない。
    """
    problems: list[str] = []
    item_ids = [item.id for item in framework.hearing_items]

    # 全診断項目の判定が1回ずつそろっているか
    returned_ids = [s.item_id for s in result.item_status]
    if sorted(returned_ids) != sorted(item_ids):
        problems.append(f"充足状況の項目が設定と一致しない: {returned_ids}")
    if final:
        return problems

    status_by_id = {s.item_id: s.status for s in result.item_status}
    has_question = bool(result.next_item_id and result.question and result.question.strip())
    # まだ質問してよい項目（上限に達した項目や「分からない」と言われた項目は除く）
    askable = askable_item_ids(state, framework, status_by_id)

    # 質問してよい項目が残っているときだけ、次の質問を求める
    # （残っていなければ、ヒアリングは終了する：hearing._finish_reason）
    if askable and not has_question:
        if not result.is_sufficient:
            problems.append("ヒアリング継続なのに次の質問がない")
        elif any(status_by_id.get(i) == "未確認" for i in askable):
            # 「十分」と言っても、まだ聞ける未確認の項目があれば続ける
            problems.append("未確認の項目があるのに次の質問がない")

    if result.next_item_id:
        asked = sum(1 for turn in state.turns if turn.item_id == result.next_item_id)
        if result.next_item_id not in item_ids:
            problems.append(f"存在しない診断項目を質問しようとした: {result.next_item_id}")
        elif status_by_id.get(result.next_item_id) == "充足":
            # 回答済みの内容を繰り返し聞かない（R3）
            problems.append(f"充足済みの項目を質問しようとした: {result.next_item_id}")
        elif asked >= MAX_QUESTIONS_PER_ITEM:
            # 「分かりません」などで埋まらない項目を、言い換えて聞き続けない（R3）
            problems.append(
                f"同じ項目を{MAX_QUESTIONS_PER_ITEM}回質問済みなのに、さらに質問しようとした: {result.next_item_id}"
            )

    # 前の質問とほとんど同じ文で聞き直さない（本物のAIで、1問目と2問目が同じ質問になった。AI-BIC 3.1）
    if has_question:
        for turn in state.turns:
            if similarity(result.question, turn.question) >= SAME_QUESTION_SIMILARITY:
                problems.append(f"{turn.turn_no}問目とほとんど同じ質問をしようとした（同じ趣旨の質問を繰り返さない）")
                break

    return problems


SAME_QUESTION_SIMILARITY = 0.5  # これ以上似ていれば「同じ質問」とみなす（2文字ずつの重なりの割合）


def similarity(a: str | None, b: str | None) -> float:
    """2つの文の似ている度合い（0〜1）。記号や空白を除いた2文字ずつの組の重なり（ジャカード係数）。"""
    def grams(text: str | None) -> set[str]:
        s = re.sub(r"[\s、。？?！!「」（）()・,.:：]", "", normalize(text))
        return {s[i:i + 2] for i in range(len(s) - 1)}
    x, y = grams(a), grams(b)
    return len(x & y) / len(x | y) if x and y else 0.0


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


def _check_quote(quote: str, texts: list[str]) -> str | None:
    """引用を照合し、根拠と認められなければその理由を返す（認められれば None）。

    - 引用が、いずれかの文章に含まれていること
    - 含まれている場所のすぐ後が、否定の言葉でないこと（「ミスがある」＋「ことはない」の切り取りを防ぐ。B04）
    """
    target = normalize(quote).strip(QUOTE_BRACKETS)
    # 空や短すぎる引用（例：「時間」）は、どこにでも含まれてしまい根拠にならない
    if len(target) < MIN_QUOTE_CHARS:
        return REASON_NOT_FOUND
    negated = False
    for text in texts:
        normalized = normalize(text)
        start = normalized.find(target)
        while start != -1:
            if not normalized[start + len(target):].startswith(NEGATION_SUFFIXES):
                return None
            negated = True
            start = normalized.find(target, start + 1)
    return REASON_NEGATED if negated else REASON_NOT_FOUND


def _quote_found(quote: str, texts: list[str]) -> bool:
    """引用が、根拠として認められる形で、いずれかの文章に含まれているかを返す。"""
    return _check_quote(quote, texts) is None


def _numbers(text: str) -> list[float]:
    """文章に含まれる数値（「2,500」のような桁区切りも1つの数値として読む）。"""
    text = unicodedata.normalize("NFKC", text or "")
    return [float(n.replace(",", "")) for n in re.findall(r"\d+(?:,\d{3})*(?:\.\d+)?", text)]


def _check_calculation(calculation: str | None, all_texts: list[str]) -> str | None:
    """「計算結果」の計算式を確かめ、問題があればその理由を返す（B04）。

    「＝」より左（計算に使った数値）が、入力に実在する数値か、単位の換算の数値であることを確かめる。
    計算の答えが正しいかまでは確かめない（式の書き方がばらばらで、誤判定しやすいため）。
    """
    if not (calculation or "").strip():
        return REASON_NO_CALCULATION
    left = unicodedata.normalize("NFKC", calculation).split("=")[0]
    operands = _numbers(left)
    if not operands:
        return REASON_NO_CALCULATION
    known = {n for text in all_texts for n in _numbers(text)} | set(CALCULATION_CONSTANTS)
    missing = [n for n in operands if n not in known]
    if missing:
        shown = "・".join(f"{n:g}" for n in missing)
        return f"計算式の数値（{shown}）を入力内容の中に確認できなかった"
    return None


def downgrade_message(original_type: str, reason: str) -> str:
    """格下げを利用者に説明する文（画面とレポートで共通）。"""
    return f"AIは「{original_type}」と判断しましたが、{reason}ため「要確認」としています。"


def verify_evidences(
    analysis: AnalysisResult,
    info: BusinessInfo,
    state: HearingState,
    attachments: list[Attachment] | None = None,
) -> tuple[AnalysisResult, dict[str, str]]:
    """「本人の申告」「計算結果」の課題の根拠を、利用者の実際の入力と照合する（詳細設計 1.6・B04）。

    - 本人の申告：引用が1つ以上、入力に（否定の切り取りでなく）実在すること
    - 計算結果：上に加えて、計算式に使った数値が入力に実在すること
    確認できない課題は「要確認」に格下げする（決定事項B3）。
    戻り値：(格下げを反映した分析結果, 格下げした課題ID → 格下げの説明)
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
    all_texts = business_texts + list(answers_by_turn.values()) + [a.text for a in attachments]

    def texts_for(ev) -> list[str]:
        if ev.source == "業務情報":
            return business_texts
        if ev.source == "添付ファイル":
            # 添付ファイルの引用は、指定された番号のファイルとだけ照合する
            valid = ev.file_no is not None and 1 <= ev.file_no <= len(attachments)
            return [attachments[ev.file_no - 1].text] if valid else []
        # 回答の引用は、指定された回の回答とだけ照合する（安全側に倒す）
        return [answers_by_turn.get(ev.turn_no, "")]

    def problem(issue: Issue) -> str | None:
        reasons = [_check_quote(ev.quote, texts_for(ev)) for ev in issue.evidences]
        if None not in reasons:
            # 否定の切り取りがあれば、それを優先して伝える
            return REASON_NEGATED if REASON_NEGATED in reasons else REASON_NOT_FOUND
        if issue.evidence_type == "計算結果":
            return _check_calculation(issue.calculation, all_texts)
        return None

    checked = analysis.model_copy(deep=True)
    downgraded: dict[str, str] = {}

    for issue in checked.issues:
        # 「分かりません」のような情報のない回答は、どの区分の課題でも根拠として扱わない
        # （本物のAIが「すみません、その点は分かりません。」を根拠に引用したため）
        issue.evidences = [ev for ev in issue.evidences if not is_unknown_answer(ev.quote)]

        if issue.evidence_type not in ("本人の申告", "計算結果"):
            continue
        reason = problem(issue)
        if reason:
            downgraded[issue.issue_id] = downgrade_message(issue.evidence_type, reason)
            issue.evidence_type = "要確認"

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

    # 診断サマリーが、実在する課題を最重要として指しているか
    if result.summary.top_issue_id not in issue_ids:
        problems.append(f"サマリーが存在しない課題を指している: {result.summary.top_issue_id}")
    if not result.summary.headline.strip() or not result.summary.first_action.strip():
        problems.append("サマリーの要点または最初の一手が空")

    for i, p in enumerate(result.proposals, start=1):
        if not p.target_issue_ids:
            problems.append(f"改善案{i}: 対応する課題がない")
        unknown = set(p.target_issue_ids) - issue_ids
        if unknown:
            problems.append(f"改善案{i}: 存在しない課題を指している {sorted(unknown)}")
        # 新規システム導入を安易に推奨しない：より簡単な方法では不十分な理由を必須にする
        if p.category == "新規システム導入" and not (p.why_not_simpler or "").strip():
            problems.append(f"改善案{i}: 新規システム導入なのに、より簡単な方法では不十分な理由がない")
        # 導入前後の比較と、最初の一歩（訴求力のための項目）
        if not p.before.strip() or not p.after.strip():
            problems.append(f"改善案{i}: 導入前後の比較が空")
        steps = [s for s in p.first_steps if s.strip()]
        if not 1 <= len(steps) <= MAX_FIRST_STEPS:
            problems.append(f"改善案{i}: 最初の一歩が1〜{MAX_FIRST_STEPS}個でない: {len(steps)}個")

    return problems
