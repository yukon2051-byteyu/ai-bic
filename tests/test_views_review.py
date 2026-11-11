"""改善案チャレンジレビューの画面のテスト（AppTest。デモで結果画面まで進める）。"""

from streamlit.testing.v1 import AppTest

from tests.test_app import all_text, new_app, result_screen  # noqa: F401
from tests.test_app import secrets  # noqa: F401（フィクスチャ）


def reviewed(secrets, pid="P1") -> AppTest:  # noqa: F811
    at = result_screen(secrets, area="review")
    at.selectbox(key="review_pid").set_value(pid).run()
    at.button(key=f"review_run_{pid}").click().run()
    assert not at.exception
    return at


def texts(at) -> str:
    return all_text(at) + "\n".join(e.value for e in at.error) + "\n".join(i.value for i in at.info)


def test_デモではAIを使わずにレビューを表示し_判定と注意書きを出す(secrets):  # noqa: F811
    at = reviewed(secrets)
    text = texts(at)
    assert "△ 条件付き推奨" in text
    assert "正式な導入の承認ではありません" in text
    assert "評価の要約" in text and "指摘事項（3件）" in text
    assert "根拠：未確認" in text  # 根拠のない一般論の指摘
    assert at.session_state.project.ai_calls == {}


def test_AIの判定をプログラムが上書きした場合は_理由を表示する(secrets):  # noqa: F811
    at = new_app(secrets)
    at.selectbox(key="demo_scenario").set_value("修理依頼の受付・手配（お客様相談室）").run()
    at = result_screen(secrets, at, area="review")
    at.selectbox(key="review_pid").set_value("P3").run()
    at.button(key="review_run_P3").click().run()
    text = texts(at)
    assert "✕ 再検討" in text
    assert "AIの判定は「条件付き推奨」でしたが、プログラムの検証により「再検討」としました" in text
    assert "試行の前に対応すべき指摘（" in text


def test_採用した指摘だけで作り直すと_変更前後と要更新の状態を表示する(secrets):  # noqa: F811
    at = reviewed(secrets)
    assert "指摘を「採用」すると" in all_text(at)
    radios = [r for r in at.radio if r.key and r.key.startswith("review_decision_P1_")]
    radios[0].set_value("採用").run()
    assert at.session_state.project.reviews["P1"].decisions
    at.button(key="review_revise_P1").click().run()
    assert not at.exception
    text = texts(at)
    assert "改善案の作り直し（1回）：変更前と変更後" in [e.label for e in at.expander]  # 開閉できる枠の見出し
    assert "前提条件（変更前）" in text and "前提条件（変更後）" in text
    assert "レビュー：再レビューが必要" in text and "効果試算：再計算が必要" in text
    # レビューは古いと表示し、やり直せる
    assert any("このレビューは、改善案が変更されました" in w.value for w in at.warning)
    at.button(key="review_run_P1").click().run()
    assert not any("このレビューは" in w.value for w in at.warning)
    # 改善後フロー・試算はまだ古いので、レビューもそれを元にしていると知らせる（レビューし直すボタンは出さない）
    assert any("古くなっています" in i.value for i in at.info)
    assert "review_run_P1" not in [b.key for b in at.button]


def test_改善案を確定できる(secrets):  # noqa: F811
    at = reviewed(secrets, "P2")
    at.button(key="review_decide_P2").click().run()
    assert at.session_state.project.selected_proposal == "P2"
    assert any("を確定しました" in s.value for s in at.success)


def test_レビュー前にプログラムの検証結果を見られる(secrets):  # noqa: F811
    at = result_screen(secrets, area="review")
    at.selectbox(key="review_pid").set_value("P3").run()
    assert "プログラムの検証で見つかった問題" in "\n".join(e.label for e in at.expander)
