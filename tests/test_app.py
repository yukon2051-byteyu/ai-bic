"""画面の結合テスト（Streamlit の AppTest で app.py を実際に動かす）。

ブラウザを使わずに、ボタンを押す・入力するといった操作を再現する。
AIモードは本物のAPIを呼ばない範囲（パスワード・回数制限）まで確認する。
"""

from datetime import datetime

import pytest
from streamlit.testing.v1 import AppTest

from ai_bic import config, guard
from ai_bic.config import DAILY_DIAGNOSIS_LIMIT, JST
from ai_bic.usage_log import count_slots_on, init_db, reserve_slot


@pytest.fixture
def secrets(monkeypatch):
    """Secrets を偽物に差し替える（本物の secrets.toml や環境変数に左右されないように）。"""
    values = {}
    monkeypatch.setattr(guard, "get_secret", lambda name: values.get(name) or None)
    return values


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "usage_test.db")
    monkeypatch.setattr(config, "DB_PATH", path)
    return path


APP_PATH = str(config.BASE_DIR / "app.py")  # 相対パスはテストファイルの場所が基準になるため絶対パスにする


def new_app(secrets) -> AppTest:
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.run()
    assert not at.exception
    return at


def all_text(at) -> str:
    parts = [m.value for m in at.markdown] + [e.value for e in at.error] + [c.value for c in at.caption]
    return "\n".join(str(p) for p in parts)


# ---------------------------------------------------------------
# デモモード：トップ → 業務情報 → ヒアリング → 診断結果
# ---------------------------------------------------------------
def test_デモモードで最後まで診断できる(secrets):
    at = new_app(secrets)
    assert at.session_state.stage == "top"

    at.button(key="start_demo").click().run()
    assert at.session_state.stage == "input"
    assert at.session_state.mode == "demo"
    assert "デモモード：AIは使用していません" in all_text(at)  # デモであることを常に表示（F05）

    # 入力欄にはシナリオの内容が入っている（決定事項E2）
    assert at.text_input(key="f_department").value == "営業管理部"
    at.button(key="submit_info").click().run()
    assert at.session_state.stage == "hearing"
    assert len(at.session_state.hearing_state.turns) == 1

    # 用意された回答で4問に答えると、資料の添付に進む（ここではAIを呼ばない）
    for _ in range(4):
        at.button(key="submit_answer").click().run()
        assert not at.exception
    assert at.session_state.stage == "attach"
    assert at.session_state.diagnosis is None
    assert "集計差異の記録.csv" in at.expander[0].label  # 見本ファイルを添付済みで表示

    at.button(key="diagnose").click().run()

    assert at.session_state.stage == "result"
    text = all_text(at)
    assert "集計作業が担当者1人に依存している" in text
    assert "根拠となる発言を入力内容の中に確認できなかった" in text  # I5 の格下げ表示
    assert "表計算ソフトの集計機能による自動集計" in text
    # 添付ファイルからの根拠が、ファイル名つきで表示される（F09）
    assert "添付ファイル『集計差異の記録.csv』" in text
    assert "診断に使った資料" in text
    # 訴求力のための項目（診断サマリー・導入前後の比較・最初の一歩）
    assert "最大の課題は、集計方法を担当者1人しか知らないことです" in text
    assert "最重要課題" in text and "最初の一手" in text
    assert "BEFORE" in text and "AFTER" in text
    assert "5つの営業所の現在の様式を集め、項目の違いを一覧表にする" in text

    # 新しい診断を始めるとトップに戻り、内容は消える
    at.button(key="new").click().run()
    assert at.session_state.stage == "top"
    assert at.session_state.business_info is None


@pytest.mark.parametrize(
    ("title", "department", "answers", "expected_text"),
    [
        ("修理依頼の受付・手配（お客様相談室）", "お客様相談室", 5, "より簡単な方法では不十分な理由"),
        ("交通費の精算・承認（総務部）", "総務部", 2, "会計ソフトの取り込み用ファイルを自動作成"),
    ],
)
def test_プルダウンで別のシナリオを選んで最後まで診断できる(
    secrets, title, department, answers, expected_text
):
    at = new_app(secrets)
    assert len(at.selectbox(key="demo_scenario").options) >= 3

    at.selectbox(key="demo_scenario").set_value(title).run()
    at.button(key="start_demo").click().run()
    assert at.text_input(key="f_department").value == department
    at.button(key="submit_info").click().run()

    for _ in range(answers):
        at.button(key="submit_answer").click().run()
        assert not at.exception
    assert at.session_state.stage == "attach"
    at.button(key="diagnose").click().run()

    assert at.session_state.stage == "result"
    assert expected_text in all_text(at)
    assert "添付ファイル『" in all_text(at)  # どのシナリオも添付ファイルを根拠に使っている


def test_修理受付のデモは不足項目を表示する(secrets):
    at = new_app(secrets)
    at.selectbox(key="demo_scenario").set_value("修理依頼の受付・手配（お客様相談室）").run()
    at.button(key="start_demo").click().run()
    at.button(key="submit_info").click().run()
    for _ in range(5):
        at.button(key="submit_answer").click().run()
    at.button(key="diagnose").click().run()
    # 5問で終わり、5問目の回答（予算は相談次第）も判定したうえで、不足項目と理由を表示（B01）
    text = all_text(at)
    assert "情報が不足しています。結果は参考としてご覧ください。" in text
    assert "改善に向けた制約条件（不十分）　足りないこと：予算の目安や、使ってよいツール・社内ルールの有無" in text


