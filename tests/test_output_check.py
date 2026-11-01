"""output_check.py と errors.py のテスト（AI出力の内容チェック）。

AIの回答の代わりに、テスト用のデータを直接作って確認する（AIは呼ばない・料金ゼロ）。
"""

import pytest

from ai_bic.config import load_framework
from ai_bic.errors import AIError, is_retryable, user_message
from ai_bic.models import (
    AnalysisResult,
    BusinessInfo,
    Evidence,
    HearingState,
    HearingTurn,
    Issue,
    ItemStatus,
    Proposal,
    ProposalResult,
    QuestionResult,
)
from ai_bic.output_check import (
    check_analysis,
    check_proposals,
    check_question_result,
    normalize,
    verify_evidences,
)

from tests.fakes import make_proposal, make_proposal_result

FW = load_framework("general")
ITEM_IDS = ["H1", "H2", "H3", "H4", "H5", "H6"]


# ---------------------------------------------------------------
# テスト用データを作る関数
# ---------------------------------------------------------------
def question_result(statuses: dict | None = None, **override) -> QuestionResult:
    """質問生成の結果。statuses で一部の項目の状態を上書きできる。"""
    statuses = statuses or {}
    data = {
        "item_status": [
            ItemStatus(item_id=i, status=statuses.get(i, "未確認"), known_facts="") for i in ITEM_IDS
        ],
        "is_sufficient": False,
        "next_item_id": "H5",
        "question_type": "新規",
        "question": "どのような場面で困っていますか？",
        "answer_example": "例：月末に数字が合わず、確認に半日かかる",
    }
    data.update(override)
    return QuestionResult(**data)


def business_info() -> BusinessInfo:
    return BusinessInfo(
        company_name=None,
        department="営業管理部",
        task_name="月次売上集計",
        overview="各営業所から届くＥｘｃｅｌを1つにまとめ、月次の売上報告書を作成する。",
        pain_points="転記に時間がかかり、毎月どこかで数字が合わない。",
    )


def hearing_state() -> HearingState:
    return HearingState(
        turns=[
            HearingTurn(turn_no=1, item_id="H3", question="時間は？", answer="毎月3営業日目に2人で半日ほど"),
            HearingTurn(
                turn_no=2, item_id="H6", question="制約は？", answer="新しいソフトは購入できません"
            ),
        ]
    )


def issue(issue_id="I1", evidence_type="事実", evidences=None, viewpoint_ids=None) -> Issue:
    return Issue(
        issue_id=issue_id,
        title="転記作業の負担",
        summary="",
        viewpoint_ids=viewpoint_ids if viewpoint_ids is not None else ["V1"],
        evidence_type=evidence_type,
        evidences=evidences if evidences is not None else [],
        assumed_cause="",
        impact="",
        severity="高",
        severity_reason="",
        followup_questions=[],
    )


def analysis(*issues: Issue, no_issue_viewpoints=None) -> AnalysisResult:
    return AnalysisResult(
        business_summary="", issues=list(issues), no_issue_viewpoints=no_issue_viewpoints or []
    )


def proposal(**override) -> Proposal:
    return make_proposal(**override)


def proposals(*items: Proposal, **summary_override) -> ProposalResult:
    return make_proposal_result(list(items), **summary_override)


# ---------------------------------------------------------------
# normalize（照合用の文字の統一）
# ---------------------------------------------------------------
def test_全角英数字を半角にし_空白と改行を除く():
    assert normalize("ＥＸＣＥＬ　で\n集計 ３回") == "EXCELで集計3回"


def test_Noneは空文字になる():
    assert normalize(None) == ""


# ---------------------------------------------------------------
# ① 質問生成のチェック
# ---------------------------------------------------------------
def test_正しい質問結果は合格():
    assert check_question_result(question_result(), HearingState(), FW) == []


def test_診断項目が欠けていたら違反():
    result = question_result()
    result.item_status = result.item_status[:5]  # H6 が欠けている
    assert check_question_result(result, HearingState(), FW)


