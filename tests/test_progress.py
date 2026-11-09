"""progress.py のテスト（結果画面の進み具合：AI-BIC 2.0 指示書 6章・7.3）。"""

from ai_bic import project as pj
from ai_bic import review as rv
from ai_bic.progress import next_action, steps
from tests.test_review import demo


def by_no(project):
    return {s.no: s for s in steps(project)}


def test_業務フローを作る前は_次にすることを案内する():
    project, _ = demo()
    project.as_is = None
    project.to_be = {}
    states = by_no(project)
    assert states[1].status == "完了"
    assert states[2].status == "未着手"
    assert next_action(steps(project)).startswith("STEP 2（現状の業務フロー）")


def test_要確認の工程があれば現状フローは要確認():
    project, _ = demo()  # 月次売上集計の現状フローには「要確認」の工程がある
    assert by_no(project)[2].status == "要確認"
    assert "要確認の工程" in by_no(project)[2].detail


def test_改善案ごとの状態をまとめ_古い結果を最優先で示す():
    project, client = demo()
    for pid in ("P1", "P2", "P3"):
        rv.generate_review(client, project, pid)
    assert by_no(project)[5].status == "完了"
    pj.mark_updated(project, "proposal", "P2")  # 改善案2を作り直した
    states = by_no(project)
    assert states[3].status == "再作成が必要" and "案2 再作成が必要" in states[3].detail
    assert states[4].status == "再計算が必要"
    assert states[5].status == "再レビューが必要" and "案1 完了" in states[5].detail


def test_試算に未入力があれば作業中():
    project, _ = demo()
    project.simulations["P1"] = project.simulations["P1"].model_copy(update={"hourly_cost": None})
    assert by_no(project)[4].status == "作業中"


def test_情報が不足したまま分析したらヒアリングは要確認():
    project, _ = demo("repair_reception")
    assert by_no(project)[1].status == "要確認"


def test_確定すれば最後のSTEPが完了():
    project, _ = demo()
    project.selected_proposal = "P2"
    assert by_no(project)[6].status == "完了" and by_no(project)[6].detail == "案2を確定"
