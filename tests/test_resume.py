"""案件の保存と再開のテスト（AI-BIC 2.0 指示書 9.1・9.2「保存データの再読み込み」）。

ファイルの選択（st.file_uploader）は Streamlit の画面テストで操作できないため、
読み込んだあとの状態を画面の状態（session_state）に入れて、再開後の動きを確かめる。
"""

import pytest
from streamlit.testing.v1 import AppTest

from ai_bic import project as pj
from ai_bic import review as rv
from tests.test_app import all_text, new_app, result_screen  # noqa: F401
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
    assert at.number_input(key="sim_P1_hourly_cost").value == 4000
    assert len(at.get("iframe")) == 1  # 業務フローの図
    at.button(key="review_revise_P1").click().run()  # デモはAIを使わないので、続きの操作ができる
    assert restored.proposal_history["P1"]


def test_読み込んだAI診断の案件では_AIを使う操作ができない(secrets):  # noqa: F811
    project, _ = demo(mode="ai")
    project.as_is, project.to_be = None, {}
    at = resumed(secrets, project, None)
    text = all_text(at)
    assert "読み込んだAI診断の案件です" in text
    assert "AIで業務フローを作れません" in text and "AIでレビューできません" in text
    keys = [b.key for b in at.button]
    assert "flow_generate" not in keys and "review_run_P1" not in keys
    assert "flow_blank" in keys  # 自分で作ることはできる


@pytest.mark.parametrize("data, message", [
    (b"{broken", "JSON形式"),
    (b'{"app": "other"}', "AI-BIC で保存した"),
])
def test_読み込めないファイルは理由を伝える(data, message):
    with pytest.raises(pj.ProjectFileError, match=message):
        pj.load_project(data)
