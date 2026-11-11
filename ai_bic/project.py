"""案件データ（AI-BIC 2.0 9.1）：保存・読み込みと「古い結果」の管理。

1つの案件に、ヒアリング・診断結果・業務フロー・効果試算などをまとめて持つ。

【古い結果の管理（指示書 6章）】
結果の種類ごとに「版番号」と「作ったときに使った元の結果の版番号」を記録する（Stamp）。
元の結果（例：改善案）が変わると版番号が合わなくなるため、
「再計算が必要」「再レビューが必要」と判定できる。元が古くなった結果を元にした結果も古いとみなす。

【保存】
サーバーには保存しない（公開環境は再起動でファイルが消えるため）。
利用者が JSON ファイルをダウンロードし、あとで読み込んで再開する。
ファイルには版番号（schema_version）を入れ、古い版は変換してから読み込む。
"""

import json
import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

from ai_bic import config
from ai_bic.flow import Flow
from ai_bic.models import (
    BusinessInfo,
    DiagnosisResult,
    HearingState,
    Level,
    Proposal,
    ReviewResult,
    ReviewViewpoint,
    Verdict,
)
from ai_bic.simulation import Scenario, SimulationInputs, ValueSource

SCHEMA_VERSION = 2  # 2：不足情報の回答・確認した数値・確定した案の記録を追加（AI-BIC 3.0）
APP_NAME = "AI-BIC"

# 結果の種類 → 作るときに使う元の結果。案件全体で1つのもの（as_is）と、改善案ごとのものがある
PER_PROPOSAL = ("proposal", "premise", "to_be", "simulation", "review")
DEPENDS: dict[str, tuple[str, ...]] = {
    "proposal": (),
    "premise": (),  # 前提：不足情報の確認で、その案に関係する情報が分かったとき（AI-BIC 3.0）
    "as_is": (),
    "to_be": ("proposal", "as_is"),
    "simulation": ("proposal", "as_is", "to_be"),
    "review": ("proposal", "premise", "to_be", "simulation"),
}
NAMES = {"proposal": "改善案", "premise": "前提", "as_is": "現状の業務フロー", "to_be": "改善後の業務フロー", "simulation": "効果試算", "review": "レビュー"}
OUTDATED_STATUS = {
    "to_be": "再作成が必要",
    "simulation": "再計算が必要",
    "review": "再レビューが必要",
}
Status = Literal["未着手", "完了", "再作成が必要", "再計算が必要", "再レビューが必要"]


class Stamp(BaseModel):
    version: int = 0
    updated_at: datetime | None = None
    based_on: dict[str, int] = Field(default_factory=dict)  # 元の結果のキー → 作ったときのその版番号


Decision = Literal["採用", "保留", "却下"]


class RuleFinding(BaseModel):
    """プログラムの検証で見つけた問題（AIの判断ではない。review.py）。"""

    viewpoint: ReviewViewpoint
    severity: Level
    message: str


class ReviewRecord(BaseModel):
    """1つの改善案のレビューの記録（AI-BIC 2.0 機能D）。"""

    result: ReviewResult  # AIのレビュー（指摘の根拠は照合済み）
    unverified: list[int] = Field(default_factory=list)  # 根拠の引用を確認できなかった指摘の番号（0から）
    rule_findings: list[RuleFinding] = Field(default_factory=list)
    ai_verdict: Verdict  # AIの総合判定
    verdict: Verdict  # プログラムの検証を反映した総合判定（画面に表示する）
    override_reasons: list[str] = Field(default_factory=list)  # 判定を変えた理由
    decisions: dict[int, Decision] = Field(default_factory=dict)  # 指摘の番号 → 採用・保留・却下
    created_at: datetime


Source = Literal["本人の申告", "添付資料"]  # 不足情報の確認で得た情報の出どころ（申告と資料を区別する）


class FactEntry(BaseModel):
    """不足情報の確認で得た数値（例：月の作業時間の合計）。出どころごとに最新の1つを持つ。"""

    value: float
    source: Source
    note: str = ""  # 資料の名前など
    at: datetime


