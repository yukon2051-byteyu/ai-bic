"""hearing.py のテスト（F02 ヒアリング・F07 途中終了・F03/F04 の呼び出し順）。

偽物のAI（FakeClient）を使うので、料金はかからない。
"""

import pytest

from ai_bic.config import MAX_HEARING_TURNS, load_framework
from ai_bic.errors import AIError
from ai_bic.hearing import (
    FINISH_BY_USER,
    FINISH_MAX_TURNS,
    FINISH_SUFFICIENT,
    add_answer,
    finish_by_user,
    missing_items,
    request_next_question,
    run_diagnosis,
    start_hearing,
)
from ai_bic.models import (
    AnalysisResult,
    Attachment,
    BusinessInfo,
    Evidence,
    Issue,
    Proposal,
    ProposalResult,
)
from tests.fakes import FakeClient, make_proposal_result, make_question

FW = load_framework("general")
INFO = BusinessInfo(
    company_name=None,
    department="営業管理部",
    task_name="月次売上集計",
    overview="各営業所から届くExcelを1つにまとめ、月次の売上報告書を作成する。",
    pain_points="転記に時間がかかり、毎月どこかで数字が合わない。",
)


def ask_and_answer(client, state, answer="回答です"):
    """質問を受け取り、回答するまでの1往復。終了したら None を返す。"""
    turn = request_next_question(client, INFO, state, FW)
    if turn is not None:
        add_answer(state, answer)
    return turn


# ---------------------------------------------------------------
# 開始と1回目の質問
# ---------------------------------------------------------------
def test_開始時は全項目が未確認():
    state = start_hearing(FW)
    assert {s.status for s in state.item_status.values()} == {"未確認"}
    assert len(state.item_status) == 6


def test_1回目の質問を受け取り_充足状況が更新される():
    client = FakeClient(questions=[make_question({"H1": "充足"}, next_item_id="H5")])
    state = start_hearing(FW)

    turn = request_next_question(client, INFO, state, FW)

    assert turn.turn_no == 1
    assert turn.item_id == "H5"
    assert turn.answer_example  # 回答例も画面に渡せる
    assert state.item_status["H1"].status == "充足"
    assert client.calls["question"] == 1


def test_回答前にもう一度求めても_AIを呼び直さない():
    # Streamlit の再実行で同じ処理が2回走っても、料金が二重にかからないこと（K04）
    client = FakeClient(questions=[make_question()])
    state = start_hearing(FW)

    first = request_next_question(client, INFO, state, FW)
    second = request_next_question(client, INFO, state, FW)

    assert first is second
    assert client.calls["question"] == 1


# ---------------------------------------------------------------
# 終了条件
# ---------------------------------------------------------------
def test_5回質問したら_6回目はAIを呼ばずに終了する():
    client = FakeClient(questions=[make_question() for _ in range(MAX_HEARING_TURNS)])
    state = start_hearing(FW)

    for _ in range(MAX_HEARING_TURNS):
        assert ask_and_answer(client, state) is not None

    assert request_next_question(client, INFO, state, FW) is None
    assert state.finished_reason == FINISH_MAX_TURNS
    assert client.calls["question"] == MAX_HEARING_TURNS  # 6回目は呼んでいない


def test_全項目が充足したら5回未満でも終了する():
    all_ok = {i: "充足" for i in ["H1", "H2", "H3", "H4", "H5", "H6"]}
    client = FakeClient(
        questions=[
            make_question(next_item_id="H5"),
            make_question(all_ok, next_item_id=None, is_sufficient=True),
        ]
    )
    state = start_hearing(FW)

    ask_and_answer(client, state)
    assert request_next_question(client, INFO, state, FW) is None
    assert state.finished_reason == FINISH_SUFFICIENT
    assert len(state.turns) == 1


def test_AIが十分と判断し_未確認がなければ終了する():
    # 「不十分」はあっても「未確認」がなければ、AIの判断で終えてよい
    statuses = {i: "充足" for i in ["H1", "H2", "H3", "H4", "H5"]} | {"H6": "不十分"}
    client = FakeClient(questions=[make_question(statuses, next_item_id=None, is_sufficient=True)])
    state = start_hearing(FW)

    assert request_next_question(client, INFO, state, FW) is None
    assert state.finished_reason == FINISH_SUFFICIENT


