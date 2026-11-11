"""デモモード用のAIクライアント（F05）。

AIを呼ばず、config/demo_scenarios.json の固定データを返す。
本物の ClaudeClient と同じ3つの関数を持つので、hearing.py はそのまま使える。
利用者がどう回答しても、質問と結果は固定（画面に「デモモード」と常に表示する）。

デモの分析結果も、本物と同じく output_check.verify_evidences で引用を照合する。
シナリオには、わざと根拠が確認できない「事実」の課題を含めており、
照合の仕組みで「要確認」に格下げされる様子を確認できる。
"""

import json

from ai_bic.config import CONFIG_DIR, MAX_FIRST_STEPS
from ai_bic.hearing import add_answer, request_extra_question, request_next_question, start_extra, start_hearing
from ai_bic.item_rules import blocked_reason, is_planned_answer, is_unknown_answer
from ai_bic.models import (
    AnalysisResult,
    Attachment,
    BusinessInfo,
    FlowResult,
    Framework,
    HearingState,
    ItemStatus,
    Proposal,
    ProposalResult,
    QuestionResult,
    ReviewFinding,
    ReviewResult,
    RevisedProposal,
)
from ai_bic.readiness import should_offer_extra


def load_scenarios() -> list[dict]:
    """デモ用の架空シナリオの一覧を返す。"""
    path = CONFIG_DIR / "demo_scenarios.json"
    return json.loads(path.read_text(encoding="utf-8"))["scenarios"]


class DemoClient:
    """選んだシナリオの固定データを返すAIクライアント。"""

    def __init__(self, scenario: dict):
        self.scenario = scenario

    def ask_next_question(
        self, info: BusinessInfo, state: HearingState, framework: Framework, final: bool = False
    ) -> QuestionResult:
        """シナリオの質問と充足状況を返す。「分からない」と答えた項目は、シナリオの値によらず「不十分」のままにする。

        固定の充足状況だけを返すと、「分からない」と答えた項目まで「充足」になり、未確認の前提として
        結果に引き継げなかった（本物のAIには、質問の指示文で同じ扱いを求めている）。
        """
        result = self._ask(state, framework, final)
        # 「分からない」と「確認予定（あとで確かめる）」の項目は、シナリオの値によらず「充足」にしない（AI-BIC 3.2）
        unknown = {t.item_id for t in state.turns
                   if t.answer and (is_unknown_answer(t.answer) or t.kind == "確認予定" or is_planned_answer(t.answer))}
        for status in result.item_status:
            if status.item_id in unknown and status.status == "充足":
                status.status = "不十分"
                status.lacking = "ヒアリングで「分からない」または「あとで確認する」と回答された"
        return result

    def _ask(self, state: HearingState, framework: Framework, final: bool) -> QuestionResult:
        if state.extra_started:
            return self._extra_question(state, framework, final)
        turns = self.scenario["turns"]
        n = _next_index(turns, state)  # 次に返す用意した質問の番号

        # 用意した質問をすべて終えたあとの最終判定は、シナリオの final_statuses を使う
        # （書いていなければ「全項目充足」）
        if final and n >= len(turns):
            return QuestionResult(
                item_status=_statuses(
                    self.scenario.get("final_statuses", {}),
                    framework,
                    default="充足",
                    lacking=self.scenario.get("final_lacking", {}),
                ),
                is_sufficient=True,
                next_item_id=None,
                question_type=None,
                question=None,
                answer_example=None,
            )

        if n < len(turns):
            t = turns[n]
            return QuestionResult(
                item_status=_statuses(t["statuses"], framework),
                is_sufficient=False,
                next_item_id=t["next_item_id"],
                question_type=t["question_type"],
                question=t["question"],
                answer_example=t["answer_example"],
            )

        # 用意した質問をすべて終えたら終了する。充足状況はシナリオの final_statuses（書いていなければ「全項目充足」）。
        # 最後の判定（final）と同じにしないと、質問を使い切らずに終えたシナリオで追加確認を提案できない
        return QuestionResult(
            item_status=_statuses(
                self.scenario.get("final_statuses", {}), framework, default="充足",
                lacking=self.scenario.get("final_lacking", {}),
            ),
            is_sufficient=True,
            next_item_id=None,
            question_type=None,
            question=None,
            answer_example=None,
        )

    def _extra_question(self, state: HearingState, framework: Framework, final: bool) -> QuestionResult:
        """追加確認（AI-BIC 2.0）。シナリオの extra_turns を順に返し、終えたら最後の判定を返す。"""
        extra = self.scenario.get("extra_turns", [])
        done = sum(1 for turn in state.turns if turn.extra)
        if not final and done < len(extra):
            t = extra[done]
            return QuestionResult(
                item_status=_statuses(t["statuses"], framework, lacking=t.get("lacking", {})),
                is_sufficient=False, next_item_id=t["next_item_id"], question_type=t["question_type"],
                question=t["question"], answer_example=t["answer_example"], decides=t.get("decides"),
            )
        after = extra[done - 1].get("statuses_after", {}) if done else self.scenario.get("final_statuses", {})
        return QuestionResult(
            item_status=_statuses(after, framework, default="充足"),
            is_sufficient=True, next_item_id=None, question_type=None, question=None, answer_example=None,
        )

    def analyze(
        self,
        info: BusinessInfo,
        state: HearingState,
        framework: Framework,
        attachments: list[Attachment] | None = None,
    ) -> AnalysisResult:
        return AnalysisResult.model_validate(self.scenario["analysis"])

    def propose(
        self, info: BusinessInfo, state: HearingState, analysis: AnalysisResult
    ) -> ProposalResult:
        return ProposalResult.model_validate(self.scenario["proposals"])

    def review(
        self, info: BusinessInfo, state: HearingState, analysis: AnalysisResult, proposal_no: int, material: str
    ) -> ReviewResult:
        """事前に作ったレビュー（AI-BIC 2.0 機能D）。プログラムの検証・判定の上書き・根拠の照合は本物と同じく行う。"""
        return ReviewResult.model_validate(self.scenario["reviews"][proposal_no - 1])

    def revise(
        self, info: BusinessInfo, state: HearingState, analysis: AnalysisResult, proposal_no: int,
        proposal: Proposal, adopted: list[ReviewFinding],
    ) -> RevisedProposal:
        """デモの作り直し：AIを使わず、採用した指摘の改善提案を前提条件と最初の一歩に加えた例を作る。"""
        steps = list(proposal.first_steps)
        for f in adopted:
            if len(steps) >= MAX_FIRST_STEPS:
                break
            steps.append(f.suggestion)
        revised = proposal.model_copy(update={
            "prerequisites": proposal.prerequisites + [f.suggestion for f in adopted],
            "risks": proposal.risks + [f.title for f in adopted if f.viewpoint in ("業務運用リスク", "セキュリティ・統制")],
            "first_steps": steps[:MAX_FIRST_STEPS],
        })
        return RevisedProposal(
            proposal=revised,
            changes=[f"「{f.title}」への対応として、前提条件に「{f.suggestion}」を加えました（デモ：AIを使わない機械的な例）"
                     for f in adopted],
        )

    def make_flows(
        self, info: BusinessInfo, state: HearingState, analysis: AnalysisResult, proposals: ProposalResult
    ) -> FlowResult:
        """事前に作った業務フロー（AI-BIC 2.0 機能B）。本物と同じく、flow_ai.to_flows で発言と照合する。"""
        return FlowResult.model_validate(self.scenario["flows"])


