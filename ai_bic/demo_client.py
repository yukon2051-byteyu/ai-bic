"""デモモード用のAIクライアント（F05）。

AIを呼ばず、config/demo_scenarios.json の固定データを返す。
本物の ClaudeClient と同じ3つの関数を持つので、hearing.py はそのまま使える。
利用者がどう回答しても、質問と結果は固定（画面に「デモモード」と常に表示する）。

デモの分析結果も、本物と同じく output_check.verify_evidences で引用を照合する。
シナリオには、わざと根拠が確認できない「事実」の課題を含めており、
照合の仕組みで「要確認」に格下げされる様子を確認できる。
"""

import json

from ai_bic.config import CONFIG_DIR
from ai_bic.models import (
    AnalysisResult,
    Attachment,
    BusinessInfo,
    FlowResult,
    Framework,
    HearingState,
    ItemStatus,
    ProposalResult,
    QuestionResult,
)


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
        if state.extra_started:
            return self._extra_question(state, framework, final)
        turns = self.scenario["turns"]
        n = len(state.turns)  # これまでの質問数で、次に返すデータを決める

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

        # 用意した質問をすべて終えたら「全項目充足」で終了する
        return QuestionResult(
            item_status=_statuses({}, framework, default="充足"),
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
                question=t["question"], answer_example=t["answer_example"],
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

    def make_flows(
        self, info: BusinessInfo, state: HearingState, analysis: AnalysisResult, proposals: ProposalResult
    ) -> FlowResult:
        """事前に作った業務フロー（AI-BIC 2.0 機能B）。本物と同じく、flow_ai.to_flows で発言と照合する。"""
        return FlowResult.model_validate(self.scenario["flows"])


def demo_answer(scenario: dict, turn_no: int) -> str:
    """回答欄にあらかじめ入れておく、シナリオの回答（決定事項E2）。"""
    turns = scenario["turns"] + scenario.get("extra_turns", [])  # 追加確認の回答は、基本の質問の後に続く
    return turns[turn_no - 1]["demo_answer"] if 1 <= turn_no <= len(turns) else ""


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
