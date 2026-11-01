"""ヒアリングの進行管理（F02・F07、基本設計書 6章）。

ハイブリッド型ヒアリングの「プログラムが確実に守る部分」を担当する。
- 質問回数を数え、上限（5回）に達したらAIを呼ばずに終了する
- AIの充足判定を記録し、終了してよいかをプログラムが確認する
- 回答前に同じ質問を再要求されても、AIを呼び直さない（二重呼び出しの防止 K04）

AIクライアント（本物・デモ・テスト用の偽物）が誰かは知らない。
AIClient の3つの関数だけを使う（基本設計の方針P4）。
"""

from typing import Protocol

from ai_bic.config import MAX_HEARING_TURNS
from ai_bic.models import (
    AnalysisResult,
    BusinessInfo,
    DiagnosisResult,
    Framework,
    HearingItem,
    HearingState,
    HearingTurn,
    ItemStatus,
    ProposalResult,
    QuestionResult,
)
from ai_bic.output_check import verify_evidences

# 終了理由（結果画面と利用状況の確認に使う）
FINISH_MAX_TURNS = "質問回数の上限に達したため終了しました"
FINISH_SUFFICIENT = "必要な情報がそろったため終了しました"
FINISH_BY_USER = "利用者の操作により、ここまでの情報で診断しました"


class AIClient(Protocol):
    """AIクライアントの共通の呼び出し方（詳細設計 3.2）。

    失敗したときは、どの関数も errors.AIError を発生させる。
    """

    def ask_next_question(
        self, info: BusinessInfo, state: HearingState, framework: Framework
    ) -> QuestionResult: ...

    def analyze(
        self, info: BusinessInfo, state: HearingState, framework: Framework
    ) -> AnalysisResult: ...

    def propose(
        self, info: BusinessInfo, state: HearingState, analysis: AnalysisResult
    ) -> ProposalResult: ...


def start_hearing(framework: Framework) -> HearingState:
    """全診断項目を「未確認」にした、ヒアリングの記録を作る。"""
    return HearingState(
        item_status={
            item.id: ItemStatus(item_id=item.id, status="未確認", known_facts="")
            for item in framework.hearing_items
        }
    )


def request_next_question(
    client: AIClient, info: BusinessInfo, state: HearingState, framework: Framework
) -> HearingTurn | None:
    """次の質問を返す。ヒアリングを終了する場合は None を返し、終了理由を記録する。

    AIの呼び出しでエラーが起きた場合は AIError がそのまま上に伝わり、
    state は変更されない（それまでの質問と回答は消えない）。
    """
    if state.finished_reason:
        return None

    # まだ回答していない質問があれば、それをそのまま返す（AIを呼び直さない）
    if state.turns and state.turns[-1].answer is None:
        return state.turns[-1]

    # 回数の上限はプログラムで判定する（AIの判断に任せない：R5）
    if len(state.turns) >= MAX_HEARING_TURNS:
        state.finished_reason = FINISH_MAX_TURNS
        return None

    result = client.ask_next_question(info, state, framework)

    # AIの充足判定を記録する（次回のAIに渡し、再質問を防ぐ：R3）
    for status in result.item_status:
        state.item_status[status.item_id] = status

    if _can_finish(result):
        state.finished_reason = FINISH_SUFFICIENT
        return None

    turn = HearingTurn(
        turn_no=len(state.turns) + 1,
        item_id=result.next_item_id,
        question=result.question,
        answer_example=result.answer_example,
    )
    state.turns.append(turn)
    return turn


def _can_finish(result: QuestionResult) -> bool:
    """情報が十分にそろったと判断してよいかを返す（基本設計 6.3）。

    - 全項目が「充足」なら終了
    - AIが「十分」と判断しても、「未確認」の項目が残っていれば続ける
    """
    statuses = [s.status for s in result.item_status]
    if all(s == "充足" for s in statuses):
        return True
    return result.is_sufficient and "未確認" not in statuses


def add_answer(state: HearingState, answer: str) -> None:
    """最新の質問に回答を記録する。入力チェックは validation.validate_answer で済ませておくこと。"""
    if not state.turns or state.turns[-1].answer is not None:
        raise ValueError("回答する質問がありません")
    state.turns[-1].answer = answer.strip()


def finish_by_user(state: HearingState) -> None:
    """利用者が「ここまでの情報で診断する」を押したときの終了処理。"""
    if not state.finished_reason:
        state.finished_reason = FINISH_BY_USER


def missing_items(state: HearingState, framework: Framework) -> list[HearingItem]:
    """「充足」でない診断項目を返す（R6。AIを使わずプログラムで計算する）。"""
    return [
        item
        for item in framework.hearing_items
        if state.item_status.get(item.id) is None or state.item_status[item.id].status != "充足"
    ]


def run_diagnosis(
    client: AIClient, info: BusinessInfo, state: HearingState, framework: Framework
) -> DiagnosisResult:
    """課題分析 → 根拠の照合 → 改善提案 を順に行い、結果をまとめて返す（F03・F04）。"""
    if not state.finished_reason:
        raise ValueError("ヒアリングが終了していません")

    analysis = client.analyze(info, state, framework)
    checked, downgraded = verify_evidences(analysis, info, state)

    # 課題が0件なら改善提案のAIを呼ばない（費用削減・課題の捏造防止）
    proposals = client.propose(info, state, checked) if checked.issues else None

    return DiagnosisResult(
        analysis=checked,
        proposals=proposals,
        downgraded_issue_ids=downgraded,
        missing_item_ids=[item.id for item in missing_items(state, framework)],
    )
