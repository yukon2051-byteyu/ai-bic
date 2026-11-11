"""業務フローの画面のテスト（AI-BIC 2.0 機能B）。

前半：表の編集（st.data_editor の変更）を受け取る処理を、画面を起動せずに直接呼んで確かめる
     （Streamlit の画面テスト AppTest は表の編集の操作に対応していないため）
後半：デモで結果画面まで進め、業務フローの表示・比較・試算への反映を確かめる（AppTest）
"""

import re
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from ai_bic import project as pj
from ai_bic.demo_client import DemoClient, load_scenarios
from ai_bic.flow_ai import generate_flows
from ai_bic import flow as fl
from ai_bic.views import flow_canvas
from ai_bic.views import flows as view
from ai_bic.views import simulator
from tests.test_app import new_app, result_screen, work  # noqa: F401
from tests.test_app import secrets  # noqa: F401（フィクスチャ）
from tests.test_project import make_project


@pytest.fixture(autouse=True)
def clean_state():
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    yield


def demo_project():
    project = make_project("demo", proposals=3, demo_scenario_id="sales_report")
    generate_flows(DemoClient(next(s for s in load_scenarios() if s["id"] == "sales_report")), project)
    return project


def edit(project, target, name, **state):
    """表の編集を st.session_state に入れて、変更を受け取る処理を呼ぶ。"""
    st.session_state[view._key(target, project, name)] = {
        "edited_rows": state.get("edited", {}), "added_rows": state.get("added", []),
        "deleted_rows": state.get("deleted", []),
    }
    (view._on_steps if name == "steps" else view._on_edges)(project, target)


# --- 工程の編集（指示書 4.5） ---
def test_工程名_担当者_作業時間を表で変更できる():
    project = demo_project()
    before = pj.version(project, "as_is")
    edit(project, "as_is", "steps", edited={0: {"工程名": "売上Excelを受け取る", "担当": "担当者A", "作業時間（分）": 8.0}})
    s = project.as_is.step("S1")
    assert (s.name, s.actor, s.minutes_per_case, s.minutes_source) == ("売上Excelを受け取る", "担当者A", 8.0, "ユーザー入力")
    assert pj.version(project, "as_is") == before + 1
    # 現状を変えたので、改善後フローは「再作成が必要」と表示される
    assert pj.status(project, "to_be", "P1") == "再作成が必要"


def test_行を削除すると前後をつなぎ直す():
    project = demo_project()
    edit(project, "as_is", "steps", deleted=[3])  # S4 電卓での検算
    ids = [s.id for s in project.as_is.steps]
    assert "S4" not in ids
    assert any(e.source == "S3" and e.target == "S5" for e in project.as_is.edges)


def test_行を追加すると最後の工程の次につながり_新しいIDになる():
    project = demo_project()
    edit(project, "as_is", "steps", added=[{"工程名": "報告書を共有フォルダに保存する", "作業時間（分）": 2.0}])
    new = project.as_is.steps[-1]
    assert new.id == "S9" and new.minutes_per_case == 2
    assert any(e.source == "S8" and e.target == "S9" for e in project.as_is.edges)


def test_工程名を入力する前の行は_まだ追加しない():
    project = demo_project()
    before = project.as_is
    edit(project, "as_is", "steps", added=[{"担当": "担当者A"}])
    assert project.as_is == before


def test_改善後フローに追加した工程は_変化が追加になる():
    project = demo_project()
    edit(project, "P1", "steps", added=[{"工程名": "結果を共有する"}])
    assert project.to_be["P1"].steps[-1].change == "追加"


@pytest.mark.parametrize("changes", [{"工程名": "   "}, {"作業時間（分）": -5.0}, {"実施割合（%）": 150.0}])
def test_不正な値は反映せず_理由を表示する(changes):
    project = demo_project()
    before = project.as_is
    edit(project, "as_is", "steps", edited={0: changes})
    assert project.as_is == before
    assert "変更を反映できませんでした" in st.session_state["flow_err_as_is"]


def test_順番を上下に動かせる():
    project = demo_project()
    view._move(project, "as_is", "S2", -1)
    assert [s.id for s in project.as_is.steps][:2] == ["S2", "S1"]