def test_AIが十分と判断しても_未確認が残っていれば続ける():
    client = FakeClient(
        questions=[make_question({"H1": "充足"}, next_item_id="H6", is_sufficient=True)]
    )
    state = start_hearing(FW)

    turn = request_next_question(client, INFO, state, FW)
    assert turn is not None
    assert turn.item_id == "H6"
    assert state.finished_reason is None


# ---------------------------------------------------------------
# 途中終了（F07）
# ---------------------------------------------------------------
def test_利用者が途中で終了したら_それ以上AIを呼ばない():
    client = FakeClient(questions=[make_question(), make_question()])
    state = start_hearing(FW)
    ask_and_answer(client, state)

    finish_by_user(state)

    assert state.finished_reason == FINISH_BY_USER
    assert request_next_question(client, INFO, state, FW) is None
    assert client.calls["question"] == 1


def test_すでに終了していれば終了理由は上書きしない():
    state = start_hearing(FW)
    state.finished_reason = FINISH_MAX_TURNS
    finish_by_user(state)
    assert state.finished_reason == FINISH_MAX_TURNS


# ---------------------------------------------------------------
# 回答の記録
# ---------------------------------------------------------------
def test_回答は前後の空白を除いて記録する():
    client = FakeClient(questions=[make_question()])
    state = start_hearing(FW)
    request_next_question(client, INFO, state, FW)
    add_answer(state, "  月末に半日かかる\n")
    assert state.turns[0].answer == "月末に半日かかる"


def test_質問がないのに回答したらエラー():
    with pytest.raises(ValueError):
        add_answer(start_hearing(FW), "回答")


def test_同じ質問に2回回答したらエラー():
    client = FakeClient(questions=[make_question()])
    state = start_hearing(FW)
    ask_and_answer(client, state)
    with pytest.raises(ValueError):
        add_answer(state, "2回目の回答")


# ---------------------------------------------------------------
# 異常系：AIのエラー
# ---------------------------------------------------------------
def test_AIでエラーが起きても_それまでの記録は消えない():
    client = FakeClient(questions=[make_question({"H1": "充足"}), AIError("connection")])
    state = start_hearing(FW)
    ask_and_answer(client, state, "1回目の回答")

    with pytest.raises(AIError):
        request_next_question(client, INFO, state, FW)

    assert len(state.turns) == 1
    assert state.turns[0].answer == "1回目の回答"
    assert state.item_status["H1"].status == "充足"
    assert state.finished_reason is None  # 再試行できる状態のまま


def test_エラーの後に再試行すれば続きから質問できる():
    client = FakeClient(questions=[AIError("rate_limit"), make_question()])
    state = start_hearing(FW)

    with pytest.raises(AIError):
        request_next_question(client, INFO, state, FW)
    turn = request_next_question(client, INFO, state, FW)

    assert turn.turn_no == 1


# ---------------------------------------------------------------
# 不足項目（R6）
# ---------------------------------------------------------------
def test_充足していない項目を不足として返す():
    client = FakeClient(
        questions=[make_question({"H1": "充足", "H2": "充足", "H3": "不十分"}, next_item_id="H3")]
    )
    state = start_hearing(FW)
    request_next_question(client, INFO, state, FW)

    assert [item.id for item in missing_items(state, FW)] == ["H3", "H4", "H5", "H6"]


# ---------------------------------------------------------------
# 診断（分析 → 照合 → 提案）
# ---------------------------------------------------------------
def make_issue(issue_id, quote) -> Issue:
    return Issue(
        issue_id=issue_id,
        title="課題",
        summary="",
        viewpoint_ids=["V1"],
        evidence_type="本人の申告",
        evidences=[Evidence(source="業務情報", turn_no=None, file_no=None, quote=quote, supports="")],
        calculation=None,
        assumed_cause="",
        impact="",
        severity="中",
        severity_reason="",
        followup_questions=[],
    )


def make_proposals() -> ProposalResult:
    return make_proposal_result()


def finished_state():
    state = start_hearing(FW)
    state.finished_reason = FINISH_BY_USER
    return state


