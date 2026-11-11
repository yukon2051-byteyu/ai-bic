"""案件の保存と再開のテスト（AI-BIC 2.0 指示書 9.1・9.2「保存データの再読み込み」）。

ファイルの選択（st.file_uploader）は Streamlit の画面テストで操作できないため、
読み込んだあとの状態を画面の状態（session_state）に入れて、再開後の動きを確かめる。
"""

import pytest
from streamlit.testing.v1 import AppTest

from ai_bic import trial as tr
from ai_bic import project as pj
from ai_bic import review as rv
from tests.test_app import all_text, new_app, open_tab, result_screen, work  # noqa: F401
from tests.test_app import secrets  # noqa: F401（フィクスチャ）
from tests.test_review import demo


def resumed(secrets, project, scenario) -> AppTest:  # noqa: F811
    """保存したファイルを読み込んだ直後と同じ状態で、結果画面を開く（app.resume と同じ値を入れる）。"""
    loaded = pj.load_project(pj.to_json_bytes(project))
    at = new_app(secrets)
    s = at.session_state
    s["mode"], s["scenario"], s["session_id"] = loaded.mode, scenario, "x"
    s["business_info"], s["hearing_state"], s["diagnosis"] = loaded.business_info, loaded.hearing, loaded.diagnosis
    s["project"], s["loaded"], s["stage"] = loaded, loaded.mode == "ai", "result"
    at.run()
    assert not at.exception
    return at


def test_結果画面から案件を保存できる(secrets):  # noqa: F811
    at = result_screen(secrets)
    assert "download_project" in [b.key for b in at.get("download_button")]


def test_デモの案件は再開しても_業務フロー_試算_レビューと採否が残り_続きができる(secrets):  # noqa: F811
    project, client = demo()
    rv.generate_review(client, project, "P1")
    project.reviews["P1"].decisions[0] = "採用"
    project.simulations["P1"] = project.simulations["P1"].model_copy(update={"hourly_cost": 4000})
    at = resumed(secrets, project, client.scenario)
    restored = at.session_state.project
    assert restored.as_is == project.as_is and restored.reviews["P1"].decisions == {0: "採用"}
    assert work(at, "sim").number_input(key="sim_P1_hourly_cost").value == 4000
    assert len([h for h in work(at, "flows").get("html") if "bicflow" in h.proto.body]) == 1  # 業務フローの図
    work(at, "review").button(key="review_revise_P1").click().run()  # デモはAIを使わないので、続きの操作ができる
    assert restored.proposal_history["P1"]


def test_読み込んだAI診断の案件では_AIを使う操作ができない(secrets):  # noqa: F811
    project, _ = demo(mode="ai")
    project.as_is, project.to_be = None, {}
    at = resumed(secrets, project, None)
    assert "読み込んだAI診断の案件です" in all_text(at)
    assert "AIで業務フローを作れません" in all_text(work(at, "flows"))
    keys = [b.key for b in at.button]
    assert "flow_generate" not in keys and "flow_blank" in keys  # 自分で作ることはできる
    assert "AIでレビューできません" in all_text(work(at, "review"))
    assert "review_run_P1" not in [b.key for b in at.button]


@pytest.mark.parametrize("data, message", [
    (b"{broken", "JSON形式"),
    (b'{"app": "other"}', "AI-BIC で保存した"),
])
def test_読み込めないファイルは理由を伝える(data, message):
    with pytest.raises(pj.ProjectFileError, match=message):
        pj.load_project(data)


def test_製造部デモで_案2の確定_試行_実測_再試行の判断_保存と再開_Excel出力まで通して操作できる(secrets):  # noqa: F811
    """指示書 6章 5。デモはAIを呼ばない。"""
    from io import BytesIO

    from openpyxl import load_workbook

    from tests.test_app import production_result

    at = production_result(secrets)
    at.radio(key="compare_plan").set_value("P2").run()
    at.button(key="go_review").click().run()
    at.button(key="review_decide_P2").click().run()
    at.button(key="review_trial_P2").click().run()  # 確定のあとに「この案を小さく試す」
    s = at.session_state
    assert s.project.trial.pid == "P2" and s.scroll_count >= 1
    assert "判定保留" in all_text(at)  # 実測がまだ
    at.button(key="trial_demo").click().run()  # 1台分のCSVで試したサンプル（デモ用の数値）
    text = all_text(at)
    assert "デモ用の数値です" in text and "判定の目安　修正して再試行" in text
    table = next(m.value for m in at.markdown if "<table class=\"bic-trial\">" in m.value)
    assert "25 − 60 = -35分" in table and "✕ 未達" in table and "未測定" not in table
    at.radio(key="trial_decision").set_value("修正して再試行")
    at.text_area(key="trial_reason").input("時間と数量は合格したが、日付の書き方の例外が残ったため")
    at.button(key="trial_decide").click().run()
    assert s.project.trial.decision == "修正して再試行"
    assert "判断：修正して再試行" in all_text(at)
    assert s.project.ai_calls == {}

    # 保存して再開しても、計画・実測・判断が残る
    again = resumed(secrets, s.project, s.scenario)
    assert again.session_state.project.trial == s.project.trial
    assert "判断：修正して再試行" in all_text(open_tab(again, "小さく試す"))
    # Excel の診断レポートに「試行・判定」シートがある
    report = next(b for b in again.get("download_button") if b.key == "download_report_top")
    assert report is not None
    from datetime import datetime

    from ai_bic.config import JST, load_framework
    from ai_bic.report import build_report

    p = again.session_state.project
    data = build_report(p.business_info, p.hearing, p.diagnosis, load_framework("general"),
                        datetime(2026, 11, 11, tzinfo=JST), demo=True, project=p)
    assert "試行・判定" in load_workbook(BytesIO(data)).sheetnames


