"""ヒアリングの進行管理（F02・F07、基本設計書 6章）。

ハイブリッド型ヒアリングの「プログラムが確実に守る部分」を担当する。
- 質問回数を数え、上限（5回）に達したらAIを呼ばずに終了する
- AIの充足判定を記録し、終了してよいかをプログラムが確認する
- 判定に反映していない回答が残ったまま終了したら、診断の前に判定だけをやり直す
- 回答前に同じ質問を再要求されても、AIを呼び直さない（二重呼び出しの防止 K04）

AIクライアント（本物・デモ・テスト用の偽物）が誰かは知らない。
AIClient の3つの関数だけを使う（基本設計の方針P4）。
"""

from typing import Protocol

from ai_bic.config import MAX_EXTRA_QUESTIONS, MAX_HEARING_TURNS
from ai_bic.item_rules import askable_item_ids
from ai_bic.models import (
    AnalysisResult,
    Attachment,
    BusinessInfo,
    DiagnosisResult,
    FlowResult,
    Framework,
    HearingItem,
    HearingState,
    HearingTurn,
    ItemStatus,
    ProposalResult,
    QuestionResult,
)
from ai_bic.output_check import verify_evidences
from ai_bic.readiness import evaluate, extra_count, extra_targets

# 終了理由（結果画面と利用状況の確認に使う）
FINISH_MAX_TURNS = "質問回数の上限に達したため終了しました"
FINISH_SUFFICIENT = "必要な情報がそろったため終了しました"
FINISH_BY_USER = "利用者の操作により、ここまでの情報で診断しました"
FINISH_NO_ITEMS = "これ以上お聞きできる項目がないため終了しました"
# 追加確認（AI-BIC 2.0）の終了理由
EXTRA_SUFFICIENT = "追加確認で、分析に必要な情報がそろいました"
EXTRA_MAX = "追加確認の上限に達しました"
EXTRA_BY_USER = "利用者の操作により、追加確認を終えて分析に進みました"
EXTRA_NO_ITEMS = "追加でお聞きできる項目がないため、追加確認を終えました"


class AIClient(Protocol):
    """AIクライアントの共通の呼び出し方（詳細設計 3.2）。

    失敗したときは、どの関数も errors.AIError を発生させる。
    """

    def ask_next_question(
        self, info: BusinessInfo, state: HearingState, framework: Framework, final: bool = False
    ) -> QuestionResult:
        """final=True のときは、質問を作らず充足状況の判定だけを行う（ヒアリング終了後）。"""
        ...

    def analyze(
        self,
        info: BusinessInfo,
        state: HearingState,
        framework: Framework,
        attachments: list[Attachment],
    ) -> AnalysisResult: ...

    def propose(
        self, info: BusinessInfo, state: HearingState, analysis: AnalysisResult
    ) -> ProposalResult: ...

    def make_flows(
        self, info: BusinessInfo, state: HearingState, analysis: AnalysisResult, proposals: ProposalResult
    ) -> FlowResult:
        """業務フロー（As-Is と改善案ごとの To-Be）。AI-BIC 2.0 機能B。"""
        ...


