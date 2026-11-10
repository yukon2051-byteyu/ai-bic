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
# 「本人の申告」は、発言にそう書かれていること（内容が正しいかまでは確かめていない）。B04で「事実」から改めた
EvidenceType = Literal["本人の申告", "計算結果", "推測", "要確認"]
Level = Literal["高", "中", "低"]
ProposalCategory = Literal[
    "業務ルールの見直し",
    "業務標準化",
    "既存ツールの活用",
    "自動化",
    "新規システム導入",
]
# 業務フロー（AI-BIC 2.0 機能B）。flow.py と、AIの出力の形（下の FlowResult）で共通に使う
StepKind = Literal["作業", "判断", "承認"]
# To-Be での変化（As-Is では「変更なし」）。指示書 4.3 の分類
ChangeType = Literal["変更なし", "自動化", "変更", "短縮", "追加", "廃止"]
TimeBasis = Literal["ヒアリング", "AI推定", "不明"]


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
    lacking: str  # 「充足」の条件を満たすのに足りないこと。「充足」なら空（結果画面で不足の理由として表示する）


class QuestionResult(BaseModel):
    """質問生成のAIが返す内容。"""

    item_status: list[ItemStatus]
    is_sufficient: bool
    next_item_id: str | None
    question_type: Literal["新規", "確認"] | None
    question: str | None
    answer_example: str | None
    # 追加確認の質問だけ：この回答で何が判断できるか（例：データの自動取り込みの案を採れるか）。基本の質問では None
    decides: str | None = None


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
    extra: bool = False  # 基本の質問のあとの「追加確認」の質問か（AI-BIC 2.0）
    decides: str | None = None  # 追加確認：この回答で何が判断できるか（質問の近くに表示する）


class HearingState(BaseModel):
    """ヒアリング全体の記録。"""

    turns: list[HearingTurn] = Field(default_factory=list)
    item_status: dict[str, ItemStatus] = Field(default_factory=dict)  # 項目ID → 最新の状況
    finished_reason: str | None = None  # 終了したときに理由を記録
    judged_answers: int = 0  # 充足状況の判定に反映済みの回答の数（最後の回答の判定漏れを防ぐ）
    # 追加確認（AI-BIC 2.0）：基本の質問のあと、機能に必要な情報が足りないときに、利用者が選んだ場合だけ行う
    extra_started: bool = False
    extra_targets: list[str] = Field(default_factory=list)  # 追加確認で質問してよい項目（readiness.py が決める）
    extra_finished_reason: str | None = None


# ---------------------------------------------------------------
# AIの出力②：課題分析（F03）
# ---------------------------------------------------------------
class Evidence(BaseModel):
    """判断根拠となる利用者の発言（または添付ファイル）の引用。output_check.py で実在を照合する。"""

    source: Literal["業務情報", "回答", "添付ファイル"]
    turn_no: int | None  # 回答の場合、何回目の回答か
    file_no: int | None  # 添付ファイルの場合、何番目のファイルか（照合に必要なため省略不可）
    quote: str  # 利用者の発言や添付ファイルの文字を、そのまま引用したもの
    supports: str  # この引用から言えること（引用と主張の対応を、読む人が確かめられるようにする。B04）


class Issue(BaseModel):
    """抽出された課題。"""

    issue_id: str  # I1, I2 ...（改善案から参照する）
    title: str
    summary: str
    viewpoint_ids: list[str]
    evidence_type: EvidenceType
    evidences: list[Evidence]
    calculation: str | None  # 「計算結果」の課題の計算式（例：90分×20日÷60＝30時間）。それ以外は None
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
    # 案を切り替えるタブに出す短い名前（何をする案か分かる12文字程度）。正式な名前は title。
    # 古い保存データやAIが書かなかったときは None（画面では title を省略せずに折り返して出す）
    short_title: str | None = None
    # 比較表の「主に減らすもの」（例：転記の時間と誤り）。古い保存データやAIが書かなかったときは None
    # （画面では、対象の課題の名前で代わりに示す）
    reduces: str | None = None
    category: ProposalCategory
    description: str
    target_issue_ids: list[str]
    expected_effect: str  # 画面では常に「想定」として表示する
    difficulty: Level
    risks: list[str]
    prerequisites: list[str]
    reason: str
    why_not_simpler: str | None  # 新規システム導入のときは必須（output_check.py で確認）
    before: str  # 今の業務の姿（導入前後の比較）
    after: str  # 改善後の業務の姿（想定）
    first_steps: list[str]  # 今週から始められる具体的な行動（1〜3個）


class DiagnosisSummary(BaseModel):
    """診断サマリー。結果画面の冒頭で、最も重要な課題と最初の一手を短く示す。"""

    headline: str  # 診断全体の要点（1文）
    top_issue_id: str  # 最も重要な課題のID
    first_action: str  # 最初に取り組むべきこと（1文）