class GapAnswer(BaseModel):
    """不足情報への文章の回答（overview.Gap の key ごと）。"""

    question: str
    answer: str  # 確認予定のときは、その予定（例：1か月記録して確かめる）
    kind: Literal["回答", "確認予定"] = "回答"  # 確認予定は回答済みにも確認済みの値にもしない（AI-BIC 3.2）
    source: Source
    note: str = ""
    affects: list[str] = Field(default_factory=list)  # 回答したときに影響すると示したもの
    at: datetime


class DecisionRecord(BaseModel):
    """確定した改善案の記録。確定した時点の内容を残し、あとで前提が変わったら再確認を求める（AI-BIC 3.0）。"""

    pid: str
    proposal: Proposal  # 確定した時点の改善案
    simulation: SimulationInputs | None = None  # 確定した時点の試算の入力値
    versions: dict[str, int] = Field(default_factory=dict)  # 確定した時点の版（proposal・premise・simulation）
    decided_at: datetime


class TrialMetric(BaseModel):
    """試行で測る項目。試算値・目標値・実測値は別の値として持つ（混ぜない。AI-BIC 3.0）。

    数値：時間などの数値。改善前の値・試算値・目標値・実測値。direction で「以下なら合格／以上なら合格」
    確認：例外処理の成功・失敗など。合格条件の文と、確認した結果（成功・失敗）
    """

    name: str
    kind: Literal["数値", "確認"] = "数値"
    unit: str = ""
    required: bool = True  # 必須の測定（未入力なら判定保留）
    baseline: float | None = None  # 改善前の値
    estimate: float | None = None  # 試算値（効果試算から。参考）
    target: float | None = None  # 目標値（合格条件）
    direction: Literal["以下", "以上"] = "以下"
    actual: float | None = None  # 実測値（未測定なら None）
    check_target: str = ""  # 確認の合格条件
    check_result: Literal["成功", "失敗"] | None = None  # 確認の結果（未確認なら None）
    note: str = ""


TrialDecision = Literal["展開する", "修正して再試行", "見送る"]


class TrialPlan(BaseModel):
    """確定した案を小さく試す計画と、その結果・判断（AI-BIC 3.0）。追加のAI呼び出しはしない。"""

    pid: str
    scope: str = ""  # 試行範囲（例：1台分のCSVで試す）
    owner: str = ""  # 担当
    due: date | None = None  # 期限
    risks: list[str] = Field(default_factory=list)  # 想定リスク（確定した案から引き継ぐ）
    assumptions: list[str] = Field(default_factory=list)  # 試算の前提（確定した時点の試算から引き継ぐ）
    metrics: list[TrialMetric] = Field(default_factory=list)
    sample: bool = False  # デモ用の数値（画面とExcelで明示する）
    decision: TrialDecision | None = None
    reason: str = ""
    decided_at: datetime | None = None
    created_at: datetime