def _next_index(turns: list[dict], state: HearingState) -> int:
    """次に返す、用意した基本の質問の番号。

    最後に聞いた質問の次から始め、「分からない」「確認予定」と答えてもう聞けない項目の質問は飛ばす
    （デモでも、同じ項目を聞き直さない。AI-BIC 3.2）。
    """
    asked = [t.question for t in state.turns if not t.extra]
    index = 0
    for no, t in enumerate(turns):
        if t["question"] in asked:
            index = no + 1
    while index < len(turns) and blocked_reason(state, turns[index]["next_item_id"]) is not None:
        index += 1
    return index


def demo_answer(scenario: dict, turn_no: int, question: str | None = None) -> str:
    """回答欄にあらかじめ入れておく、シナリオの回答（決定事項E2）。

    question があれば、その質問の回答を返す（質問を飛ばしたときに、回の番号と回答がずれないように）。
    """
    turns = scenario["turns"] + scenario.get("extra_turns", [])  # 追加確認の回答は、基本の質問の後に続く
    if question is not None:
        hit = next((t["demo_answer"] for t in turns if t["question"] == question), None)
        if hit is not None:
            return hit
    return turns[turn_no - 1]["demo_answer"] if 1 <= turn_no <= len(turns) else ""


def answered_all_basic(scenario: dict, state: HearingState) -> bool:
    """用意した基本の質問に、すべて答えたか（途中で終えていないか）。"""
    answered = sum(1 for t in state.turns if not t.extra and t.answer is not None)
    return answered >= len(scenario["turns"])


def sample_hearing(scenario: dict, framework: Framework) -> tuple[BusinessInfo, HearingState]:
    """全問（基本と追加確認）にサンプルの回答で答えた場合のヒアリング（AI-BIC 3.0）。

    デモでヒアリングを途中で終えても、結果は全問回答を前提にした固定の内容のため、
    その根拠はこのサンプル回答と照合する（途中までの入力と照合すると、同じ引用が「確認済み」と
    「根拠不足」に分かれてしまう）。AIは呼ばない。
    """
    client = DemoClient(scenario)
    info = BusinessInfo(**scenario["business_info"])
    state = start_hearing(framework)
    while (turn := request_next_question(client, info, state, framework)) is not None:
        add_answer(state, demo_answer(scenario, turn.turn_no, turn.question))
    if should_offer_extra(state, framework):
        start_extra(state, framework)
        while (turn := request_extra_question(client, info, state, framework)) is not None:
            add_answer(state, demo_answer(scenario, turn.turn_no, turn.question))
    return info, state


def demo_attachments(scenario: dict) -> list[Attachment]:
    """シナリオの見本ファイル（F09。添付済みの状態で表示し、根拠の照合にも使う）。"""
    return [Attachment(**a) for a in scenario.get("attachments", [])]


def _statuses(
    values: dict, framework: Framework, default: str = "未確認", lacking: dict | None = None
) -> list[ItemStatus]:
    lacking = lacking or {}
    return [
        ItemStatus(
            item_id=item.id,
            status=values.get(item.id, default),
            known_facts="",
            lacking=lacking.get(item.id, ""),
        )
        for item in framework.hearing_items
    ]