def test_診断項目が重複していたら違反():
    result = question_result()
    result.item_status[5] = ItemStatus(item_id="H1", status="未確認", known_facts="")
    assert check_question_result(result, HearingState(), FW)


def test_継続なのに質問がなければ違反():
    result = question_result(next_item_id=None, question=None)
    assert check_question_result(result, HearingState(), FW)


def test_質問文が空白だけなら違反():
    assert check_question_result(question_result(question="  "), HearingState(), FW)


def test_存在しない診断項目を質問したら違反():
    problems = check_question_result(question_result(next_item_id="H9"), HearingState(), FW)
    assert any("存在しない" in p for p in problems)


def test_充足済みの項目を再び質問したら違反():
    result = question_result({"H5": "充足"}, next_item_id="H5")
    problems = check_question_result(result, HearingState(), FW)
    assert any("充足済み" in p for p in problems)


def asked(*item_ids) -> HearingState:
    """指定した項目を順に質問・回答済みのヒアリング記録。"""
    return HearingState(
        turns=[
            HearingTurn(turn_no=n, item_id=i, question="質問", answer="分かりません")
            for n, i in enumerate(item_ids, start=1)
        ]
    )


def test_同じ項目を2回聞いた後に_3回目を聞こうとしたら違反():
    # 実際のAIで、「分かりません」と答えた使用ツール（H4）を3回聞き続けた問題への対策
    result = question_result({"H4": "不十分"}, next_item_id="H4", question_type="確認")
    problems = check_question_result(result, asked("H3", "H4", "H4"), FW)
    assert any("2回質問済み" in p for p in problems)


def test_同じ項目でも1回しか聞いていなければ_確認の質問はできる():
    result = question_result({"H4": "不十分"}, next_item_id="H4", question_type="確認")
    assert check_question_result(result, asked("H3", "H4"), FW) == []


def test_他の項目を2回聞いていても_別の項目なら質問できる():
    result = question_result({"H4": "不十分"}, next_item_id="H6")
    assert check_question_result(result, asked("H4", "H4"), FW) == []


def test_不十分な項目への確認質問は合格():
    result = question_result({"H3": "不十分"}, next_item_id="H3", question_type="確認")
    assert check_question_result(result, HearingState(), FW) == []


def test_全項目充足で終了するなら質問なしで合格():
    result = question_result(
        {i: "充足" for i in ITEM_IDS}, is_sufficient=True, next_item_id=None, question=None
    )
    assert check_question_result(result, HearingState(), FW) == []


def test_十分と言いつつ未確認があり質問もなければ違反():
    result = question_result({"H1": "充足"}, is_sufficient=True, next_item_id=None, question=None)
    assert check_question_result(result, HearingState(), FW)


# ---------------------------------------------------------------
# ② 課題分析のチェック
# ---------------------------------------------------------------
def test_正しい分析結果は合格():
    assert check_analysis(analysis(issue("I1"), issue("I2")), FW) == []


def test_課題0件も合格_無理に課題を作らない():
    assert check_analysis(analysis(no_issue_viewpoints=["V1", "V7"]), FW) == []


def test_課題が5件を超えたら違反():
    issues = [issue(f"I{n}") for n in range(1, 7)]
    assert check_analysis(analysis(*issues), FW)


def test_課題IDの重複は違反():
    assert check_analysis(analysis(issue("I1"), issue("I1")), FW)


@pytest.mark.parametrize("viewpoint_ids", [[], ["V9"]])
def test_評価観点がない_または存在しない観点は違反(viewpoint_ids):
    assert check_analysis(analysis(issue(viewpoint_ids=viewpoint_ids)), FW)


def test_問題なしの観点に存在しないIDがあれば違反():
    assert check_analysis(analysis(no_issue_viewpoints=["V8"]), FW)


# ---------------------------------------------------------------
# ② 引用の照合（この設計の重要な工夫）
# ---------------------------------------------------------------
def ev(quote, source="業務情報", turn_no=None, file_no=None) -> Evidence:
    return Evidence(source=source, turn_no=turn_no, file_no=file_no, quote=quote)


