"""結果画面の冒頭（結論と次の操作）と、改善案の比較表の中身を決める（AI-BIC 3.0 第1段階）。

画面の部品（ui.py）に渡す値を、既存の診断結果からプログラムで作る。AIは使わない。

- 冒頭：「最大の課題」「最初にやること」「次の判断に必要な情報」。
  確認済み（発言・資料と照合できた）か、推測・要確認かを見分けられるように、それぞれに区分を付ける
- 不足情報：ヒアリングで足りない必須の項目と、根拠を確認できなかった（要確認の）課題。
  重要なものがあれば、主ボタンを「不足情報を確認する」にする
- 比較表：「改善案」「主に減らすもの」「着手前に確認すること」と、案どうしの関係（先にやる・次に試す・併用できる）
"""

import re
from dataclasses import dataclass, field

from ai_bic import genai
from ai_bic import config
from ai_bic.models import DiagnosisResult, Framework, HearingState, Issue, Proposal, ProposalRelation
from ai_bic.item_rules import is_unknown_answer, planned_turn
from ai_bic.output_check import similarity
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
    merged_keys: list[str] = field(default_factory=list)  # 同じ確認項目として1件にまとめた、ほかの不足情報の key
    planned: str | None = None  # 確認予定の内容（あとで確かめる。聞き直さず、確認済みの値にもしない。AI-BIC 3.2）


@dataclass
class Overview:
    top_issue: Issue | None
    top_confirmation: str  # 最大の課題の確認状態
    first_action: str
    gaps: list[Gap] = field(default_factory=list)

    @property
    def has_important_gaps(self) -> bool:
        # 確認予定の項目は、いま答えを求めない（あとで確かめると決めている）ので数えない
        return any(g.important and not g.answered and not g.planned for g in self.gaps)

    @property
    def open_gaps(self) -> list[Gap]:
        """まだ答えていない不足情報（冒頭に出す）。"""
        return [g for g in self.gaps if not g.answered]


def _targets(proposals: list[Proposal], issue_id: str) -> list[str]:
    return [f"案{no}" for no, p in enumerate(proposals, start=1) if issue_id in p.target_issue_ids]


NUMBERS_KEY = "fact:numbers"  # 試算の元になる数値の確認（数値で答える）