def test_試行の測定結果は画面で入力して保存でき_必須が未入力なら判断ボタンが押せない(secrets):  # noqa: F811
    from tests.test_app import production_result

    at = production_result(secrets)
    at.radio(key="compare_plan").set_value("P1").run()
    at.button(key="go_review").click().run()
    at.button(key="review_decide_P1").click().run()
    at.button(key="review_trial_P1").click().run()
    assert at.button(key="trial_decide").disabled
    metrics = at.session_state.project.trial.metrics
    # 案1は作業時間の短縮が直接の目的ではないため、案の指標（集計の誤りの件数）が先頭の必須の項目、作業時間は参考（AI-BIC 3.1）
    # 作業時間は、診断の数値（設備1台分の集計時間・分／台）の名前と単位を引き継ぐ（AI-BIC 3.3）
    assert [m.name for m in metrics] == ["集計の誤りの件数", "設備1台分の集計時間", "例外の処理（想定外の形式など）"]
    assert tr.basis_of(metrics[1]) == "分／台" and metrics[1].baseline == 300
    assert metrics[0].required and not metrics[1].required
    assert tr.evaluate(metrics[0]).status == "単位を確認"  # 案の指標に単位がない間は判定に使わない
    at.text_input(key="trial_m0_unit").set_value("件")
    at.text_input(key="trial_m0_per").set_value("試行期間")
    at.number_input(key="trial_m0_target").set_value(0.0)
    at.number_input(key="trial_m0_actual").set_value(0.0)
    at.number_input(key="trial_m1_actual").set_value(200.0)
    at.radio(key="trial_m2_check_result").set_value("成功")
    at.button(key="trial_metrics_save").click().run()
    metrics = at.session_state.project.trial.metrics
    assert metrics[1].actual == 200 and metrics[1].estimate == 255 and metrics[1].target == 255
    assert "判定の目安　展開する" in all_text(at)
    assert not at.button(key="trial_decide").disabled


def test_確定のあとの主ボタンは1つだけで_計画を作ったあとは試行計画を開くになる(secrets):  # noqa: F811
    from tests.test_app import production_result

    at = production_result(secrets)
    at.radio(key="compare_plan").set_value("P2").run()
    at.button(key="go_review").click().run()
    at.button(key="review_decide_P2").click().run()
    labels = [b.label for b in at.button]
    assert labels.count("この案を小さく試す") == 1 and "trial_start" not in [b.key for b in at.button]
    assert at.session_state.project.trial is None  # 押すまで計画は作らない
    at.button(key="review_trial_P2").click().run()
    assert at.session_state.project.trial.pid == "P2"
    assert at.session_state.result_tab == "小さく試す"  # 計画を作ると「小さく試す」タブを開く
    labels = [b.label for b in open_tab(at, "改善案を選ぶ").button]
    assert "この案を小さく試す" not in labels and labels.count("試行計画を開く") == 1
    at.button(key="review_trial_P2").click().run()  # 開き直しても、計画は作り直さない
    assert at.session_state.project.trial.pid == "P2"


def test_値のある項目の単位を変えると再確認が必要になり_確かめると判定に使える(secrets):  # noqa: F811
    """AI-BIC 3.3：単位を変えて実測結果と合わなくなったら再確認が必要と示し、確かめるまで差と判定を出さない。"""
    from tests.test_app import production_result

    at = production_result(secrets)
    at.radio(key="compare_plan").set_value("P1").run()
    at.button(key="go_review").click().run()
    at.button(key="review_decide_P1").click().run()
    at.button(key="review_trial_P1").click().run()
    at.number_input(key="trial_m1_actual").set_value(200.0)
    at.button(key="trial_metrics_save").click().run()
    assert tr.evaluate(at.session_state.project.trial.metrics[1]).status == "達成"
    at.text_input(key="trial_m1_per").set_value("件").run()
    at.button(key="trial_metrics_save").click().run()
    m = at.session_state.project.trial.metrics[1]
    r = tr.evaluate(m)
    assert r.status == "単位を確認" and "分／台 → 分／件" in r.diff_text and m.actual == 200
    assert any("再確認が必要" in w.value for w in at.warning)
    at.checkbox(key="trial_m1_basis_ok").check().run()
    at.button(key="trial_metrics_save").click().run()
    m = at.session_state.project.trial.metrics[1]
    assert m.checked_basis == "分／件" and tr.evaluate(m).status == "達成"