def test_業務情報に実在する引用なら事実のまま():
    result, downgraded = verify_evidences(
        analysis(issue(evidences=[ev("転記に時間がかかり")])), business_info(), hearing_state()
    )
    assert downgraded == []
    assert result.issues[0].evidence_type == "事実"


def test_回答に実在する引用なら事実のまま():
    result, downgraded = verify_evidences(
        analysis(issue(evidences=[ev("2人で半日ほど", source="回答", turn_no=1)])),
        business_info(),
        hearing_state(),
    )
    assert downgraded == []


def test_利用者が言っていない引用は要確認に格下げ():
    # 「転記ミスが多い」とは利用者は言っていない（AIがもっともらしく補った想定）
    result, downgraded = verify_evidences(
        analysis(issue(evidences=[ev("転記ミスが多い")])), business_info(), hearing_state()
    )
    assert downgraded == ["I1"]
    assert result.issues[0].evidence_type == "要確認"


def test_根拠がない事実も要確認に格下げ():
    _, downgraded = verify_evidences(analysis(issue(evidences=[])), business_info(), hearing_state())
    assert downgraded == ["I1"]


def test_全角半角と空白の違いは吸収する():
    # 入力は全角「Ｅｘｃｅｌ」、引用は半角「Excel」で空白入り
    _, downgraded = verify_evidences(
        analysis(issue(evidences=[ev("Excelを 1つにまとめ")])), business_info(), hearing_state()
    )
    assert downgraded == []


def test_かぎかっこで囲んだ引用も照合できる():
    _, downgraded = verify_evidences(
        analysis(issue(evidences=[ev("「数字が合わない」")])), business_info(), hearing_state()
    )
    assert downgraded == []


def test_言い換えは吸収しない():
    # 入力は「時間がかかり」。「時間がかかる」は一字一句一致しない（決定事項C1）
    _, downgraded = verify_evidences(
        analysis(issue(evidences=[ev("転記に時間がかかる")])), business_info(), hearing_state()
    )
    assert downgraded == ["I1"]


def test_短すぎる引用は根拠と認めない():
    _, downgraded = verify_evidences(
        analysis(issue(evidences=[ev("転記")])), business_info(), hearing_state()
    )
    assert downgraded == ["I1"]


def test_回答の引用は指定された回の回答とだけ照合する():
    # 「購入できません」は2回目の回答。1回目と指定されていたら確認できない
    _, downgraded = verify_evidences(
        analysis(issue(evidences=[ev("購入できません", source="回答", turn_no=1)])),
        business_info(),
        hearing_state(),
    )
    assert downgraded == ["I1"]


def test_存在しない回の回答を指定したら格下げ():
    _, downgraded = verify_evidences(
        analysis(issue(evidences=[ev("購入できません", source="回答", turn_no=9)])),
        business_info(),
        hearing_state(),
    )
    assert downgraded == ["I1"]


def test_複数の引用のうち1つでも確認できれば事実のまま():
    _, downgraded = verify_evidences(
        analysis(issue(evidences=[ev("言っていない内容です"), ev("数字が合わない")])),
        business_info(),
        hearing_state(),
    )
    assert downgraded == []


def test_分かりませんだけの引用は_どの区分でも根拠から外す():
    # 本物のAIが「すみません、その点は分かりません。」を要確認の課題の根拠に引用した（改善の余地c）
    state = HearingState(
        turns=[HearingTurn(turn_no=1, item_id="H2", question="手順は？", answer="すみません、その点は分かりません。")]
    )
    unknown = ev("すみません、その点は分かりません。", source="回答", turn_no=1)
    result, _ = verify_evidences(
        analysis(issue("I1", evidence_type="要確認", evidences=[unknown])), business_info(), state
    )
    assert result.issues[0].evidences == []


def test_分かりませんの引用しかない事実の課題は格下げ():
    state = HearingState(
        turns=[HearingTurn(turn_no=1, item_id="H2", question="手順は？", answer="分かりません")]
    )
    _, downgraded = verify_evidences(
        analysis(issue(evidences=[ev("分かりません", source="回答", turn_no=1)])), business_info(), state
    )
    assert downgraded == ["I1"]


