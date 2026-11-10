"""結果画面の冒頭（結論と次の操作）と、改善案の比較表の中身を決める（AI-BIC 3.0 第1段階）。

画面の部品（ui.py）に渡す値を、既存の診断結果からプログラムで作る。AIは使わない。

- 冒頭：「最大の課題」「最初にやること」「次の判断に必要な情報」。
  確認済み（発言・資料と照合できた）か、推測・要確認かを見分けられるように、それぞれに区分を付ける
- 不足情報：ヒアリングで足りない必須の項目と、根拠を確認できなかった（要確認の）課題。
  重要なものがあれば、主ボタンを「不足情報を確認する」にする
- 比較表：「改善案」「主に減らすもの」「着手前に確認すること」と、案どうしの関係（先にやる・次に試す・併用できる）
"""

from dataclasses import dataclass, field

from ai_bic import config
from ai_bic.models import DiagnosisResult, Framework, HearingState, Issue, Proposal, ProposalRelation
from ai_bic.item_rules import is_unknown_answer
from ai_bic.readiness import evaluate

# 課題の根拠の区分 → 冒頭に出す確認状態。「本人の申告」「計算結果」は、引用を入力と照合できたものだけが残る
# （照合できなければ output_check.verify_evidences が「要確認」に格下げする）
CONFIRMATION = {
    "本人の申告": "確認済み（ご本人の発言・資料）",
    "計算結果": "確認済み（発言の数値からの計算）",
    "推測": "推測（AIの推定）",
    "要確認": "要確認（根拠を確認できていません）",
}


@dataclass
class Gap:
    """次の判断に必要な、まだ確認できていない情報。"""

    key: str  # 例：item:H3、issue:I2
    question: str  # 利用者に確認すること
    reason: str  # 確認が必要な理由
    affects: list[str]  # 影響する課題・改善案・機能
    important: bool  # 重要（主ボタンを「不足情報を確認する」にする）。回答済みなら重要として数えない
    answered: str | None = None  # 回答済みなら、その出どころつきの短い表示（例：本人の申告：12時間）
    numeric: bool = False  # 数値で答える項目（試算の元になる数値）


@dataclass
class Overview:
    top_issue: Issue | None
    top_confirmation: str  # 最大の課題の確認状態
    first_action: str
    gaps: list[Gap] = field(default_factory=list)

    @property
    def has_important_gaps(self) -> bool:
        return any(g.important and not g.answered for g in self.gaps)

    @property
    def open_gaps(self) -> list[Gap]:
        """まだ答えていない不足情報（冒頭に出す）。"""
        return [g for g in self.gaps if not g.answered]


def _targets(proposals: list[Proposal], issue_id: str) -> list[str]:
    return [f"案{no}" for no, p in enumerate(proposals, start=1) if issue_id in p.target_issue_ids]


NUMBERS_KEY = "fact:numbers"  # 試算の元になる数値の確認（数値で答える）


def gaps(result: DiagnosisResult, hearing: HearingState, framework: Framework,
         answers: dict | None = None, numbers_answered: str | None = None) -> list[Gap]:
    """まだ確認できていない情報（重要なものから。回答済みのものは最後）。

    answers：不足情報の key → 回答（project.GapAnswer）。numbers_answered：試算の元になる数値の確認状況の表示。
    """
    answers = answers or {}
    names = {item.id: item for item in framework.hearing_items}
    proposals = result.proposals.proposals if result.proposals else []
    top_id = result.proposals.summary.top_issue_id if result.proposals else None
    found: list[Gap] = []

    # 1. 3機能（業務フロー・試算・レビュー）に必須なのに、ヒアリングで足りなかった項目
    needed: dict[str, list[str]] = {}
    for feature in evaluate(hearing):
        for item_id in feature.missing_required:
            needed.setdefault(item_id, []).append(feature.name)
    for item_id, features in needed.items():
        item = names[item_id]
        status = hearing.item_status.get(item_id)
        # 「分からない」は有効な回答。聞き直さず、未確認の前提として扱っていることを示す
        unknown = any(t.item_id == item_id and t.answer and is_unknown_answer(t.answer) for t in hearing.turns)
        reason = (f"ヒアリングで「分からない」と回答されたため、未確認の前提として{'・'.join(features)}を作っています"
                  if unknown else f"{'・'.join(features)}に必要な情報が、ヒアリングで確認できていません")
        found.append(Gap(
            key=f"item:{item_id}",
            # 足りないことが分かっていれば、それをそのまま聞く（項目の説明より具体的で答えやすい）
            question=(f"{status.lacking}を教えてください（{item.name}）" if status and status.lacking
                      else f"{item.name}（{item.description}）を教えてください"),
            reason=reason, affects=features, important=True))

    # 2. 根拠を確認できなかった課題（要確認）。重要度「高」と最大の課題は重要とする
    issues = sorted(result.analysis.issues, key=lambda i: ("高", "中", "低").index(i.severity))
    for issue in issues:
        if issue.evidence_type != "要確認":
            continue
        question = issue.followup_questions[0] if issue.followup_questions else f"「{issue.title}」は実際に起きていますか？"
        reason = result.downgraded.get(issue.issue_id) or "この課題の根拠となる発言や資料が、まだありません。"
        found.append(Gap(
            key=f"issue:{issue.issue_id}", question=question, reason=reason,
            affects=[f"課題「{issue.title}」"] + _targets(proposals, issue.issue_id),
            important=issue.severity == "高" or issue.issue_id == top_id))

    # 3. 確認済みの重要な課題でも、AIが「追加で確認したい」とした点（判断の精度が上がる。重要ではない）
    for issue in issues:
        if issue.evidence_type == "要確認" or issue.severity != "高" or not issue.followup_questions:
            continue
        found.append(Gap(
            key=f"followup:{issue.issue_id}", question=issue.followup_questions[0],
            reason="分かると、改善案の選び方や効果の見込みがより確かになります。",
            affects=[f"課題「{issue.title}」"] + _targets(proposals, issue.issue_id), important=False))

    # 4. 試算の元になる数値（月の作業時間の合計と内訳・件数・人件費）。計算結果の課題があれば、その確認にもなる
    if result.proposals:
        calculated = [f"課題「{i.title}」" for i in result.analysis.issues if i.evidence_type == "計算結果"]
        found.append(Gap(
            key=NUMBERS_KEY,
            question="試算の元になる数値（月の作業時間の合計と、そのうち最も時間がかかる作業の時間・月の件数・時間あたり人件費）を確認してください",
            reason="確認した数値で、割合と1件あたりの時間をプログラムが計算し、効果試算に反映します。",
            affects=["効果試算（全案）"] + calculated, important=False, numeric=True, answered=numbers_answered))

    for g in found:
        if g.key in answers:
            a = answers[g.key]
            g.answered = f"{a.source}：{a.answer}"
    # 未回答の重要なもの → 未回答の参考 → 回答済み（同じ重さの中は上の順）
    return sorted(found, key=lambda g: (bool(g.answered), not g.important))


