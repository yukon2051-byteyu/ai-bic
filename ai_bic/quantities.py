"""数値の一覧から計算する値（AI-BIC 3.1）。計算はAIではなくプログラムが行う。

AIは「Q1 * Q2 / 60」のような式だけを書く。ここで式を確かめ（Q の番号・数・四則演算だけ）、
分かっている値だけで計算する。必要な値が1つでも分からなければ計算せず、何が足りないかを返す
（例：記入漏れの月間発生日数が分かるまで、追加確認を含む総作業時間は未確定）。
"""

import ast
import re
from dataclasses import dataclass, field

from ai_bic.simulation import fmt

def basis_text(unit: str | None, per: str | None) -> str:
    """単位と集計期間（何あたり）を1つの文にする。例：分／稼働日。何あたりがなければ単位だけ（AI-BIC 3.3）。"""
    unit, per = (unit or "").strip(), (per or "").strip()
    return f"{unit}／{per}" if unit and per else unit


def label(name: str, unit: str | None, per: str | None) -> str:
    """画面・Excel で使う数値の名前。例：閉館前の照合時間（分／稼働日）。

    何あたりが別にあるときは、名前の末尾の「（1日あたり）」のような書き方を外して重ねない。
    """
    name = name.strip()
    if per:
        name = re.sub(r"[（(][^（）()]*あたり[）)]$", "", name).strip() or name
    b = basis_text(unit, per)
    return f"{name}（{b}）" if b else name


_OPS = {ast.Add: ("＋", lambda a, b: a + b), ast.Sub: ("−", lambda a, b: a - b),
        ast.Mult: ("×", lambda a, b: a * b), ast.Div: ("÷", lambda a, b: a / b)}


def formula_problem(formula: str, known_ids: set[str]) -> str | None:
    """式が使えなければ理由、使えれば None。"""
    try:
        tree = ast.parse(formula.strip(), mode="eval")
    except SyntaxError:
        return "式として読めません"
    for node in ast.walk(tree):
        if isinstance(node, (ast.Expression, ast.BinOp, ast.Load)) or type(node) in _OPS:
            continue
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            continue
        if isinstance(node, ast.USub):
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            continue
        if isinstance(node, ast.Name):
            if node.id not in known_ids:
                return f"存在しない番号 {node.id} を使っています"
            continue
        return "使えない記号が含まれています（使えるのは番号・数・＋−×÷・かっこ）"
    return None


@dataclass
class Calc:
    value: float | None
    text: str  # 例：15分 × 20日 ÷ 60 ＝ 5時間（計算できないときは空）
    missing: list[str] = field(default_factory=list)  # 足りない値の番号
    error: str = ""  # 0で割るなど


def evaluate(formula: str, unit: str, values: dict[str, float | None], units: dict[str, str]) -> Calc:
    """式を計算する。values：番号 → 値（分からなければ None）。units：番号 → 単位（式の表示に使う）。"""
    tree = ast.parse(formula.strip(), mode="eval").body
    missing = sorted({n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and values.get(n.id) is None})
    if missing:
        return Calc(None, "", missing)

    def show(node, parent_prec: int = 0) -> str:
        if isinstance(node, ast.Name):
            return f"{fmt(values[node.id])}{units.get(node.id, '')}"
        if isinstance(node, ast.Constant):
            return fmt(float(node.value))
        if isinstance(node, ast.UnaryOp):
            return f"−{show(node.operand, 3)}"
        prec = 1 if isinstance(node.op, (ast.Add, ast.Sub)) else 2
        text = f"{show(node.left, prec)} {_OPS[type(node.op)][0]} {show(node.right, prec + 1)}"
        return f"（{text}）" if prec < parent_prec else text

    def calc(node) -> float:
        if isinstance(node, ast.Name):
            return float(values[node.id])
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.UnaryOp):
            return -calc(node.operand)
        return _OPS[type(node.op)][1](calc(node.left), calc(node.right))

    try:
        value = calc(tree)
    except ZeroDivisionError:
        return Calc(None, "", [], "0で割る計算になるため計算できません（分母が0）。入力を確認してください")
    return Calc(value, f"{show(tree)} ＝ {fmt(value)}{unit}")