def test_結果画面の冒頭と末尾に_診断レポートのダウンロードボタンがある(secrets):
    at = new_app(secrets)
    at.button(key="start_demo").click().run()
    at.button(key="submit_info").click().run()
    while at.session_state.stage == "hearing":
        at.button(key="submit_answer").click().run()
    at.button(key="diagnose").click().run()

    assert not at.exception
    keys = [b.proto.id for b in at.get("download_button")]
    assert len(keys) == 2  # 長い結果を読み終えた後でも、すぐ保存できるように末尾にも置く
    assert all("download_report" in k for k in keys)
    assert "結果画面からExcelのレポートをダウンロード" not in all_text(at)  # 案内はトップ画面だけ


def test_トップ画面で_レポートで結果を控えるよう案内する(secrets):
    at = new_app(secrets)
    assert "結果画面からExcelのレポートをダウンロードして控えてください" in all_text(at)


def test_ヒアリングの途中で_ここまでの情報で診断できる(secrets):
    at = new_app(secrets)
    at.button(key="start_demo").click().run()
    at.button(key="submit_info").click().run()
    at.button(key="submit_answer").click().run()  # 1問だけ回答

    at.button(key="finish").click().run()
    assert at.session_state.stage == "attach"
    at.button(key="diagnose").click().run()

    assert at.session_state.stage == "result"
    assert "情報が不足しています" in all_text(at)  # 不足項目を明示（R6）


def test_資料の添付の段階で中止するとトップに戻る(secrets):
    at = new_app(secrets)
    at.button(key="start_demo").click().run()
    at.button(key="submit_info").click().run()
    at.button(key="finish").click().run()

    at.button(key="cancel").click().run()

    assert at.session_state.stage == "top"
    assert at.session_state.attachments == []


def test_診断を中止するとトップに戻り内容が消える(secrets):
    at = new_app(secrets)
    at.button(key="start_demo").click().run()
    at.button(key="submit_info").click().run()

    at.button(key="cancel").click().run()

    assert at.session_state.stage == "top"
    assert at.session_state.hearing_state is None


# ---------------------------------------------------------------
# 入力ミス（異常系）
# ---------------------------------------------------------------
def test_必須項目が空なら次に進めず_項目ごとにエラーを表示する(secrets):
    at = new_app(secrets)
    at.button(key="start_demo").click().run()
    at.text_input(key="f_department").set_value("")
    at.text_area(key="f_pain_points").set_value("   ")
    at.button(key="submit_info").click().run()

    assert at.session_state.stage == "input"
    errors = [e.value for e in at.error]
    assert "部署名を入力してください" in errors
    assert "現在困っていることを入力してください" in errors
    # 入力済みの他の項目は消えていない
    assert at.text_input(key="f_task_name").value == "月次売上集計"


def test_回答が空なら次の質問に進まない(secrets):
    at = new_app(secrets)
    at.button(key="start_demo").click().run()
    at.button(key="submit_info").click().run()
    at.text_area(key="answer_1").set_value("")
    at.button(key="submit_answer").click().run()

    assert "回答を入力してください" in [e.value for e in at.error]
    assert len(at.session_state.hearing_state.turns) == 1


# ---------------------------------------------------------------
# AIモードの入口（APIは呼ばない範囲）
# ---------------------------------------------------------------
def test_APIキーやパスワードが未設定ならAI診断は使えない(secrets):
    at = new_app(secrets)
    assert "AI診断は現在利用できません" in all_text(at)
    assert not [b for b in at.button if b.key == "start_ai"]


def test_パスワードが違えばAI診断を始められない(secrets, db):
    secrets.update(ANTHROPIC_API_KEY="dummy", APP_PASSWORD="correct")
    at = new_app(secrets)
    at.text_input(key="password").set_value("wrong")
    at.button(key="start_ai").click().run()

    assert at.session_state.stage == "top"
    assert "パスワードが正しくありません。" in [e.value for e in at.error]


def test_正しいパスワードならAI診断の入力画面に進む(secrets, db):
    secrets.update(ANTHROPIC_API_KEY="dummy", APP_PASSWORD="correct")
    at = new_app(secrets)
    at.text_input(key="password").set_value("correct")
    at.button(key="start_ai").click().run()

    assert at.session_state.stage == "input"
    assert at.session_state.mode == "ai"
    assert at.text_input(key="f_department").value == ""  # AI診断では入力欄は空
    # 「診断を始める」の時点で、その診断の枠を1つ確保している（X01）
    assert count_slots_on(db, datetime.now(JST).date()) == 1


def test_1日の上限に達していればAI診断を始められない(secrets, db):
    secrets.update(ANTHROPIC_API_KEY="dummy", APP_PASSWORD="correct")
    init_db(db)
    now = datetime.now(JST)
    for n in range(DAILY_DIAGNOSIS_LIMIT):
        reserve_slot(db, f"s{n}", DAILY_DIAGNOSIS_LIMIT, now)

    at = new_app(secrets)
    at.text_input(key="password").set_value("correct")
    at.button(key="start_ai").click().run()

    assert at.session_state.stage == "top"
    assert any("上限" in e.value for e in at.error)