def build(result: DiagnosisResult, hearing: HearingState, framework: Framework,
          answers: dict | None = None, numbers_answered: str | None = None) -> Overview:
    issues = {i.issue_id: i for i in result.analysis.issues}
    if result.proposals:
        top = issues.get(result.proposals.summary.top_issue_id)
        first_action = result.proposals.summary.first_action
    else:
        top, first_action = None, "課題が確認できなかったため、改善案は作成していません。"
    return Overview(
        top_issue=top,
        top_confirmation=CONFIRMATION[top.evidence_type] if top else "",
        first_action=first_action,
        gaps=gaps(result, hearing, framework, answers, numbers_answered),
    )


# ---------------------------------------------------------------
# 改善案の比較
# ---------------------------------------------------------------
def tab_name(p: Proposal) -> str:
    """案を切り替えるタブや比較表に出す短い名前。短い名前がなければ正式な名前を省略せずに使う。"""
    return (p.short_title or "").strip() or p.title


def valid_relations(relations: list[ProposalRelation], count: int) -> list[ProposalRelation]:
    """画面に出してよい関係だけを返す（存在しない案・自分自身・重複・逆向きの順序は出さない）。"""
    kept, seen = [], set()
    for r in relations:
        if not (1 <= r.first <= count and 1 <= r.second <= count) or r.first == r.second or not r.reason.strip():
            continue
        pair = frozenset((r.first, r.second))
        if pair in seen:  # 同じ2案に2つ目の関係（逆向きの順序など）は、矛盾しうるので出さない
            continue
        seen.add(pair)
        kept.append(r)
    return kept


@dataclass
class RelationTag:
    label: str  # 先にやる・次に試す・併用できる
    other: int  # 相手の案の番号
    reason: str

    @property
    def text(self) -> str:
        return {"先にやる": f"案{self.other}の前に", "次に試す": f"案{self.other}のあとに",
                "併用できる": f"案{self.other}と"}[self.label] + self.label


def relation_tags(relations: list[ProposalRelation], count: int) -> dict[int, list[RelationTag]]:
    """案の番号 → その案に付ける関係の印。"""
    tags: dict[int, list[RelationTag]] = {no: [] for no in range(1, count + 1)}
    for r in valid_relations(relations, count):
        if r.kind == "順序":
            tags[r.first].append(RelationTag("先にやる", r.second, r.reason))
            tags[r.second].append(RelationTag("次に試す", r.first, r.reason))
        else:
            tags[r.first].append(RelationTag("併用できる", r.second, r.reason))
            tags[r.second].append(RelationTag("併用できる", r.first, r.reason))
    return tags


@dataclass
class CompareRow:
    no: int
    name: str  # 短い名前
    title: str  # 正式な名前
    category: str
    reduces: str
    reduces_from_issues: bool  # 「主に減らすもの」がなく、対象の課題の名前で代わりに示したか
    check_before: str  # 着手前に確認すること（前提条件の1つ目）
    more_checks: int  # ほかの前提条件の数
    relations: list[RelationTag]


def compare_rows(result: DiagnosisResult) -> list[CompareRow]:
    if not result.proposals:
        return []
    issues = {i.issue_id: i for i in result.analysis.issues}
    proposals = result.proposals.proposals
    tags = relation_tags(result.proposals.relations, len(proposals))
    rows = []
    for no, p in enumerate(proposals, start=1):
        reduces = (p.reduces or "").strip()
        from_issues = not reduces
        if from_issues:
            reduces = "、".join(issues[i].title for i in p.target_issue_ids if i in issues) or "—"
        rows.append(CompareRow(
            no=no, name=tab_name(p), title=p.title, category=p.category, reduces=reduces, reduces_from_issues=from_issues,
            check_before=p.prerequisites[0] if p.prerequisites else "特になし",
            more_checks=max(len(p.prerequisites) - 1, 0), relations=tags[no]))
    return rows


MAX_TOP_GAPS = 2  # 冒頭に出す不足情報の数（最初の画面に収めるため。残りは件数で示す）


def open_assumptions(hearing: HearingState, answers: dict | None = None) -> list[str]:
    """結果に示す仮定条件。足りなかった必須の項目に、結果画面ですべて答えた機能の仮定条件は出さない。"""
    answers = answers or {}
    return [config.FEATURE_ASSUMPTIONS[f.key] for f in evaluate(hearing)
            if f.level == "不足" and any(f"item:{i}" not in answers for i in f.missing_required)]
