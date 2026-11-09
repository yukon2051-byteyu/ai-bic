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
# 表示の区分 → (バッジの文字, 塗り, 線)。色だけで区別しないよう、バッジの文字も付ける（指示書 4.4）
STYLE = {
    "変更なし": ("", "#F8F9FA", "#9AA0A6"),
    "問題": ("問題あり", "#FCE8E6", "#C5221F"),
    "自動化": ("自動化", "#E8F0FE", "#1967D2"),
    "変更": ("変更", "#E8F0FE", "#1967D2"),
    "追加": ("追加", "#E8F0FE", "#1967D2"),
    "短縮": ("短縮", "#E6F4EA", "#137333"),
    "廃止": ("廃止予定", "#FFFFFF", "#9AA0A6"),
}
LEGEND = [
    ("変更なし", "灰色"), ("問題", "赤"), ("自動化", "青"), ("変更", "青"), ("追加", "青"), ("短縮", "緑"), ("廃止", "破線"),
]
# バッジの色（濃い地に白い文字。区分の線の色と同じ）。要確認と、判断・承認の種類にもバッジを付ける
BADGE_CONFIRM = ("要確認", "#B06000")
BADGE_KIND = "#5F6368"
FONT = "Noto Sans CJK JP, Noto Sans JP, Yu Gothic, Meiryo, sans-serif"

# 工程の箱の大きさ（指示書：横幅220〜280px・高さ60〜90px・工程名14〜15px・補助情報11〜12px・縦の間隔24〜40px）
# Graphviz の1インチは図の上で72px。幅3.3インチ＝約238px、縦の間隔0.4インチ＝約29px
NODE_WIDTH_IN = 3.3
NODE_MIN_HEIGHT_IN = 0.85  # 約61px
RANK_SEP_IN = 0.4
NODE_SEP_IN = 0.3
NAME_PT, SUB_PT, BADGE_PT = 14, 11, 9
# 工程名の1行の文字数（全角）。2行を超える分は「…」で省略し、選んだときに全文を表示する。
# Graphviz は日本語の文字幅を実際より狭く見積もるため、15文字では箱からはみ出した（実際に描いて確認）。13文字にする
NAME_LINE_CHARS = 13
SUB_CHARS = 18
EDGE_LABEL_CHARS = 8  # 分岐の条件の文字数。長いと図の端で切れるため（実際に描いて確認）


def display_class(step: FlowStep, to_be: bool) -> str:
    """工程の表示の区分。改善後フローは変化の種類、現状フローは課題の有無で決める。"""
    if to_be and step.change != "変更なし":
        return step.change
    return "問題" if step.problem.strip() else "変更なし"


def _dot_text(text: str) -> str:
    """DOT の文字列の中で特別な意味を持つ文字を無効にする（利用者の入力やAIの出力をそのまま図に入れるため）。"""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", " ")


def _html(text: str) -> str:
    """HTML形式のラベルの中で特別な意味を持つ文字を無効にする（図の記述を壊されないため）。"""
    text = " ".join(text.split())
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _width(ch: str) -> float:
    """文字の幅（全角を1とした目安）。半角の英数字は約半分。"""
    return 0.55 if ord(ch) < 0x2E80 else 1.0


def clip_lines(text: str, per_line: float, lines: int) -> list[str]:
    """文字を、1行の幅（全角の文字数）で折り返し、最大 lines 行にする。超える分は最後の行を「…」で省略する。"""
    text = " ".join(text.split())
    result, current, width = [], "", 0.0
    for ch in text:
        w = _width(ch)
        if width + w > per_line:
            result.append(current)
            current, width = "", 0.0
            if len(result) == lines:
                break
        current += ch
        width += w
    else:
        if current:
            result.append(current)
        return result
    last = result[-1]
    while sum(_width(c) for c in last) + 1 > per_line and last:
        last = last[:-1]
    result[-1] = last + "…"
    return result


def _badge(text: str, color: str) -> str:
    # Graphviz は日本語の文字幅を狭く見積もるため、前後に余白を足して文字が欠けないようにする（実際に描いて確認）
    pad = "&#160;" * 2
    return f'<td bgcolor="{color}"><font point-size="{BADGE_PT}" color="#FFFFFF">{pad}{_html(text)}{pad}</font></td>'


def step_badges(step: FlowStep, to_be: bool) -> list[tuple[str, str]]:
    """工程の状態のバッジ（文字, 色）。"""
    badges = []
    css = display_class(step, to_be)
    tag, _, line = STYLE[css]
    if tag:  # 改善後フローでは変化の種類を表示する（課題は、選んだときの詳細で確認できる）
        badges.append((tag, line))
    if step.needs_confirmation:
        badges.append(BADGE_CONFIRM)
    if step.kind != "作業":
        badges.append((step.kind, BADGE_KIND))
    return badges