def test_接続を追加_変更_削除できる():
    project = demo_project()
    edit(project, "as_is", "edges", added=[{"元の工程": "S4", "次の工程": "S8", "条件": "差異が大きい"}])
    assert any(e.source == "S4" and e.target == "S8" and e.label == "差異が大きい" for e in project.as_is.edges)
    count = len(project.as_is.edges)
    edit(project, "as_is", "edges", deleted=[0])
    assert len(project.as_is.edges) == count - 1


def test_存在しない接続や自分への接続は反映しない():
    project = demo_project()
    before = project.as_is
    edit(project, "as_is", "edges", edited={0: {"次の工程": "S1"}})  # S1 → S1
    assert project.as_is == before
    assert "反映できませんでした" in st.session_state["flow_err_as_is"]


# --- 効果試算との連動（指示書 4.7：同じ作業時間を二重に数えない） ---
def test_フローの時間を試算に反映すると_上書きされ_出どころがわかる():
    project = demo_project()
    simulator.ensure_inputs(project)
    project.simulations["P2"] = project.simulations["P2"].model_copy(update={"minutes_per_case": 999})
    view._apply_to_simulation(project, "P2", 185.0, 33.0)
    s = project.simulations["P2"]
    assert s.minutes_per_case == 185 and s.reduction_rate == 33  # 足し合わせず上書き
    assert s.source_of("minutes_per_case") == "AI推定"  # 工程の時間に AI推定 が含まれる
    assert s.cases_per_month == 5  # フローの「1件」の数にそろえる
    # このテストの業務情報には根拠の発言がないため、照合で「AI推定」になっている。その出どころを引き継ぐ
    assert s.source_of("cases_per_month") == project.cases_source == "AI推定"
    assert st.session_state["sim_P2_minutes_per_case"] == 185 and st.session_state["sim_P2_cases_per_month"] == 5


def test_フローから月の件数が分からなければ_件数は変えない():
    project = demo_project()
    simulator.ensure_inputs(project)
    project.cases_per_month, project.cases_source = None, None
    project.simulations["P1"] = project.simulations["P1"].model_copy(update={"cases_per_month": 7})
    view._apply_to_simulation(project, "P1", 100.0, 10.0)
    assert project.simulations["P1"].cases_per_month == 7


# ---------------------------------------------------------------
# 画面（AppTest）
# ---------------------------------------------------------------
def flow_screen(secrets) -> AppTest:  # noqa: F811
    at = result_screen(secrets, area="flows")
    at.button(key="flow_generate").click().run()
    assert not at.exception
    return at


def test_デモではAIを使わずにサンプルの業務フローを表示できる(secrets):  # noqa: F811
    at = result_screen(secrets, area="flows")
    assert "サンプルの業務フローを表示する" in [b.label for b in at.button]
    at = flow_screen(secrets)
    assert len([h for h in at.get("html") if "bicflow" in h.proto.body]) == 1  # 図の表示部品（flow_canvas.py）
    texts = [m.label for m in at.metric]
    assert "1件あたりの時間（現状）" in texts and "1件あたりの時間（改善後）" in texts
    assert at.session_state.project.ai_calls == {}  # デモはAIを使わない


def test_左右に並べて比較できる(secrets):  # noqa: F811
    at = flow_screen(secrets)
    at.radio(key="flow_view").set_value("左右に並べて比較").run()
    assert len([h for h in at.get("html") if "bicflow" in h.proto.body]) == 1  # 2つの図を1つの部品の中に、同じ縮尺で並べる
    at.selectbox(key="flow_pid").set_value("P3").run()
    assert not at.exception


def test_照合で直したことと_確認したいことを表示する(secrets):  # noqa: F811
    at = flow_screen(secrets)
    text = "\n".join(m.value for m in at.markdown)
    assert "業務フローについて、確認したいこと" in text
    assert "「AI推定」としています" in text  # S3 の時間の格下げ
    assert "「要確認」としています" in text  # S6 の根拠なし


