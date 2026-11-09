"""flow.py のテスト（業務フロー：AI-BIC 2.0 機能B）。

工程IDの安定性、削除したときの接続の整合性、分岐・循環で作業時間を二重に数えないことを確かめる。
"""

import pytest
from pydantic import ValidationError

from ai_bic.flow import (
    Flow,
    FlowEdge,
    FlowStep,
    add_step,
    check_flow,
    delete_step,
    move_step,
    set_edges,
    time_per_case,
    update_step,
)


def step(no: int, minutes=None, rate=100.0, **fields) -> FlowStep:
    return FlowStep(id=f"S{no}", name=f"工程{no}", minutes_per_case=minutes, occurrence_rate=rate, **fields)


def edges(*pairs) -> list[FlowEdge]:
    return [FlowEdge(source=f"S{a}", target=f"S{b}") for a, b in pairs]


def linear() -> Flow:
    """S1 → S2 → S3"""
    return Flow(steps=[step(1, 10), step(2, 20), step(3, 30)], edges=edges((1, 2), (2, 3)), next_no=4)


def branching() -> Flow:
    """S1 →（差異あり 30%）S2 → S4、S1 →（差異なし）S4。S2 → S3（手戻り）→ S1 の循環つき"""
    return Flow(
        steps=[step(1, 10), step(2, 20, rate=30), step(3, 15, rate=10), step(4, 5)],
        edges=edges((1, 2), (1, 4), (2, 4), (2, 3), (3, 1)),
        next_no=5,
    )


def pairs(flow: Flow) -> set[tuple[str, str]]:
    return {(e.source, e.target) for e in flow.edges}


# --- 分岐・循環と作業時間 ---
def test_分岐と循環があっても各工程を1回だけ数える():
    t = time_per_case(branching())
    # 10 + 20×30% + 15×10% + 5 ＝ 22.5分（接続をたどって足すと、合流のS4や循環を二重に数えてしまう）
    assert t.minutes_per_case == 22.5
    assert t.unknown_step_ids == []


def test_作業時間が不明な工程は合計に入れず_不明として返す():
    flow = Flow(steps=[step(1, 10), step(2, None)], edges=edges((1, 2)), next_no=3)
    t = time_per_case(flow)
    assert t.minutes_per_case == 10 and t.unknown_step_ids == ["S2"]


def test_廃止する工程は改善後の時間に入れない():
    flow = update_step(linear(), "S2", change="廃止")
    assert time_per_case(flow).minutes_per_case == 40
    assert time_per_case(flow, include_removed=True).minutes_per_case == 60


# --- 工程の削除と接続の整合性 ---
def test_工程を削除すると前後を直接つなぎ直す():
    flow = delete_step(linear(), "S2")
    assert [s.id for s in flow.steps] == ["S1", "S3"]
    assert pairs(flow) == {("S1", "S3")}


def test_つなぎ直さない削除では接続だけが消える():
    flow = delete_step(linear(), "S2", reconnect=False)
    assert pairs(flow) == set()
    assert any("つながっていません" in w for w in check_flow(flow))


def test_分岐の工程を削除しても重複や自己接続を作らない():
    flow = delete_step(branching(), "S2")
    # S1→S2→S4 は、S1→S4 がもとからあるので重複させない。S1→S2→S3 は S1→S3 になる。S3→S1 は残る
    assert pairs(flow) == {("S1", "S4"), ("S1", "S3"), ("S3", "S1")}
    flow = delete_step(flow, "S3")  # S1→S3→S1 の循環を消しても S1→S1 は作らない
    assert pairs(flow) == {("S1", "S4")}


def test_削除した工程のIDは再利用しない():
    flow = delete_step(linear(), "S3")
    flow, new_id = add_step(flow, "新しい工程", after_id="S2")
    assert new_id == "S4"


def test_存在しない工程は削除できない():
    with pytest.raises(KeyError):
        delete_step(linear(), "S9")


# --- 追加・移動・変更 ---
def test_工程を間に追加すると接続も間に入る():
    flow, new_id = add_step(linear(), "確認", after_id="S1", minutes_per_case=5)
    assert [s.id for s in flow.steps] == ["S1", "S4", "S2", "S3"]
    assert pairs(flow) == {("S1", "S4"), ("S4", "S2"), ("S2", "S3")}
    assert flow.step(new_id).minutes_per_case == 5


def test_元のフローは書き換えない():
    original = linear()
    delete_step(original, "S2")
    add_step(original, "追加")
    assert len(original.steps) == 3


def test_順序を変えても接続は変わらない():
    flow = move_step(linear(), "S3", -1)
    assert [s.id for s in flow.steps] == ["S1", "S3", "S2"]
    assert pairs(flow) == pairs(linear())
    assert move_step(linear(), "S1", -1) == linear()  # 先頭より上には動かない


def test_担当者や作業時間を変更できるがIDは変えられない():
    flow = update_step(linear(), "S2", actor="経理課", minutes_per_case=45, name="  転記  ")
    assert flow.step("S2").actor == "経理課" and flow.step("S2").name == "転記"
    with pytest.raises(ValueError):
        update_step(linear(), "S2", id="S9")
    with pytest.raises(ValidationError):
        update_step(linear(), "S2", minutes_per_case=-1)


def test_接続を修正できる():
    flow = set_edges(linear(), edges((1, 3), (3, 2)))
    assert pairs(flow) == {("S1", "S3"), ("S3", "S2")}


