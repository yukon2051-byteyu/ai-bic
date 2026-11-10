"""overview.py のテスト（結果画面の冒頭・不足情報・改善案の比較。AI-BIC 3.0 第1段階）。AIは呼ばない。"""

from ai_bic import overview
from ai_bic.config import load_framework
from ai_bic.models import DiagnosisResult, HearingState, ItemStatus, ProposalRelation
from ai_bic.models import AnalysisResult
from tests.fakes import make_proposal, make_proposal_result
from tests.test_ui import make_issue

FW = load_framework("general")
ALL = {i: "充足" for i in ["H1", "H2", "H3", "H4", "H5", "H6"]}


def hearing(statuses: dict) -> HearingState:
    return HearingState(item_status={
        k: ItemStatus(item_id=k, status=v, known_facts="", lacking="分単位の時間" if k == "H3" else "")
        for k, v in statuses.items()})


def diagnosis(issues, proposals=None, relations=None, top="I1") -> DiagnosisResult:
    result = make_proposal_result(proposals or [make_proposal(target_issue_ids=["I1"])], top_issue_id=top)
    result.relations = relations or []
    return DiagnosisResult(attachment_names=[], analysis=AnalysisResult(business_summary="s", issues=issues,
                                                                         no_issue_viewpoints=[]),
                           proposals=result, downgraded={}, missing_item_ids=[])


# --- 冒頭と不足情報 ---
def test_最大の課題には確認状態を付け_確認済みと推測を見分けられる():
    ov = overview.build(diagnosis([make_issue(evidence_type="計算結果")]), hearing(ALL), FW)
    assert ov.top_issue.issue_id == "I1"
    assert ov.top_confirmation.startswith("確認済み")
    ov = overview.build(diagnosis([make_issue(evidence_type="推測")]), hearing(ALL), FW)
    assert ov.top_confirmation.startswith("推測")


def test_必要な情報がそろい_要確認の課題もなければ_重要な不足はない():
    ov = overview.build(diagnosis([make_issue(followup_questions=[])]), hearing(ALL), FW)
    assert ov.gaps == [] and not ov.has_important_gaps


def test_機能に必須の項目が足りなければ重要な不足情報になり_影響する機能と足りないことを示す():
    ov = overview.build(diagnosis([make_issue(followup_questions=[])]), hearing({**ALL, "H3": "不十分"}), FW)
    gap = ov.gaps[0]
    assert gap.important and gap.key == "item:H3"
    assert "作業時間・頻度" in gap.question and "分単位の時間" in gap.question
    assert gap.affects == ["改善効果の試算"]


def test_要確認の課題は_重要度高なら重要で_対象の改善案を影響先に示す():
    issues = [make_issue(issue_id="I1", evidence_type="本人の申告", followup_questions=[]),
              make_issue(issue_id="I2", title="誤りの手戻り", evidence_type="要確認", severity="高",
                         followup_questions=["誤りは月に何件ですか"]),
              make_issue(issue_id="I3", evidence_type="要確認", severity="低", followup_questions=[])]
    proposals = [make_proposal(target_issue_ids=["I1"]), make_proposal(target_issue_ids=["I2", "I3"])]
    ov = overview.build(diagnosis(issues, proposals), hearing(ALL), FW)
    first, second = ov.gaps
    assert first.important and first.question == "誤りは月に何件ですか"
    assert first.affects == ["課題「誤りの手戻り」", "案2"]
    assert not second.important and "実際に起きていますか" in second.question


def test_確認済みの重要な課題の追加確認は参考で_重要な不足より後に出す():
    issues = [make_issue(issue_id="I1", followup_questions=["設定を変えられますか"]),
              make_issue(issue_id="I2", evidence_type="要確認", severity="高", followup_questions=["件数は？"])]
    ov = overview.build(diagnosis(issues), hearing(ALL), FW)
    assert [g.key for g in ov.gaps] == ["issue:I2", "followup:I1"]
    assert [g.important for g in ov.gaps] == [True, False]


# --- 比較表と関係 ---
def test_比較表は短い名前_主に減らすもの_前提条件の1つ目を出し_なければ代わりを示す():
    issues = [make_issue(issue_id="I1", title="転記の負担")]
    proposals = [make_proposal(short_title="変換表", reduces="読み替えの誤り", prerequisites=["一覧にする", "確認する"]),
                 make_proposal(title="正式な名前", short_title=None, reduces=None, target_issue_ids=["I1"], prerequisites=[])]
    rows = overview.compare_rows(diagnosis(issues, proposals))
    assert (rows[0].name, rows[0].reduces, rows[0].check_before, rows[0].more_checks) == ("変換表", "読み替えの誤り", "一覧にする", 1)
    assert (rows[1].name, rows[1].reduces, rows[1].reduces_from_issues, rows[1].check_before) == (
        "正式な名前", "転記の負担", True, "特になし")


def test_関係は_順序なら先にやる案と次に試す案_併用なら両方に印を付ける():
    relations = [ProposalRelation(kind="順序", first=1, second=2, reason="準備になる"),
                 ProposalRelation(kind="併用", first=2, second=3, reason="同時に進められる")]
    tags = overview.relation_tags(relations, 3)
    assert [t.text for t in tags[1]] == ["案2の前に先にやる"]
    assert [t.text for t in tags[2]] == ["案1のあとに次に試す", "案3と併用できる"]
    assert [t.text for t in tags[3]] == ["案2と併用できる"]


def test_存在しない案_自分自身_理由なし_同じ2案の2つ目の関係は出さない():
    relations = [ProposalRelation(kind="順序", first=1, second=4, reason="r"),
                 ProposalRelation(kind="併用", first=2, second=2, reason="r"),
                 ProposalRelation(kind="併用", first=1, second=2, reason=" "),
                 ProposalRelation(kind="順序", first=1, second=3, reason="先"),
                 ProposalRelation(kind="順序", first=3, second=1, reason="逆向き")]
    kept = overview.valid_relations(relations, 3)
    assert [(r.first, r.second, r.reason) for r in kept] == [(1, 3, "先")]


def test_関係がない案件には_関係の印を付けない():
    tags = overview.relation_tags([], 3)
    assert tags == {1: [], 2: [], 3: []}
