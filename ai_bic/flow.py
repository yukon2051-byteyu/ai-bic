"""業務フロー（AI-BIC 2.0 機能B）のデータと操作。

- 工程（FlowStep）と接続（FlowEdge）を構造化データとして持ち、図はそこから描く（図の文字列を保存データにしない）
- 工程IDは「S1, S2 …」。削除したIDは再利用しない（next_no で管理）。編集しても接続が別の工程を指さない
- 分岐・合流・循環（手戻り）に対応する。作業時間は「工程ごとに1回だけ」数え、
  分岐や手戻りは「実施割合（全件のうち何%で行うか）」で表す。接続をたどって足さないので二重に数えない
- 操作はすべて新しい Flow を返す（元のデータを書き換えない。画面の「元に戻す」や比較に使える）
"""

from dataclasses import dataclass
from pydantic import BaseModel, Field, field_validator, model_validator

from ai_bic import config
from ai_bic.models import ChangeType, StepKind
from ai_bic.simulation import ValueSource


class FlowStep(BaseModel):
    id: str = Field(pattern=r"^S[1-9][0-9]*$")
    name: str = Field(min_length=1, max_length=config.MAX_STEP_TEXT)
    kind: StepKind = "作業"
    actor: str = Field("", max_length=config.MAX_STEP_TEXT)  # 担当者・担当部門
    description: str = Field("", max_length=config.MAX_STEP_NOTE)
    system: str = Field("", max_length=config.MAX_STEP_TEXT)  # 使用システム（Excel、メールなど）
    manual: bool | None = None  # 手作業か（None は不明）
    minutes_per_case: float | None = Field(None, ge=0)  # 1件あたりの作業時間（分）。None は不明
    minutes_source: ValueSource | None = None  # 作業時間の出どころ
    occurrence_rate: float = Field(100.0, ge=0, le=100)  # 全件のうち、この工程を行う割合（%）
    wait_or_rework: str = Field("", max_length=config.MAX_STEP_NOTE)  # 待ち時間・手戻り
    problem: str = Field("", max_length=config.MAX_STEP_NOTE)  # 課題・ボトルネック（空でなければ「問題」として表示）
    change: ChangeType = "変更なし"
    change_note: str = Field("", max_length=config.MAX_STEP_NOTE)  # 改善内容（To-Be）
    needs_confirmation: bool = False  # 「要確認」：ヒアリングで確かめられていない工程

    @field_validator("name")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("工程名を入力してください")
        return value.strip()


class FlowEdge(BaseModel):
    source: str
    target: str
    label: str = Field("", max_length=config.MAX_STEP_TEXT)  # 分岐の条件など（例：「差異あり」）