# --- 壊れたデータ（読み込んだファイルやAIの出力）をはじく ---
@pytest.mark.parametrize(
    "bad",
    [
        {"steps": [step(1), step(1)]},  # IDの重複
        {"steps": [step(1)], "edges": edges((1, 2))},  # 存在しない工程への接続
        {"steps": [step(1)], "edges": edges((1, 1))},  # 自己接続
        {"steps": [step(1), step(2)], "edges": edges((1, 2), (1, 2))},  # 接続の重複
        {"steps": [step(no) for no in range(1, 32)]},  # 工程が多すぎる
    ],
)
def test_整合しないフローは作れない(bad):
    with pytest.raises(ValidationError):
        Flow(**bad)


@pytest.mark.parametrize("bad_id", ["1", "S0", "s1", "S1a", ""])
def test_工程IDの形が違えばはじく(bad_id):
    with pytest.raises(ValidationError):
        FlowStep(id=bad_id, name="工程")


def test_空白だけの工程名ははじく():
    with pytest.raises(ValidationError):
        FlowStep(id="S1", name="   ")


def test_番号がずれたファイルでもIDを再利用しない():
    flow = Flow(steps=[step(5)], next_no=1)
    assert flow.next_no == 6


def test_確認すべき点を知らせる():
    flow = update_step(branching(), "S4", needs_confirmation=True, minutes_per_case=None)
    warnings = check_flow(flow)
    assert any("要確認の工程が1個" in w for w in warnings)
    assert any("作業時間が不明な工程が1個" in w for w in warnings)
    # 差し戻しの循環は普通の形なので、注意しない
    loop = Flow(steps=[step(1, 1), step(2, 1)], edges=edges((1, 2), (2, 1)), next_no=3)
    assert check_flow(loop) == []
    # 最初の工程からたどり着けない工程（S3→S4 が S1・S2 の流れから切れている。S4 は前の工程があるので出発点ではない）
    cut = Flow(steps=[step(1, 1), step(2, 1), step(3, 1), step(4, 1)], edges=edges((1, 2), (3, 4), (4, 3)), next_no=5)
    assert any("たどり着けない工程があります（S4）" in w or "（S3、S4）" in w for w in check_flow(cut))


# ---------------------------------------------------------------
# 図の描画（DOT）と画像の出力
# ---------------------------------------------------------------
from ai_bic.flow import compare_flows, render_image, to_dot  # noqa: E402


def test_図には色だけでなく記号と文字で区分を書く():
    flow = Flow(steps=[step(1, 10, problem="手作業が多い"), step(2, 5, needs_confirmation=True)],
                edges=edges((1, 2)), next_no=3)
    dot = to_dot(flow)
    assert "【▲ 問題あり】" in dot and "【？ 要確認】" in dot
    to_be = update_step(update_step(flow, "S1", change="自動化"), "S2", change="廃止")
    dot = to_dot(to_be, to_be=True)
    assert "【◆ 自動化】" in dot and "【× 廃止予定】" in dot and "dashed" in dot
    assert "問題あり" not in dot  # 改善後は変化の種類で表示する


def test_判断と承認の工程は形を変える():
    flow = Flow(steps=[step(1, kind="判断"), step(2, kind="承認"), step(3)], next_no=4)
    dot = to_dot(flow)
    assert dot.count("shape=hexagon") == 2


def test_利用者の入力で図の記述を壊せない():
    """工程名に「"」や「\\」があっても、DOT の文字列の外に出ない。"""
    flow = Flow(steps=[FlowStep(id="S1", name='確認" ]; S9 [label="x', actor=r"a\b")], next_no=2)
    dot = to_dot(flow)
    assert r'S1 [label="S1 確認\" ]; S9 [label=\"x' in dot  # 「"」は「\"」になり、文字列の中にとどまる
    assert r"担当：a\\b" in dot  # 「\」は「\\」になる
    assert [line for line in dot.splitlines() if line.startswith("  S")] == [dot.splitlines()[4]]  # 工程は S1 だけ
    assert "\n  S9 " not in dot  # 名前の中の文字で、余計な工程（S9）が作られていない


def test_接続の条件も図に出る():
    flow = Flow(steps=[step(1), step(2)], edges=[FlowEdge(source="S1", target="S2", label="差異あり")], next_no=3)
    assert 'S1 -> S2 [label="差異あり"]' in to_dot(flow)


def test_Graphvizがない環境では画像を作らない(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert render_image("digraph {}") is None


def test_Graphvizがあれば画像を作る_失敗や時間切れはNone(monkeypatch):
    import subprocess
    from types import SimpleNamespace

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/dot")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=b"PNG"))
    assert render_image("digraph {}") == b"PNG"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout=b""))
    assert render_image("digraph {}") is None

    def timeout(*a, **k):
        raise subprocess.TimeoutExpired("dot", 1)

    monkeypatch.setattr(subprocess, "run", timeout)
    assert render_image("digraph {}") is None


def test_比較では変化の種類ごとに工程を数え_時間を比べる():
    as_is = linear()
    to_be = update_step(update_step(linear(), "S2", change="廃止"), "S3", change="短縮", minutes_per_case=10)
    d = compare_flows(as_is, to_be)
    assert d.counts == {"廃止": 1, "短縮": 1}
    assert d.as_is_minutes.minutes_per_case == 60 and d.to_be_minutes.minutes_per_case == 20