class Project(BaseModel):
    schema_version: int = SCHEMA_VERSION
    app: Literal["AI-BIC"] = APP_NAME
    project_id: str
    mode: Literal["ai", "demo"]  # デモの案件と実案件を混ぜない（デモの案件ではAIを呼ばない）
    demo_scenario_id: str | None = None
    created_at: datetime
    updated_at: datetime
    business_info: BusinessInfo
    hearing: HearingState  # 診断の根拠にしたヒアリング（デモを途中で終えた場合は、全問回答のサンプル）
    diagnosis: DiagnosisResult
    # デモでヒアリングを途中で終えた場合の、利用者が実際に答えたところまでの記録（判定には使わない）。それ以外は None
    partial_hearing: HearingState | None = None
    as_is: Flow | None = None
    to_be: dict[str, Flow] = Field(default_factory=dict)  # 改善案ID（P1…）→ 改善後のフロー
    simulations: dict[str, SimulationInputs] = Field(default_factory=dict)  # 改善案ID → 試算の入力値
    scenarios: dict[str, list[Scenario]] = Field(default_factory=dict)  # 改善案ID → 3シナリオ
    kept_controls: dict[str, list[str]] = Field(default_factory=dict)  # 改善案ID → 統制のために残した工程と理由
    setup_tasks: dict[str, list[str]] = Field(default_factory=dict)  # 改善案ID → 初回だけの準備作業（初期費用の参考）
    case_unit: str = ""  # 業務フローの「1件」の意味（例：精算書1枚）。工程の時間は1件あたり
    cases_per_month: float | None = None  # 業務フローから分かる月の件数
    cases_source: ValueSource | None = None  # 月の件数の出どころ
    flow_questions: list[str] = Field(default_factory=list)  # 業務フローについてAIが確認したいこと
    flow_notes: list[str] = Field(default_factory=list)  # 照合で格下げしたことなど（プログラムが記録）
    ai_calls: dict[str, int] = Field(default_factory=dict)  # 種類（flow など）→ AIを呼んで成功した回数
    reviews: dict[str, ReviewRecord] = Field(default_factory=dict)  # 改善案ID → 最新のレビュー
    proposal_history: dict[str, list[Proposal]] = Field(default_factory=dict)  # 改善案ID → 作り直す前の改善案（古い順）
    revision_notes: dict[str, list[str]] = Field(default_factory=dict)  # 改善案ID → 最後の作り直しで変えたこと
    selected_proposal: str | None = None  # 確定した改善案（STEP 6）
    decision: DecisionRecord | None = None  # 確定した時点の内容（版2から。版1のファイルでは None）
    facts: dict[str, list[FactEntry]] = Field(default_factory=dict)  # 確認した数値の名前 → 出どころごとの値
    gap_answers: dict[str, GapAnswer] = Field(default_factory=dict)  # 不足情報の key → 回答
    # 確認した数値で試算の元の値が変わったが、利用者が手入力した値を残した項目（改善案ID → 項目名 → 確認した値）
    basis_changes: dict[str, dict[str, float]] = Field(default_factory=dict)
    trial: TrialPlan | None = None  # 確定した案の試行と効果測定（版2から）
    stamps: dict[str, Stamp] = Field(default_factory=dict)  # 結果のキー（例：simulation:P1）→ 版の記録

    @model_validator(mode="after")
    def _known_proposals(self) -> "Project":
        ids = set(proposal_ids(self))
        for name in ("to_be", "simulations", "scenarios", "kept_controls", "setup_tasks", "reviews",
                     "proposal_history", "revision_notes", "basis_changes"):
            unknown = set(getattr(self, name)) - ids
            if unknown:
                raise ValueError(f"存在しない改善案のデータがあります（{name}：{'、'.join(sorted(unknown))}）")
        if self.selected_proposal is not None and self.selected_proposal not in ids:
            raise ValueError("確定した改善案が存在しません")
        if self.decision is not None and self.decision.pid != self.selected_proposal:
            raise ValueError("確定した改善案の記録が一致しません")
        if self.trial is not None and self.trial.pid not in ids:
            raise ValueError("試行の対象の改善案が存在しません")
        if self.mode == "demo" and not self.demo_scenario_id:
            raise ValueError("デモの案件にシナリオの記録がありません")
        return self


def proposal_ids(project: Project) -> list[str]:
    """改善案のID（P1, P2 …）。改善案は並び順で識別する。"""
    proposals = project.diagnosis.proposals
    return [f"P{no}" for no in range(1, len(proposals.proposals) + 1)] if proposals else []


def now() -> datetime:
    return datetime.now(config.JST)


def new_project(
    mode: str,
    info: BusinessInfo,
    hearing: HearingState,
    diagnosis: DiagnosisResult,
    demo_scenario_id: str | None = None,
) -> Project:
    """診断が終わったときに案件を作る。改善案は版1として記録する。"""
    created = now()
    project = Project(
        project_id=uuid.uuid4().hex, mode=mode, demo_scenario_id=demo_scenario_id,
        created_at=created, updated_at=created, business_info=info, hearing=hearing, diagnosis=diagnosis,
    )
    for pid in proposal_ids(project):
        mark_updated(project, "proposal", pid)
    return project


# ---------------------------------------------------------------
# 版の記録と「古い結果」の判定
# ---------------------------------------------------------------
def key(kind: str, pid: str | None = None) -> str:
    """結果のキー。改善案ごとの結果は「simulation:P1」の形。"""
    if kind not in DEPENDS:
        raise ValueError(f"不明な結果の種類です：{kind}")
    if kind in PER_PROPOSAL:
        if pid is None:
            raise ValueError(f"{kind} には改善案IDが必要です")
        return f"{kind}:{pid}"
    return kind