class Flow(BaseModel):
    steps: list[FlowStep] = Field(default_factory=list)  # 並び順＝画面の表示順
    edges: list[FlowEdge] = Field(default_factory=list)
    next_no: int = 1  # 次に追加する工程の番号（削除したIDを再利用しないため）

    @model_validator(mode="after")
    def _consistent(self) -> "Flow":
        """読み込んだファイルやAIの出力が壊れていないかを確かめる。"""
        ids = [s.id for s in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("工程IDが重複しています")
        if len(ids) > config.MAX_FLOW_STEPS:
            raise ValueError(f"工程は{config.MAX_FLOW_STEPS}個までです")
        known = set(ids)
        pairs = set()
        for e in self.edges:
            if e.source not in known or e.target not in known:
                raise ValueError(f"存在しない工程への接続があります（{e.source}→{e.target}）")
            if e.source == e.target:
                raise ValueError(f"工程が自分自身に接続しています（{e.source}）")
            if (e.source, e.target) in pairs:
                raise ValueError(f"同じ接続が重複しています（{e.source}→{e.target}）")
            pairs.add((e.source, e.target))
        largest = max((_no(i) for i in ids), default=0)
        if self.next_no <= largest:
            self.next_no = largest + 1  # 古いファイルなどで番号がずれていても、IDの再利用を防ぐ
        return self

    def step(self, step_id: str) -> FlowStep:
        for s in self.steps:
            if s.id == step_id:
                return s
        raise KeyError(f"工程 {step_id} がありません")


def _no(step_id: str) -> int:
    return int(step_id[1:])


# ---------------------------------------------------------------
# 編集の操作（すべて新しい Flow を返す）
# ---------------------------------------------------------------
def add_step(flow: Flow, name: str, after_id: str | None = None, connect: bool = True, **fields) -> tuple[Flow, str]:
    """工程を追加する。after_id の直後に並べ、connect なら after_id → 新工程 → (after_id の次) とつなぎ直す。

    戻り値：(新しいフロー, 追加した工程のID)
    """
    new_id = f"S{flow.next_no}"
    step = FlowStep(id=new_id, name=name, **fields)
    steps = list(flow.steps)
    edges = list(flow.edges)
    if after_id is None:
        steps.append(step)
    else:
        index = [s.id for s in steps].index(flow.step(after_id).id)
        steps.insert(index + 1, step)
        if connect:
            outgoing = [e for e in edges if e.source == after_id]
            edges = [e for e in edges if e.source != after_id]
            edges.append(FlowEdge(source=after_id, target=new_id))
            edges += [FlowEdge(source=new_id, target=e.target, label=e.label) for e in outgoing]
    return Flow(steps=steps, edges=edges, next_no=flow.next_no + 1), new_id


def delete_step(flow: Flow, step_id: str, reconnect: bool = True) -> Flow:
    """工程を削除し、その工程につながる接続も消す。

    reconnect なら、前の工程と次の工程を直接つなぐ（流れが途切れないように）。
    自分自身への接続や重複する接続は作らない。
    """
    flow.step(step_id)  # 存在しなければ KeyError
    incoming = [e for e in flow.edges if e.target == step_id]
    outgoing = [e for e in flow.edges if e.source == step_id]
    edges = [e for e in flow.edges if step_id not in (e.source, e.target)]
    if reconnect:
        existing = {(e.source, e.target) for e in edges}
        for i in incoming:
            for o in outgoing:
                pair = (i.source, o.target)
                if i.source != o.target and pair not in existing:
                    edges.append(FlowEdge(source=i.source, target=o.target, label=i.label or o.label))
                    existing.add(pair)
    steps = [s for s in flow.steps if s.id != step_id]
    return Flow(steps=steps, edges=edges, next_no=flow.next_no)


def move_step(flow: Flow, step_id: str, offset: int) -> Flow:
    """表示順を上下に動かす（offset：-1で上、+1で下）。接続は変えない。端を越える移動は何もしない。"""
    ids = [s.id for s in flow.steps]
    index = ids.index(flow.step(step_id).id)
    new_index = index + offset
    if not 0 <= new_index < len(ids):
        return flow
    steps = list(flow.steps)
    steps.insert(new_index, steps.pop(index))
    return flow.model_copy(update={"steps": steps})


def update_step(flow: Flow, step_id: str, **fields) -> Flow:
    """工程の内容を変える（ID は変えられない）。値は FlowStep の検証を通す。"""
    if "id" in fields:
        raise ValueError("工程IDは変更できません")
    old = flow.step(step_id)
    new = FlowStep.model_validate({**old.model_dump(), **fields})
    steps = [new if s.id == step_id else s for s in flow.steps]
    return flow.model_copy(update={"steps": steps})


def set_edges(flow: Flow, edges: list[FlowEdge]) -> Flow:
    """接続をまとめて置き換える（画面の接続の表から）。不正なら ValueError（Flow の検証）。"""
    return Flow(steps=flow.steps, edges=edges, next_no=flow.next_no)


# ---------------------------------------------------------------
# 集計と確認
# ---------------------------------------------------------------
@dataclass
class FlowTime:
    minutes_per_case: float  # 1件あたりの作業時間（分）。分かっている工程だけの合計
    unknown_step_ids: list[str]  # 作業時間が不明な工程（合計に含まれていない）


def time_per_case(flow: Flow, include_removed: bool = False) -> FlowTime:
    """1件あたりの作業時間の合計。

    工程ごとに「作業時間 × 実施割合」を1回だけ足す。接続をたどらないので、
    分岐・合流・手戻りがあっても同じ工程を二重に数えない。
    To-Be で「廃止」の工程は、include_removed でなければ数えない。
    """
    total = 0.0
    unknown = []
    for s in flow.steps:
        if s.change == "廃止" and not include_removed:
            continue
        if s.minutes_per_case is None:
            unknown.append(s.id)
            continue
        total += s.minutes_per_case * s.occurrence_rate / 100
    return FlowTime(minutes_per_case=total, unknown_step_ids=unknown)


def check_flow(flow: Flow) -> list[str]:
    """利用者に知らせる注意点（エラーではない）。例：どこにもつながっていない工程。"""
    warnings = []
    if not flow.steps:
        return ["工程がありません"]
    connected = {e.source for e in flow.edges} | {e.target for e in flow.edges}
    if len(flow.steps) > 1:
        for s in flow.steps:
            if s.id not in connected:
                warnings.append(f"{s.id}「{s.name}」はどの工程ともつながっていません")
    # 差し戻し（循環）があると、最初の工程にも前の工程ができる。そのため「前の工程がない工程」と
    # 「一覧の先頭の工程」の両方を出発点とし、そこからたどり着けない工程だけを知らせる
    starts = {s.id for s in flow.steps if not any(e.target == s.id for e in flow.edges)} | {flow.steps[0].id}
    reached, queue = set(starts), list(starts)
    while queue:
        current = queue.pop()
        for e in flow.edges:
            if e.source == current and e.target not in reached:
                reached.add(e.target)
                queue.append(e.target)
    unreachable = [s.id for s in flow.steps if s.id not in reached]
    if unreachable:
        warnings.append(
            f"最初の工程からたどり着けない工程があります（{'、'.join(unreachable)}）。接続を確認してください"
        )
    confirm = [s.id for s in flow.steps if s.needs_confirmation]
    if confirm:
        warnings.append(f"要確認の工程が{len(confirm)}個あります（{'、'.join(confirm)}）")
    unknown = time_per_case(flow, include_removed=True).unknown_step_ids
    if unknown:
        warnings.append(f"作業時間が不明な工程が{len(unknown)}個あります（{'、'.join(unknown)}）。合計時間には含まれません")
    return warnings


# ---------------------------------------------------------------
# 図の描画（Graphviz の DOT 言語。図は保存データにせず、表示のたびに工程データから作る）
# ---------------------------------------------------------------
# 表示の区分 → (記号つきの文字ラベル, 塗り, 線)。色だけで区別しないよう、記号と文字も付ける（指示書 4.4）
STYLE = {
    "変更なし": ("", "#F1F3F4", "#9AA0A6"),
    "問題": ("▲ 問題あり", "#FCE8E6", "#C5221F"),
    "自動化": ("◆ 自動化", "#E8F0FE", "#1967D2"),
    "変更": ("● 変更", "#E8F0FE", "#1967D2"),
    "追加": ("＋ 追加", "#E8F0FE", "#1967D2"),
    "短縮": ("▼ 短縮", "#E6F4EA", "#137333"),
    "廃止": ("× 廃止予定", "#FFFFFF", "#9AA0A6"),
}
LEGEND = [
    ("変更なし", "灰色"), ("問題", "赤"), ("自動化", "青"), ("変更", "青"), ("追加", "青"), ("短縮", "緑"), ("廃止", "破線"),
]
FONT = "Noto Sans CJK JP, Noto Sans JP, Yu Gothic, Meiryo, sans-serif"


def display_class(step: FlowStep, to_be: bool) -> str:
    """工程の表示の区分。改善後フローは変化の種類、現状フローは課題の有無で決める。"""
    if to_be and step.change != "変更なし":
        return step.change
    return "問題" if step.problem.strip() else "変更なし"


def _dot_text(text: str) -> str:
    """DOT の文字列の中で特別な意味を持つ文字を無効にする（利用者の入力やAIの出力をそのまま図に入れるため）。"""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", " ")


def step_label(step: FlowStep, to_be: bool) -> str:
    lines = []
    tag = STYLE[display_class(step, to_be)][0]
    if tag:
        lines.append(f"【{tag}】")
    if step.needs_confirmation:
        lines.append("【？ 要確認】")
    kind = "" if step.kind == "作業" else f"〈{step.kind}〉"
    lines.append(f"{step.id} {kind}{step.name}")
    if step.actor:
        lines.append(f"担当：{step.actor}")
    if step.minutes_per_case is not None:
        rate = "" if step.occurrence_rate == 100 else f"（{step.occurrence_rate:g}%の件）"
        lines.append(f"{step.minutes_per_case:g}分{rate}")
    return r"\n".join(_dot_text(line) for line in lines)  # DOT の改行は「\n」の2文字で書く


def to_dot(flow: Flow, to_be: bool = False) -> str:
    """工程データから DOT を作る。縦向き（上から下）にして、スマートフォンでも縦に読めるようにする。"""
    out = [
        "digraph flow {",
        f'  graph [rankdir=TB, bgcolor="transparent", nodesep=0.35, ranksep=0.4, fontname="{FONT}"];',
        f'  node [shape=box, style="rounded,filled", fontname="{FONT}", fontsize=11, penwidth=1.4, margin="0.15,0.08"];',
        f'  edge [color="#80868B", fontname="{FONT}", fontsize=10, arrowsize=0.7, fontcolor="#5F6368"];',
    ]
    for s in flow.steps:
        css = display_class(s, to_be)
        _, fill, line = STYLE[css]
        style = "rounded,filled,dashed" if css == "廃止" else "rounded,filled"
        font = "#9AA0A6" if css == "廃止" else "#14161A"
        shape = "hexagon" if s.kind in ("判断", "承認") else "box"
        out.append(
            f'  {s.id} [label="{step_label(s, to_be)}", shape={shape}, style="{style}", '
            f'fillcolor="{fill}", color="{line}", fontcolor="{font}"];'
        )
    for e in flow.edges:
        label = f' [label="{_dot_text(e.label)}"]' if e.label else ""
        out.append(f"  {e.source} -> {e.target}{label};")
    out.append("}")
    return "\n".join(out)


def render_image(dot: str, fmt: str = "png") -> bytes | None:
    """DOT を画像にする。Graphviz（dot コマンド）がない環境や、失敗したときは None。

    公開環境（Hugging Face Spaces）では Dockerfile で Graphviz と日本語フォントを入れている。
    """
    import shutil
    import subprocess

    command = shutil.which("dot")
    if command is None or fmt not in ("png", "svg"):
        return None
    try:
        done = subprocess.run(
            [command, f"-T{fmt}", "-Gdpi=144"] if fmt == "png" else [command, f"-T{fmt}"],
            input=dot.encode("utf-8"), capture_output=True, timeout=config.DOT_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 and done.stdout else None


@dataclass
class Difference:
    """現状と改善後の比較の要点（画面とExcelで共通）。"""

    as_is_minutes: FlowTime
    to_be_minutes: FlowTime
    counts: dict[str, int]  # 変化の種類 → 工程数（変更なしを除く）


def compare_flows(as_is: Flow, to_be: Flow) -> Difference:
    counts: dict[str, int] = {}
    for s in to_be.steps:
        if s.change != "変更なし":
            counts[s.change] = counts.get(s.change, 0) + 1
    return Difference(time_per_case(as_is), time_per_case(to_be), counts)