def test_推測や要確認の課題は照合しない():
    result, downgraded = verify_evidences(
        analysis(issue("I1", evidence_type="推測"), issue("I2", evidence_type="要確認")),
        business_info(),
        hearing_state(),
    )
    assert downgraded == []
    assert [i.evidence_type for i in result.issues] == ["推測", "要確認"]


def test_元の分析結果は変更しない():
    original = analysis(issue(evidences=[ev("転記ミスが多い")]))
    verify_evidences(original, business_info(), hearing_state())
    assert original.issues[0].evidence_type == "事実"


# ---------------------------------------------------------------
# ③ 改善提案のチェック
# ---------------------------------------------------------------
ANALYSIS_2 = analysis(issue("I1"), issue("I2"))


def test_正しい改善提案は合格():
    result = proposals(proposal(), proposal(target_issue_ids=["I1", "I2"]))
    assert check_proposals(result, ANALYSIS_2) == []


@pytest.mark.parametrize("count", [0, 4])
def test_改善案が0件や4件以上は違反(count):
    result = proposals(*[proposal() for _ in range(count)])
    assert check_proposals(result, ANALYSIS_2)


def test_対応する課題がない改善案は違反():
    assert check_proposals(proposals(proposal(target_issue_ids=[])), ANALYSIS_2)


def test_存在しない課題を指す改善案は違反():
    assert check_proposals(proposals(proposal(target_issue_ids=["I3"])), ANALYSIS_2)


@pytest.mark.parametrize("why", [None, "", "  "])
def test_新規システム導入で理由がなければ違反(why):
    result = proposals(proposal(category="新規システム導入", why_not_simpler=why))
    problems = check_proposals(result, ANALYSIS_2)
    assert any("新規システム導入" in p for p in problems)


def test_新規システム導入でも理由があれば合格():
    result = proposals(
        proposal(
            category="新規システム導入",
            why_not_simpler="拠点が20か所あり、表の統一だけでは集約の手間が残るため",
        )
    )
    assert check_proposals(result, ANALYSIS_2) == []


# --- 訴求力のための項目（診断サマリー・導入前後の比較・最初の一歩） ---


def test_サマリーが存在しない課題を最重要としたら違反():
    problems = check_proposals(proposals(proposal(), top_issue_id="I9"), ANALYSIS_2)
    assert any("サマリー" in p for p in problems)


@pytest.mark.parametrize("field", ["headline", "first_action"])
def test_サマリーの要点や最初の一手が空なら違反(field):
    problems = check_proposals(proposals(proposal(), **{field: "  "}), ANALYSIS_2)
    assert any("サマリー" in p for p in problems)


@pytest.mark.parametrize("field", ["before", "after"])
def test_導入前後の比較が空なら違反(field):
    problems = check_proposals(proposals(proposal(**{field: ""})), ANALYSIS_2)
    assert any("導入前後" in p for p in problems)


@pytest.mark.parametrize("steps", [[], ["  "], ["1", "2", "3", "4"]])
def test_最初の一歩が0個や4個以上は違反(steps):
    problems = check_proposals(proposals(proposal(first_steps=steps)), ANALYSIS_2)
    assert any("最初の一歩" in p for p in problems)


def test_最初の一歩は3個まで合格():
    result = proposals(proposal(first_steps=["様式を集める", "違いを一覧にする", "案を作る"]))
    assert check_proposals(result, ANALYSIS_2) == []


# ---------------------------------------------------------------
# errors.py
# ---------------------------------------------------------------
def test_エラーの種類ごとに日本語メッセージと再試行の有無を返す():
    assert "混み合って" in user_message("rate_limit")
    assert is_retryable("rate_limit") is True
    assert is_retryable("auth") is False  # APIキーの問題は再試行しても直らない
    assert is_retryable("refusal") is False


def test_一覧にないエラーでも落ちずにメッセージを返す():
    assert "予期しないエラー" in user_message("unknown_kind")


def test_AIErrorは種類と補足を持つ():
    err = AIError("connection", "timeout")
    assert err.kind == "connection"
    assert err.detail == "timeout"