def gaps(result: DiagnosisResult, hearing: HearingState, framework: Framework,
         answers: dict | None = None, numbers_answered: str | None = None, needs: list | None = None) -> list[Gap]:
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
        if item_id == "H3" and result.analysis.quantities:
            # 数値の一覧がある案件では、作業時間・頻度をまとめて「未確認」と言わず、欠けている数値だけを下の4で確認する
            continue
        item = names[item_id]
        status = hearing.item_status.get(item_id)
        # 「分からない」は有効な回答。聞き直さず、未確認の前提として扱っていることを示す
        unknown = any(t.item_id == item_id and t.answer and is_unknown_answer(t.answer) for t in hearing.turns)
        reason = (f"ヒアリングで「分からない」と回答されたため、未確認の前提として{'・'.join(features)}を作っています"
                  if unknown else f"{'・'.join(features)}に必要な情報が、ヒアリングで確認できていません")
        planned = planned_turn(hearing, item_id)
        found.append(Gap(planned=planned.answer if planned else None,
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

    # 4. 数値の一覧がある案件（AI-BIC 3.1）：改善案の効果や試算に必要で、まだ分からない値だけを確認する
    if result.proposals and result.analysis.quantities:
        for n in needs or []:
            found.append(number_gap(n))
    # 4'. 一覧がない古い案件：試算の元になる数値（合計と内訳・件数・人件費）。計算結果の課題があれば、その確認にもなる
    elif result.proposals:
        calculated = [f"課題「{i.title}」" for i in result.analysis.issues if i.evidence_type == "計算結果"]
        found.append(Gap(
            key=NUMBERS_KEY,
            question="試算の元になる数値（月の作業時間の合計と、そのうち最も時間がかかる作業の時間・月の件数・時間あたり人件費）を確認してください",
            reason="確認した数値で、割合と1件あたりの時間をプログラムが計算し、効果試算に反映します。",
            affects=["効果試算（全案）"] + calculated, important=False, numeric=True, answered=numbers_answered))

    found = merge_duplicates(found)
    for g in found:
        hit = next((answers[k] for k in [g.key, *g.merged_keys] if k in answers), None)
        if hit and hit.kind == "確認予定":
            g.planned = hit.answer
        elif hit:
            g.answered, g.planned = f"{hit.source}：{hit.answer}", None
    # 未回答の重要なもの → 未回答の参考 → 確認予定 → 回答済み（同じ重さの中は上の順）
    return sorted(found, key=lambda g: (bool(g.answered), bool(g.planned), not g.important))


# ---------------------------------------------------------------
# 同じ確認項目の統合（AI-BIC 3.2）：ヒアリングの判定・課題の追加質問・数値の一覧から、同じことを別々に聞かない
# ---------------------------------------------------------------
_UNITS = [("日", r"日数|何日|[0-9０-９]+日"), ("件", r"件数|何件"), ("回", r"回数|何回"), ("時間", r"何時間|何分|作業時間|時間"),
          ("人", r"人数|何人"), ("円", r"人件費|費用|円")]
_PERIODS = [("月", r"月"), ("週", r"週"), ("年", r"年"), ("日", r"1日|毎日|日あたり")]
_GENERIC = {"教えて", "ください", "確認", "くらい", "ありますか", "ですか", "どれくらい", "何日", "何件", "何回", "何時間",
            "何分", "月間", "月あたり", "発生", "回答", "ヒアリング", "集計", "おおよそ", "構いません"}


def signature(text: str) -> tuple[str | None, str | None, set[str]]:
    """確認項目の手がかり：(単位, 期間, 内容の言葉)。単位と期間が同じで、内容の言葉が重なれば同じ確認項目とみなす。"""
    unit = next((u for u, pattern in _UNITS if re.search(pattern, text)), None)
    period = next((p for p, pattern in _PERIODS if re.search(pattern, text)), None)
    words = {w for w in re.findall(r"[一-龥ァ-ヴー]{2,}", text) if w not in _GENERIC}
    return unit, period, words


def same_item(a: Gap, b: Gap) -> bool:
    """同じ確認項目か。単位（日・件など）や期間（月・日など）が違えば、別の項目（例：月間発生日数と月間漏れ件数）。"""
    if similarity(a.question, b.question) >= 0.6:
        return True
    ua, pa, wa = signature(a.question)
    ub, pb, wb = signature(b.question)
    if not ua or ua != ub or pa != pb:
        return False
    return any(x in y or y in x for x in wa for y in wb)


def merge_duplicates(gaps: list[Gap]) -> list[Gap]:
    """同じ確認項目を1件にまとめる。数値で答える項目を残し、ほかの確認理由と影響するもの（課題・案・試算）を引き継ぐ。"""
    kept: list[Gap] = []
    for g in gaps:
        twin = next((k for k in kept if same_item(k, g)), None)
        if twin is None:
            kept.append(g)
            continue
        main, other = (g, twin) if g.numeric and not twin.numeric else (twin, g)
        if main is g:
            kept[kept.index(twin)] = g
        main.merged_keys = [*main.merged_keys, other.key, *other.merged_keys]
        if other.reason and other.reason not in main.reason:
            main.reason = f"{main.reason}　{other.reason}".strip()
        main.affects = list(dict.fromkeys([*main.affects, *other.affects]))
        main.important = main.important or other.important
    return kept


def number_gap(n) -> Gap:
    """確認が必要な数値（facts.Need）の不足情報。"""
    if n.status == "確認予定":
        return Gap(key=f"fact:{n.key}", question=n.name,
                   reason="あとで確かめる予定の値です。確かめたら、ここで値を入れると関係する計算と試算を更新します。",
                   affects=n.proposals + (["効果試算"] if n.for_simulation else []) + [f"「{b}」" for b in n.blocked],
                   important=False, numeric=True, planned=n.detail)
    if n.status == "食い違い":
        question = f"{n.name}：申告と資料の値が食い違っています（{n.detail}）。正しい値を確認してください"
    elif n.status == "未集計":
        question = f"{n.name}を教えてください（ヒアリングでは「集計していない」とのことでした。おおよそでも構いません）"
    elif n.key == "hourly_cost":
        question = "時間あたり人件費を教えてください（削減できる時間を、金額に換算するのに使います）"
    else:
        question = f"{n.name}を教えてください"
    reasons = []
    if n.proposals:
        reasons.append(f"{'・'.join(n.proposals)}の効果を判断するのに必要です。")
    if n.blocked:
        reasons.append(f"「{'」「'.join(n.blocked)}」は、これが分かるまで未確定です。")
    if n.for_simulation and not n.proposals:
        reasons.append("効果試算に使います。")
    affects = n.proposals + (["効果試算"] if n.for_simulation else []) + [f"「{b}」" for b in n.blocked]
    # 改善案の効果の判断に必要な値は重要（主ボタンを「不足情報を確認する」にする）。試算の金額換算だけなら参考
    return Gap(key=f"fact:{n.key}", question=question, reason="".join(reasons), affects=affects,
               important=bool(n.proposals), numeric=True)


def build(result: DiagnosisResult, hearing: HearingState, framework: Framework,
          answers: dict | None = None, numbers_answered: str | None = None, needs: list | None = None) -> Overview:
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
        gaps=gaps(result, hearing, framework, answers, numbers_answered, needs),
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
    genai_label: str = ""  # 生成AI活用・生成AIを併用（生成AIを使わない案は空。AI-BIC 3.4）
    genai_step: str = ""  # 生成AIの対象工程
    genai_effect: str = ""  # 生成AIの効果の見込み
    genai_condition: str = ""  # 利用条件（承認の確認が必要か）


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
        g = p.genai
        rows.append(CompareRow(
            no=no, name=tab_name(p), title=p.title, category=p.category, reduces=reduces, reduces_from_issues=from_issues,
            check_before=p.prerequisites[0] if p.prerequisites else "特になし",
            more_checks=max(len(p.prerequisites) - 1, 0), relations=tags[no],
            genai_label=genai.label(p), genai_step=g.target_step if g else "", genai_effect=g.effect if g else "",
            genai_condition=genai.condition_text(p)))
    return rows


MAX_TOP_GAPS = 2  # 冒頭に出す不足情報の数（最初の画面に収めるため。残りは件数で示す）


def open_assumptions(hearing: HearingState, answers: dict | None = None, quantity_lines: list[str] | None = None) -> list[str]:
    """結果に示す仮定条件。足りなかった必須の項目に、結果画面ですべて答えた機能の仮定条件は出さない。

    quantity_lines：数値の一覧がある案件の、確認済みの値と未確定の値（facts.assumption_lines）。あれば、試算の仮定条件を
    「作業時間・頻度が未確認」とまとめて言わず、欠けている値とその影響だけを示す（AI-BIC 3.2）。
    """
    if quantity_lines is not None:
        rest = [config.FEATURE_ASSUMPTIONS[f.key] for f in evaluate(hearing)
                if f.key != "simulation" and f.level == "不足" and any(f"item:{i}" not in (answers or {}) for i in f.missing_required)]
        # すべて確認済みの値なら、仮定ではないので出さない。未確定の値があるときだけ、分かっている範囲と一緒に示す
        undetermined = any("未確定" in x or "計算できません" in x for x in quantity_lines)
        return ([f"試算の前提：{'。'.join(quantity_lines)}。"] if undetermined else []) + rest
    answers = answers or {}
    return [config.FEATURE_ASSUMPTIONS[f.key] for f in evaluate(hearing)
            if f.level == "不足" and any(f"item:{i}" not in answers for i in f.missing_required)]
