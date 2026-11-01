"""テスト用の偽物のAIクライアント（基本設計 13章・方針P4）。

本物の ClaudeClient と同じ3つの関数を持ち、あらかじめ渡した答えを順に返す。
AIを呼ばないので料金はかからず、毎回同じ結果になる。
エラー（AIError）をわざと発生させて、異常系も試せる。
"""

from ai_bic.errors import AIError
from ai_bic.models import AnalysisResult, ItemStatus, ProposalResult, QuestionResult

ITEM_IDS = ["H1", "H2", "H3", "H4", "H5", "H6"]


def make_question(
    statuses: dict | None = None,
    next_item_id: str | None = "H5",
    is_sufficient: bool = False,
    question: str | None = "どのような場面で困っていますか？",
) -> QuestionResult:
    """質問生成の結果を作る。statuses に書かなかった項目は「未確認」。"""
    statuses = statuses or {}
    return QuestionResult(
        item_status=[
            ItemStatus(item_id=i, status=statuses.get(i, "未確認"), known_facts="")
            for i in ITEM_IDS
        ],
        is_sufficient=is_sufficient,
        next_item_id=next_item_id,
        question_type="新規" if next_item_id else None,
        question=question if next_item_id else None,
        answer_example="例：月末に数字が合わず、確認に半日かかる" if next_item_id else None,
    )


class FakeClient:
    """渡した答え（またはAIError）を順に返す偽物。呼ばれた回数も数える。"""

    def __init__(self, questions=None, analysis=None, proposals=None):
        self.questions = list(questions or [])  # QuestionResult または AIError の並び
        self.analysis = analysis  # AnalysisResult または AIError
        self.proposals = proposals  # ProposalResult または AIError
        self.calls = {"question": 0, "analysis": 0, "proposal": 0}

    @staticmethod
    def _give(value):
        if isinstance(value, AIError):
            raise value
        return value

    def ask_next_question(self, info, state, framework) -> QuestionResult:
        self.calls["question"] += 1
        return self._give(self.questions.pop(0))

    def analyze(self, info, state, framework) -> AnalysisResult:
        self.calls["analysis"] += 1
        return self._give(self.analysis)

    def propose(self, info, state, analysis) -> ProposalResult:
        self.calls["proposal"] += 1
        return self._give(self.proposals)