def test_工程をクリックしたときの詳細は_全文と改善内容を含む():
    """図の工程をクリックすると表示する内容（指示書：選択時に全文を確認できる）。"""
    project = demo_project()
    as_is = dict(flow_canvas.step_details(project.as_is.step("S8"), to_be=False))
    assert as_is["工程"] == "S8 数字が合わない原因を調べて直す"
    # このテストの業務情報には「3時間ほど」の発言がないため、照合で「AI推定」になっている
    assert as_is["作業時間"] == "180分（AI推定）" and "原因調査" in as_is["課題"]
    assert "改善内容" not in as_is
    to_be = dict(flow_canvas.step_details(project.to_be["P3"].step("S4"), to_be=True))
    assert to_be["変化"] == "廃止" and "検算が不要" in to_be["改善内容"]
    assert "確認事項" in dict(flow_canvas.step_details(project.as_is.step("S6"), to_be=False))  # 要確認の工程


def test_図の部品は_比較で2つの図と詳細を持ち_データで画面を壊せない():
    project = demo_project()
    project.as_is = fl.update_step(project.as_is, "S1", name="</script><script>alert(1)</script>")
    panes = [flow_canvas.pane("現状", project.as_is, False), flow_canvas.pane("改善後", project.to_be["P1"], True)]
    html = flow_canvas.build_html(panes, 600)
    assert html.count("</script>") == 1  # 処理のスクリプト1つだけ（工程名の「</script>」は無効化）
    assert "\\u003c/script>\\u003cscript>alert(1)" in html
    js = html[html.index("<script>") + 8: html.rindex("</script>")]
    assert not re.search(r"<[/\w!]", js)  # Streamlit の安全チェック（DOMPurify）にスクリプトを消されない
    assert "@viz-js/viz@3.29.0" in html  # 版を固定
    assert "textContent" in html and "innerHTML" not in html  # 画面に出す文字は HTML として解釈させない


def test_図の部品の見た目の指定は部品の中だけに限り_アプリ全体に漏らさない():
    """iframe を使わない（st.html でページに直接書く）ため、body や全要素への指定はアプリの画面を崩す。"""
    project = demo_project()
    html = flow_canvas.build_html([flow_canvas.pane("現状", project.as_is, False)], 600)
    uid = re.search(r'<div id="(bicflow-[0-9a-f]{12})"', html).group(1)
    css = html[html.index("<style>") + 7: html.index("</style>")]
    rules = [r.strip() for r in re.findall(r"([^{}]+)\{", css) if not r.strip().startswith("@media")]
    assert rules and all(sel.strip().startswith("#" + uid) for r in rules for sel in r.split(","))
    assert "getElementById(\"" + uid + "\")" in html  # 要素は自分の入れ物の中だけで探す
    assert "document.querySelector" not in html
    assert flow_canvas.build_html([flow_canvas.pane("現状", project.as_is, False)], 600) == html  # 同じ内容なら同じ id


def test_図の部品の高さは工程数に応じ_上限で止める():
    project = demo_project()
    assert 460 <= flow_canvas.height_for(project.as_is) <= 860
    many = fl.Flow(steps=[fl.FlowStep(id=f"S{i}", name="工程") for i in range(1, 31)])
    assert flow_canvas.height_for(many) == 860


def test_現状を変えたら_改善後フローの確認を求め_確認すれば消える(secrets):  # noqa: F811
    at = flow_screen(secrets)
    project = at.session_state.project
    pj.mark_updated(project, "as_is")
    at.run()
    def flow_warnings():  # 試算の「再計算が必要」にも同じ理由が出るため、業務フローの警告だけを見る
        return [w.value for w in at.warning if "の改善後フローは" in w.value]

    assert any("現状の業務フローが変更されました" in w for w in flow_warnings())
    at.button(key="flow_ack_P1").click().run()
    assert flow_warnings() == []


def test_フローの時間を効果試算に反映できる(secrets):  # noqa: F811
    at = flow_screen(secrets)
    at.button(key="flow_to_sim_P1").click().run()
    assert not at.exception
    assert any("効果試算に反映しました" in s.value for s in at.success)
    assert work(at, "sim").number_input(key="sim_P1_minutes_per_case").value == 185  # 現状フローの1件あたりの時間


def test_自分で作る場合は空のフローから始められる(secrets):  # noqa: F811
    at = result_screen(secrets, area="flows")
    at.button(key="flow_blank").click().run()
    assert not at.exception
    assert "工程がありません" in "\n".join(m.value for m in at.markdown)


def test_業務フローの工程一覧をExcelでダウンロードできる(secrets):  # noqa: F811
    at = flow_screen(secrets)
    assert "download_flow" in [b.key for b in at.get("download_button")]