def test_診断で分析と提案が行われ_根拠のない本人の申告は格下げされる():
    analysis = AnalysisResult(
        business_summary="",
        issues=[make_issue("I1", "数字が合わない"), make_issue("I2", "利用者が言っていない内容")],
        no_issue_viewpoints=[],
    )
    client = FakeClient(analysis=analysis, proposals=make_proposals())

    result = run_diagnosis(client, INFO, finished_state(), FW)

    assert list(result.downgraded) == ["I2"]
    assert result.analysis.issues[1].evidence_type == "要確認"
    assert result.proposals is not None
    assert result.missing_item_ids == ["H1", "H2", "H3", "H4", "H5", "H6"]
    assert client.calls == {"question": 0, "analysis": 1, "proposal": 1}


def test_添付ファイルは課題分析に渡り_結果にファイル名が残る():
    files = [Attachment(name="記録.csv", text="数字が合わない月の一覧")]
    analysis = AnalysisResult(business_summary="", issues=[], no_issue_viewpoints=[])
    client = FakeClient(analysis=analysis)

    result = run_diagnosis(client, INFO, finished_state(), FW, files)

    assert client.received_attachments == files
    assert result.attachment_names == ["記録.csv"]


def test_添付なしでも診断できる():
    client = FakeClient(
        analysis=AnalysisResult(business_summary="", issues=[], no_issue_viewpoints=[])
    )
    result = run_diagnosis(client, INFO, finished_state(), FW)
    assert client.received_attachments == []
    assert result.attachment_names == []


def test_課題が0件なら提案のAIを呼ばない():
    client = FakeClient(
        analysis=AnalysisResult(business_summary="", issues=[], no_issue_viewpoints=["V1"])
    )
    result = run_diagnosis(client, INFO, finished_state(), FW)

    assert result.proposals is None
    assert client.calls["proposal"] == 0


def test_ヒアリング終了前に診断しようとしたらエラー():
    with pytest.raises(ValueError):
        run_diagnosis(FakeClient(), INFO, start_hearing(FW), FW)


def test_提案でエラーが起きたらAIErrorが伝わる():
    analysis = AnalysisResult(
        business_summary="", issues=[make_issue("I1", "数字が合わない")], no_issue_viewpoints=[]
    )
    client = FakeClient(analysis=analysis, proposals=AIError("server"))
    with pytest.raises(AIError) as e:
        run_diagnosis(client, INFO, finished_state(), FW)
    assert e.value.kind == "server"


# ---------------------------------------------------------------
# 最後の回答の判定（B01：目的を回答したのに「情報が不足」と表示された不具合）
# ---------------------------------------------------------------
NO_ISSUES = AnalysisResult(business_summary="", issues=[], no_issue_viewpoints=[])
ALL_SUFFICIENT = {i: "充足" for i in ["H1", "H2", "H3", "H4", "H5", "H6"]}


def hear_until_max_turns(client):
    """上限の5問まで回答し、6回目でAIを呼ばずに終了した状態にする。"""
    state = start_hearing(FW)
    while ask_and_answer(client, state) is not None:
        pass
    assert state.finished_reason == FINISH_MAX_TURNS
    return state


def test_上限で終わったら_診断の前に最後の回答を判定し_不足表示に反映する():
    questions = [make_question({"H2": "充足"}, next_item_id="H1") for _ in range(MAX_HEARING_TURNS)]
    final = make_question(ALL_SUFFICIENT, next_item_id=None, is_sufficient=True)  # 5問目の回答で目的も充足
    client = FakeClient(questions=[*questions, final], analysis=NO_ISSUES)
    state = hear_until_max_turns(client)

    result = run_diagnosis(client, INFO, state, FW)

    assert client.final_calls == 1
    assert result.missing_item_ids == []  # 回答済みの項目を「不足」と表示しない


def test_最後の回答を判定しても足りなければ_不足表示は残る():
    questions = [make_question({"H2": "充足"}, next_item_id="H1") for _ in range(MAX_HEARING_TURNS)]
    final = make_question({**ALL_SUFFICIENT, "H1": "不十分"}, next_item_id=None, is_sufficient=True)
    client = FakeClient(questions=[*questions, final], analysis=NO_ISSUES)
    state = hear_until_max_turns(client)

    result = run_diagnosis(client, INFO, state, FW)

    assert result.missing_item_ids == ["H1"]  # 本当に目的が分からないケースは不足のまま