def version(project: Project, kind: str, pid: str | None = None) -> int:
    stamp = project.stamps.get(key(kind, pid))
    return stamp.version if stamp else 0


def mark_updated(project: Project, kind: str, pid: str | None = None) -> None:
    """結果を作った・変えたことを記録する（版番号を1つ上げ、元の結果の今の版番号を控える）。"""
    k = key(kind, pid)
    old = project.stamps.get(k)
    project.stamps[k] = Stamp(
        version=(old.version if old else 0) + 1,
        updated_at=now(),
        based_on={key(dep, pid): version(project, dep, pid) for dep in DEPENDS[kind]},
    )
    project.updated_at = now()


def outdated_reasons(project: Project, kind: str, pid: str | None = None) -> list[str]:
    """結果が古くなった理由（変わった元の結果の名前）。古くなければ空。未作成でも空。"""
    stamp = project.stamps.get(key(kind, pid))
    if stamp is None:
        return []
    reasons = []
    for dep in DEPENDS[kind]:
        dep_key = key(dep, pid)
        if version(project, dep, pid) != stamp.based_on.get(dep_key, 0):
            reasons.append(f"{NAMES[dep]}が変更されました")
        elif outdated_reasons(project, dep, pid):
            reasons.append(f"{NAMES[dep]}が古くなっています")
    return reasons


def status(project: Project, kind: str, pid: str | None = None) -> Status:
    if key(kind, pid) not in project.stamps:
        return "未着手"
    if outdated_reasons(project, kind, pid):
        return OUTDATED_STATUS[kind]
    return "完了"


# ---------------------------------------------------------------
# 保存（JSON）と読み込み
# ---------------------------------------------------------------
class ProjectFileError(Exception):
    """読み込めない案件ファイル。message は画面にそのまま表示できる文。"""


# 古い版のファイルを1つ新しい版の形に変換する関数（版番号 → 関数）。版を上げるときに追加する
def _v1_to_v2(raw: dict) -> dict:
    """版1 → 版2：新しい項目（確認した数値・不足情報の回答・確定した時点の記録）は空で読み込む。"""
    return raw


MIGRATIONS: dict[int, callable] = {1: _v1_to_v2}


def to_json_bytes(project: Project) -> bytes:
    return project.model_dump_json(indent=2).encode("utf-8")


def file_name(project: Project) -> str:
    stamp = project.updated_at.astimezone(config.JST).strftime("%Y%m%d_%H%M")
    prefix = "AI-BIC_デモ案件" if project.mode == "demo" else "AI-BIC_案件"
    return f"{prefix}_{stamp}.json"


def load_project(data: bytes) -> Project:
    """案件ファイルを読み込む。読めなければ ProjectFileError（中身は表示しない）。"""
    if len(data) > config.MAX_PROJECT_FILE_BYTES:
        raise ProjectFileError(f"ファイルが大きすぎます（{config.MAX_PROJECT_FILE_BYTES // 1024 // 1024}MBまで）")
    try:
        raw = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ProjectFileError("JSON形式のファイルとして読み込めませんでした") from None
    if not isinstance(raw, dict) or raw.get("app") != APP_NAME:
        raise ProjectFileError("AI-BIC で保存した案件ファイルではありません")
    file_version = raw.get("schema_version")
    if not isinstance(file_version, int) or isinstance(file_version, bool) or file_version < 1:
        raise ProjectFileError("ファイルの版番号が読み取れません")
    if file_version > SCHEMA_VERSION:
        raise ProjectFileError("このアプリより新しい版で保存されたファイルのため、読み込めません")
    while file_version < SCHEMA_VERSION:
        if file_version not in MIGRATIONS:
            raise ProjectFileError(f"版{file_version}のファイルは読み込みに対応していません")
        raw = MIGRATIONS[file_version](raw)
        file_version += 1
        raw["schema_version"] = file_version
    try:
        return Project.model_validate(raw)
    except ValidationError as e:
        # 内容（利用者の入力）は表示せず、件数と場所だけを伝える
        places = sorted({str(err["loc"][0]) for err in e.errors() if err["loc"]})
        raise ProjectFileError(
            f"ファイルの内容に{e.error_count()}件の問題があり、読み込めませんでした（{'、'.join(places)}）"
        ) from None