def step_label(step: FlowStep, to_be: bool) -> str:
    """工程の箱の中身（HTML形式のラベル）。工程ID・工程名・担当・作業時間・状態のバッジだけを表示する。"""
    rows = []
    badges = step_badges(step, to_be)
    if badges:
        # 空のセルで余った幅を吸収する。ないとバッジが1つのとき横長の帯になる（実際に描いて確認）
        cells = "".join(_badge(text, color) for text, color in badges) + "<td> </td>" * 3
        # 内側の表を左寄せにしないと、バッジが横長の帯になる（実際に描いて確認）
        rows.append(f'<tr><td align="left"><table border="0" cellspacing="2" cellpadding="1" align="left">'
                    f'<tr>{cells}</tr></table></td></tr>')
    name = clip_lines(f"{step.id} {step.name}", NAME_LINE_CHARS, 2)
    color = "#9AA0A6" if display_class(step, to_be) == "廃止" else "#14161A"
    name_html = "<br/>".join(_html(line) for line in name)
    rows.append(f'<tr><td align="left" balign="left"><font point-size="{NAME_PT}" color="{color}">{name_html}</font></td></tr>')
    sub = []
    if step.actor:
        sub.append(clip_lines(step.actor, 11, 1)[0])
    if step.minutes_per_case is not None:
        rate = "" if step.occurrence_rate == 100 else f"×{step.occurrence_rate:g}%"
        sub.append(f"{step.minutes_per_case:g}分{rate}")
    if sub:
        text = clip_lines("・".join(sub), SUB_CHARS, 1)[0]
        rows.append(f'<tr><td align="left"><font point-size="{SUB_PT}" color="#5F6368">{_html(text)}</font></td></tr>')
    return f'<<table border="0" cellborder="0" cellspacing="0" cellpadding="1">{"".join(rows)}</table>>'


def back_edges(flow: Flow) -> set[tuple[str, str]]:
    """差し戻し（流れを輪にする接続）を見つける。

    最初の工程（前の工程がない工程と、一覧の先頭）から深さ優先でたどり、たどっている途中の工程へ戻る接続を
    「輪を閉じる接続」とする。一覧の順番では判定しない（改善後フローで追加した工程は一覧の最後に来るため、
    順番で判定すると普通の流れを差し戻しと誤り、図が崩れた。実際に描いて確認）。
    """
    nexts: dict[str, list[str]] = {s.id: [] for s in flow.steps}
    for e in flow.edges:
        nexts[e.source].append(e.target)
    targets = {e.target for e in flow.edges}
    starts = [s.id for s in flow.steps if s.id not in targets] + [s.id for s in flow.steps]
    found, done, path = set(), set(), set()

    def visit(node: str) -> None:
        path.add(node)
        for nxt in nexts[node]:
            if nxt in path:
                found.add((node, nxt))
            elif nxt not in done:
                visit(nxt)
        path.discard(node)
        done.add(node)

    for start in starts:
        if start not in done:
            visit(start)
    return found


def to_dot(flow: Flow, to_be: bool = False) -> str:
    """工程データから DOT を作る。縦向き（上から下）にして、スマートフォンでも縦に読めるようにする。

    差し戻し（流れを輪にする接続）は配置の計算に使わない（constraint=false）。
    使うと、戻る矢印に引っぱられて図が縦に伸び、線が大回りするため。接続そのものは表示する。
    """
    loops = back_edges(flow)
    out = [
        "digraph flow {",
        f'  graph [rankdir=TB, newrank=true, bgcolor="transparent", nodesep={NODE_SEP_IN}, ranksep={RANK_SEP_IN}, '
        f'pad=0.3, fontname="{FONT}"];',
        f'  node [shape=box, style="rounded,filled", fontname="{FONT}", width={NODE_WIDTH_IN}, '
        f'height={NODE_MIN_HEIGHT_IN}, margin="0.1,0.04", penwidth=1.3];',
        f'  edge [color="#80868B", fontname="{FONT}", fontsize=10, arrowsize=0.6, penwidth=1.1, fontcolor="#5F6368"];',
    ]
    for s in flow.steps:
        css = display_class(s, to_be)
        _, fill, line = STYLE[css]
        style = "rounded,filled,dashed" if css == "廃止" else "rounded,filled"
        out.append(f'  {s.id} [label={step_label(s, to_be)}, style="{style}", fillcolor="{fill}", color="{line}"];')
    for e in flow.edges:
        attrs = []
        if e.label:
            attrs.append(f'label="{_dot_text(clip_lines(e.label, EDGE_LABEL_CHARS, 1)[0])}"')
        if (e.source, e.target) in loops:  # 差し戻し
            attrs += ["constraint=false", 'style="dashed"', 'color="#B0B4BA"']
        out.append(f"  {e.source} -> {e.target}" + (f" [{', '.join(attrs)}]" if attrs else "") + ";")
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
