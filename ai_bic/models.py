"""データの形の定義（詳細設計書 1章）。

Pydantic の BaseModel を継承したクラスで「どんな項目を、どんな型で持つか」を定義する。
AIの回答もこの形で受け取る（構造化出力）ため、値の候補が決まっている項目は
Literal で候補を制限し、想定外の値（例：「やや高」）をはじく。

診断項目のID（H1など）や評価観点のID（V1など）は設定ファイルで増減するため、
ここでは型で制限せず、output_check.py で設定ファイルと照合する（詳細設計 1.1 方針3）。
"""

from typing import Literal

from pydantic import BaseModel, Field

# 値の候補（画面表示・プロンプトでも同じ言葉を使う）
ItemStatusValue = Literal["充足", "不十分", "未確認"]
EvidenceType = Literal["事実", "推測", "要確認"]
Level = Literal["高", "中", "低"]
ProposalCategory = Literal[
    "業務ルールの見直し",
    "業務標準化",
    "既存ツールの活用",
    "自動化",
    "新規システム導入",
]


# ---------------------------------------------------------------
# 診断フレームワーク（config/framework_*.json の形）
# ---------------------------------------------------------------
class HearingItem(BaseModel):
    """ヒアリングで確認する診断項目（H1〜H6）。"""

    id: str
    name: str
    description: str
    sufficient_when: str  # 「充足」と判断する条件。AIの判断のぶれを減らすために渡す


class Viewpoint(BaseModel):
    """課題分析の評価観点（V1〜V7）。"""

    id: str
    name: str


class Framework(BaseModel):
    """診断フレームワーク。専門診断モジュールは、このファイルを追加して増やす。"""

    framework_id: str
    name: str
    hearing_items: list[HearingItem]
    viewpoints: list[Viewpoint]


# ---------------------------------------------------------------
# 業務情報（F01の入力）
# ---------------------------------------------------------------
class BusinessInfo(BaseModel):
    """利用者が最初に入力する業務情報。入力チェックは validation.py で行う。"""

    company_name: str | None  # 任意項目。未入力は None
    department: str
    task_name: str
    overview: str
    pain_points: str


# ---------------------------------------------------------------
# AIの出力①：質問生成（F02）
# ---------------------------------------------------------------
class ItemStatus(BaseModel):
    """1つの診断項目の充足状況。"""

    item_id: str
    status: ItemStatusValue
    known_facts: str  # その項目について分かっていること（短い要約）


class QuestionResult(BaseModel):
    """質問生成のAIが返す内容。"""

    item_status: list[ItemStatus]
    is_sufficient: bool
    next_item_id: str | None
    question_type: Literal["新規", "確認"] | None
    question: str | None
    answer_example: str | None


# ---------------------------------------------------------------
# ヒアリングの記録（プログラムが管理。AIの出力ではない）
# ---------------------------------------------------------------
class HearingTurn(BaseModel):
    """質問と回答の1往復。"""

    turn_no: int
    item_id: str
    question: str
    answer_example: str | None = None  # 画面で回答欄の近くに表示する
    answer: str | None = None  # 回答前は None


class HearingState(BaseModel):
    """ヒアリング全体の記録。"""

    turns: list[HearingTurn] = Field(default_factory=list)
    item_status: dict[str, ItemStatus] = Field(default_factory=dict)  # 項目ID → 最新の状況
    finished_reason: str | None = None  # 終了したときに理由を記録


# ---------------------------------------------------------------
# AIの出力②：課題分析（F03）
# ---------------------------------------------------------------
class Evidence(BaseModel):
    """判断根拠となる利用者の発言（または添付ファイル）の引用。output_check.py で実在を照合する。"""

    source: Literal["業務情報", "回答", "添付ファイル"]
    turn_no: int | None  # 回答の場合、何回目の回答か
    file_no: int | None  # 添付ファイルの場合、何番目のファイルか（照合に必要なため省略不可）
    quote: str  # 利用者の発言や添付ファイルの文字を、そのまま引用したもの


class Issue(BaseModel):
    """抽出された課題。"""

    issue_id: str  # I1, I2 ...（改善案から参照する）
    title: str
    summary: str
    viewpoint_ids: list[str]
    evidence_type: EvidenceType
    evidences: list[Evidence]
    assumed_cause: str  # 画面では常に「推測」として表示する
    impact: str
    severity: Level
    severity_reason: str
    followup_questions: list[str]


class AnalysisResult(BaseModel):
    """課題分析のAIが返す内容。"""

    business_summary: str
    issues: list[Issue]
    no_issue_viewpoints: list[str]


# ---------------------------------------------------------------
# AIの出力③：改善提案（F04）
# ---------------------------------------------------------------
class Proposal(BaseModel):
    """改善案。"""

    title: str
    category: ProposalCategory
    description: str
    target_issue_ids: list[str]
    expected_effect: str  # 画面では常に「想定」として表示する
    difficulty: Level
    risks: list[str]
    prerequisites: list[str]
    reason: str
    why_not_simpler: str | None  # 新規システム導入のときは必須（output_check.py で確認）


class ProposalResult(BaseModel):
    """改善提案のAIが返す内容。"""

    proposals: list[Proposal]


# ---------------------------------------------------------------
# 添付ファイル（F09。プログラムが読み込む。保存はしない）
# ---------------------------------------------------------------
class Attachment(BaseModel):
    """文字として読み込んだ添付ファイル。"""

    name: str  # ファイル名
    text: str  # 取り出した文字（Excelはシートごとに、セルをタブ区切りで並べる）


# ---------------------------------------------------------------
# 診断結果のまとめ（プログラムが作る。結果画面に表示する）
# ---------------------------------------------------------------
class DiagnosisResult(BaseModel):
    """課題分析・改善提案と、画面に添える補足情報。"""

    attachment_names: list[str]  # 診断に使った添付ファイルの名前（file_no の順）
    analysis: AnalysisResult  # 引用照合（格下げ）を反映したもの
    proposals: ProposalResult | None  # 課題が0件のときは None（提案のAIを呼ばない）
    downgraded_issue_ids: list[str]  # 根拠を確認できず「要確認」に格下げした課題
    missing_item_ids: list[str]  # 情報が不足している診断項目（R6）