def start_hearing(framework: Framework) -> HearingState:
    """全診断項目を「未確認」にした、ヒアリングの記録を作る。"""
    return HearingState(
        item_status={
            item.id: ItemStatus(item_id=item.id, status="未確認", known_facts="", lacking="")
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
    _record_statuses(state, result)

    reason = _finish_reason(result, state, framework)
    if reason:
        state.finished_reason = reason
        return None

    turn = HearingTurn(
        turn_no=len(state.turns) + 1,
        item_id=result.next_item_id,
        question=result.question,
        answer_example=result.answer_example,
    )
    state.turns.append(turn)
    return turn


def _answered_count(state: HearingState) -> int:
    return sum(1 for turn in state.turns if turn.answer is not None)


def _record_statuses(state: HearingState, result: QuestionResult) -> None:
    """AIの充足判定を記録する（次回のAIに渡し、再質問を防ぐ：R3）。"""
    for status in result.item_status:
        state.item_status[status.item_id] = status
    state.judged_answers = _answered_count(state)


def judge_last_answers(
    client: AIClient, info: BusinessInfo, state: HearingState, framework: Framework
) -> None:
    """判定に反映していない回答があれば、充足状況の判定だけをAIにやり直させる（B01）。

    質問回数の上限で終了したときや、利用者が「ここまでの情報で診断する」を押したときは、
    最後の回答のあとにAIを呼ばないため、その回答が充足状況に反映されていなかった。
    """
    if _answered_count(state) > state.judged_answers:
        _record_statuses(state, client.ask_next_question(info, state, framework, final=True))


def _finish_reason(result: QuestionResult, state: HearingState, framework: Framework) -> str | None:
    """ヒアリングを終えてよければ終了理由を、続けるなら None を返す（基本設計 6.3）。

    - 全項目が「充足」なら終了
    - まだ質問してよい項目が残っていなければ終了（上限に達した・「分からない」と言われた項目しかない）
    - AIが「十分」と判断しても、まだ聞ける「未確認」の項目が残っていれば続ける
    """
    statuses = {s.item_id: s.status for s in result.item_status}
    if all(s == "充足" for s in statuses.values()):
        return FINISH_SUFFICIENT
    askable = askable_item_ids(state, framework, statuses)
    if not askable:
        return FINISH_NO_ITEMS
    if result.is_sufficient and not any(statuses.get(i) == "未確認" for i in askable):
        return FINISH_SUFFICIENT
    return None


def add_answer(state: HearingState, answer: str) -> None:
    """最新の質問に回答を記録する。入力チェックは validation.validate_answer で済ませておくこと。"""
    if not state.turns or state.turns[-1].answer is not None:
        raise ValueError("回答する質問がありません")
    state.turns[-1].answer = answer.strip()


def finish_by_user(state: HearingState) -> None:
    """利用者が「ここまでの情報で診断する」を押したときの終了処理。"""
    if not state.finished_reason:
        state.finished_reason = FINISH_BY_USER
    if state.extra_started and not state.extra_finished_reason:
        state.extra_finished_reason = EXTRA_BY_USER


# ---------------------------------------------------------------
# 追加確認（AI-BIC 2.0）：基本の質問のあと、3機能に必要な情報が足りないときに、利用者が選んだ場合だけ行う
# ---------------------------------------------------------------
def start_extra(state: HearingState, framework: Framework) -> None:
    """追加確認を始める。質問してよい項目を、機能に必要な情報が足りない項目に絞る。"""
    if not state.finished_reason:
        raise ValueError("基本のヒアリングが終わっていません")
    state.extra_started = True
    state.extra_targets = extra_targets(state, framework)


def request_extra_question(
    client: AIClient, info: BusinessInfo, state: HearingState, framework: Framework
) -> HearingTurn | None:
    """追加確認の次の質問を返す。終える場合は None を返し、理由を記録する。

    回数（最大 MAX_EXTRA_QUESTIONS 問）と、聞く項目はプログラムが決める。質問の文はAIが作る。
    答えるたびにAIが充足状況を判定し直し、必要な情報がそろえば、上限の前でも終える。
    """
    if not state.extra_started or state.extra_finished_reason:
        return None
    if state.turns and state.turns[-1].answer is None:
        return state.turns[-1]
    if extra_count(state) >= MAX_EXTRA_QUESTIONS:
        state.extra_finished_reason = EXTRA_MAX
        return None

    result = client.ask_next_question(info, state, framework)
    _record_statuses(state, result)
    state.extra_targets = extra_targets(state, framework)  # 新しい判定で、まだ足りない項目に絞り直す
    if not state.extra_targets or not (result.next_item_id in state.extra_targets and result.question):
        # 「聞ける項目がない」と「必要な情報がそろった」を区別する。必須の項目が不足したまま聞けなくなったのに
        # 「そろいました」と表示していた（本物のAIでの確認で見つけた）
        lacking = any(f.missing_required for f in evaluate(state))
        state.extra_finished_reason = EXTRA_NO_ITEMS if lacking else EXTRA_SUFFICIENT
        return None

    turn = HearingTurn(
        turn_no=len(state.turns) + 1,
        item_id=result.next_item_id,
        question=result.question,
        answer_example=result.answer_example,
        extra=True,
    )
    state.turns.append(turn)
    return turn


def missing_items(state: HearingState, framework: Framework) -> list[HearingItem]:
    """「充足」でない診断項目を返す（R6。AIを使わずプログラムで計算する）。"""
    return [
        item
        for item in framework.hearing_items
        if state.item_status.get(item.id) is None or state.item_status[item.id].status != "充足"
    ]


def run_diagnosis(
    client: AIClient,
    info: BusinessInfo,
    state: HearingState,
    framework: Framework,
    attachments: list[Attachment] | None = None,
    cache: dict | None = None,
) -> DiagnosisResult:
    """充足状況の最終判定 → 課題分析 → 根拠の照合 → 改善提案 を順に行い、結果をまとめて返す（F03・F04）。

    添付ファイル（F09）は課題分析にだけ渡し、引用の照合にも使う。
    課題分析と結果画面の不足項目は、どちらも最終判定のあとの充足状況を使う。

    cache（画面が診断ごとに持つ空の dict）を渡すと、成功した課題分析の結果をそこに残す。
    改善提案で失敗して再試行したときは、同じ添付ファイルなら課題分析をやり直さない（X01：費用の重複を防ぐ）。
    """
    if not state.finished_reason:
        raise ValueError("ヒアリングが終了していません")
    attachments = attachments or []

    judge_last_answers(client, info, state, framework)

    if cache and cache.get("attachments") == attachments:
        checked, downgraded = cache["analysis"]
    else:
        analysis = client.analyze(info, state, framework, attachments)
        checked, downgraded = verify_evidences(analysis, info, state, attachments)
        if cache is not None:
            cache.update(attachments=attachments, analysis=(checked, downgraded))

    # 課題が0件なら改善提案のAIを呼ばない（費用削減・課題の捏造防止）
    proposals = client.propose(info, state, checked) if checked.issues else None

    return DiagnosisResult(
        attachment_names=[a.name for a in attachments],
        analysis=checked,
        proposals=proposals,
        downgraded=downgraded,
        missing_item_ids=[item.id for item in missing_items(state, framework)],
    )