class ProposalRelation(BaseModel):
    """改善案どうしの関係（比較表に「先にやる」「次に試す」「併用できる」と理由を出す）。

    順序：first の案を先にやると、second の案の準備になる（first＝先にやる、second＝次に試す）
    併用：first と second の案を同時に進められる
    案の番号は 1 から。存在しない番号などの不正な関係は、画面に出さない（overview.valid_relations）。
    """

    kind: Literal["順序", "併用"]
    first: int
    second: int
    reason: str  # 入力や改善案の内容に基づく理由（1文）


class ProposalResult(BaseModel):
    """改善提案のAIが返す内容。"""

    summary: DiagnosisSummary
    proposals: list[Proposal]
    # 関係がなければ空（すべての案件に同じ関係を付けない）。古い保存データにはない
    relations: list[ProposalRelation] = Field(default_factory=list)


# ---------------------------------------------------------------
# AIの出力④：業務フロー（AI-BIC 2.0 機能B）
# 画面で使う形（flow.Flow）には、flow_ai.py が確認・変換してから入れる
# ---------------------------------------------------------------
class AIFlowStep(BaseModel):
    """AIが返す工程。"""

    ref: str  # 工程の番号（S1, S2 …）。同じフローの中で重複しない
    name: str
    kind: StepKind
    actor: str  # 担当者・担当部門（分からなければ空）
    description: str
    system: str  # 使用システム（分からなければ空）
    manual: bool | None  # 手作業か（分からなければ null）
    minutes_per_case: float | None  # 1件あたりの作業時間（分）。分からなければ null
    time_basis: TimeBasis  # 作業時間の根拠
    evidence_quote: str | None  # 工程や時間の根拠となる、利用者の発言の引用（そのまま）
    occurrence_rate: float  # 全件のうち、この工程を行う割合（%）
    wait_or_rework: str
    problem: str  # 課題・ボトルネック（なければ空）
    needs_confirmation: bool  # ヒアリングで確かめられていない工程


class AIFlowEdge(BaseModel):
    source: str
    target: str
    label: str  # 分岐の条件など（なければ空）


class AIToBeStep(AIFlowStep):
    change: ChangeType
    change_note: str  # 改善内容（変更なしなら空）


class AIToBeFlow(BaseModel):
    proposal_no: int  # 改善案の番号（1から）
    steps: list[AIToBeStep]
    edges: list[AIFlowEdge]
    kept_controls: list[str]  # 品質管理・内部統制のために残す工程と、その理由
    # 初回だけの準備作業（手順書やひな形の作成など）。毎回の工程には入れない（試算の初期費用の参考）
    setup_tasks: list[str]


class FlowResult(BaseModel):
    """業務フローのAIが返す内容。"""

    # 「1件」の意味と月の件数。工程の時間は「1件あたり」なので、試算の件数と意味をそろえるために必要
    case_unit: str  # 例：「営業所1か所分のファイル」「修理依頼1件」
    cases_per_month: float | None  # 月に何件か。分からなければ null
    cases_basis: TimeBasis  # 件数の根拠
    cases_quote: str | None  # 件数の根拠となる、利用者の発言の引用（そのまま）
    as_is_steps: list[AIFlowStep]
    as_is_edges: list[AIFlowEdge]
    questions: list[str]  # 業務フローを確定するために、利用者に確認したいこと
    to_be: list[AIToBeFlow]


# ---------------------------------------------------------------
# AIの出力⑤：改善案チャレンジレビュー（AI-BIC 2.0 機能D）
# 改善案を作るAIとは別の指示文で、改善案の弱点や不足情報を批判的に評価させる
# ---------------------------------------------------------------
ReviewViewpoint = Literal[
    "課題との整合性", "技術的実現可能性", "費用対効果", "業務運用リスク", "セキュリティ・統制", "代替案の妥当性",
]
Verdict = Literal["推奨", "条件付き推奨", "再検討"]


class ReviewFinding(BaseModel):
    """レビューの指摘（懸念）。"""

    viewpoint: ReviewViewpoint
    severity: Level
    title: str
    detail: str
    evidence: str | None  # 根拠となる利用者の発言などの引用（そのまま）。根拠がなければ null（画面では「未確認」）
    suggestion: str  # 具体的な改善提案


class ReviewResult(BaseModel):
    """レビューのAIが返す内容。"""

    verdict: Verdict
    summary: str  # 評価の要約（2〜3文）
    findings: list[ReviewFinding]  # 懸念事項（重要度つき）
    strengths: list[str]  # 評価できる点
    missing_info: list[str]  # 不足している情報
    alternatives: list[str]  # 代替案（より低コスト・簡単な方法など）
    next_checks: list[str]  # 次に確認すべき事項


class RevisedProposal(BaseModel):
    """採用した指摘を反映して作り直した改善案（AIの出力）。"""

    proposal: Proposal
    changes: list[str]  # 何をどう変えたか（採用した指摘ごと）


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
    downgraded: dict[str, str]  # 根拠を確認できず「要確認」に格下げした課題ID → 格下げの理由
    missing_item_ids: list[str]  # 情報が不足している診断項目（R6）