def test_回答のあとにAIが判定して終わったなら_判定し直さない():
    client = FakeClient(
        questions=[make_question(), make_question(ALL_SUFFICIENT, next_item_id=None, is_sufficient=True)],
        analysis=NO_ISSUES,
    )
    state = start_hearing(FW)
    ask_and_answer(client, state)
    ask_and_answer(client, state)  # 1問目の回答を判定して「全項目充足」で終了
    assert state.finished_reason == FINISH_SUFFICIENT

    run_diagnosis(client, INFO, state, FW)

    assert client.final_calls == 0
    assert client.calls["question"] == 2


def test_回答前の質問を残して途中終了したら_判定し直さない():
    client = FakeClient(questions=[make_question(), make_question()], analysis=NO_ISSUES)
    state = start_hearing(FW)
    ask_and_answer(client, state)
    request_next_question(client, INFO, state, FW)  # 2問目を表示したが、回答せずに終了
    finish_by_user(state)

    run_diagnosis(client, INFO, state, FW)

    assert client.final_calls == 0  # 1問目の回答は2問目を作るときに判定済み


def test_回答直後の質問作成で失敗し_途中終了したら_最後の回答を判定する():
    client = FakeClient(
        questions=[make_question(), AIError("timeout"), make_question(ALL_SUFFICIENT, next_item_id=None)],
        analysis=NO_ISSUES,
    )
    state = start_hearing(FW)
    ask_and_answer(client, state)
    with pytest.raises(AIError):
        request_next_question(client, INFO, state, FW)
    finish_by_user(state)

    result = run_diagnosis(client, INFO, state, FW)

    assert client.final_calls == 1
    assert result.missing_item_ids == []


def test_最後の回答の判定でエラーが起きたら_診断のAIは呼ばない():
    questions = [make_question(next_item_id="H1") for _ in range(MAX_HEARING_TURNS)]
    client = FakeClient(questions=[*questions, AIError("timeout")], analysis=NO_ISSUES)
    state = hear_until_max_turns(client)

    with pytest.raises(AIError):
        run_diagnosis(client, INFO, state, FW)
    assert client.calls["analysis"] == 0


# ---------------------------------------------------------------
# 再試行で課題分析をやり直さない（X01：費用の重複を防ぐ）
# ---------------------------------------------------------------
def test_提案で失敗して再試行しても_成功した課題分析はやり直さない():
    analysis = AnalysisResult(business_summary="", issues=[make_issue("I1", "数字が合わない")], no_issue_viewpoints=[])
    client = FakeClient(analysis=analysis, proposals=AIError("timeout"))
    state, cache = finished_state(), {}

    with pytest.raises(AIError):
        run_diagnosis(client, INFO, state, FW, cache=cache)
    client.proposals = make_proposals()  # 再試行では提案が成功する
    result = run_diagnosis(client, INFO, state, FW, cache=cache)

    assert client.calls == {"question": 0, "analysis": 1, "proposal": 2}
    assert result.proposals is not None


def test_添付ファイルを変えて診断し直したら_課題分析もやり直す():
    analysis = AnalysisResult(business_summary="", issues=[make_issue("I1", "数字が合わない")], no_issue_viewpoints=[])
    client = FakeClient(analysis=analysis, proposals=AIError("timeout"))
    state, cache = finished_state(), {}

    with pytest.raises(AIError):
        run_diagnosis(client, INFO, state, FW, cache=cache)
    client.proposals = make_proposals()
    run_diagnosis(client, INFO, state, FW, [Attachment(name="記録.csv", text="月,件数")], cache=cache)

    assert client.calls["analysis"] == 2


def test_置き場所を渡さなければ_毎回課題分析を行う():
    analysis = AnalysisResult(business_summary="", issues=[make_issue("I1", "数字が合わない")], no_issue_viewpoints=[])
    client = FakeClient(analysis=analysis, proposals=make_proposals())
    state = finished_state()
    run_diagnosis(client, INFO, state, FW)
    run_diagnosis(client, INFO, state, FW)
    assert client.calls["analysis"] == 2
