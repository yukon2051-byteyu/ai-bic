"""guard.py のテスト（S05 アクセス用パスワード・K03 1日の診断回数）。

本物の secrets.toml の内容に左右されないよう、get_secret を偽物に差し替える。
"""

from datetime import datetime, timedelta, timezone

import pytest

from ai_bic import guard
from ai_bic.config import DAILY_DIAGNOSIS_LIMIT, JST
from ai_bic.usage_log import init_db, record_call


@pytest.fixture
def secrets(monkeypatch):
    """テスト用の Secrets。辞書に入れた値だけが「設定済み」になる。"""
    values = {}
    monkeypatch.setattr(guard, "get_secret", lambda name: values.get(name) or None)
    return values


# --- AI診断を使えるか ---


def test_APIキーとパスワードの両方があればAI診断を使える(secrets):
    secrets.update(ANTHROPIC_API_KEY="dummy", APP_PASSWORD="pass")
    assert guard.ai_available() is True


@pytest.mark.parametrize(
    "values",
    [
        {"APP_PASSWORD": "pass"},  # APIキーが未設定
        {"ANTHROPIC_API_KEY": "dummy"},  # パスワードが未設定（決定事項B2）
        {"ANTHROPIC_API_KEY": "dummy", "APP_PASSWORD": ""},  # パスワードが空
        {},
    ],
)
def test_どちらかが未設定ならAI診断は使えない(secrets, values):
    secrets.update(values)
    assert guard.ai_available() is False


# --- パスワード ---


def test_正しいパスワードなら通す(secrets):
    secrets["APP_PASSWORD"] = "correct-horse"
    assert guard.check_password("correct-horse") is True


@pytest.mark.parametrize("entered", ["wrong", "", None, "correct-hors", "correct-horse "])
def test_違うパスワードや空欄は通さない(secrets, entered):
    secrets["APP_PASSWORD"] = "correct-horse"
    assert guard.check_password(entered) is False


def test_パスワードが未設定なら何を入れても通さない(secrets):
    assert guard.check_password("") is False
    assert guard.check_password("anything") is False


def test_日本語のパスワードも扱える(secrets):
    secrets["APP_PASSWORD"] = "業務改善2026"
    assert guard.check_password("業務改善2026") is True


# --- 1日の診断回数 ---


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "usage_test.db")
    init_db(path)  # 記録を書き込む前にテーブルを作っておく
    return path


NOW = datetime(2026, 11, 1, 15, 0, tzinfo=JST)


def fill_sessions(db, count, day=NOW):
    for n in range(count):
        record_call(db, f"s{n}", "question", "m", 0, 0, "success", None, day)


def test_記録がなければ診断を始められる(db):
    assert guard.can_start_diagnosis(db, NOW) is True


def test_上限の1つ手前までは始められる(db):
    fill_sessions(db, DAILY_DIAGNOSIS_LIMIT - 1)
    assert guard.can_start_diagnosis(db, NOW) is True


def test_上限に達したら始められない(db):
    fill_sessions(db, DAILY_DIAGNOSIS_LIMIT)
    assert guard.can_start_diagnosis(db, NOW) is False


def test_日付が変われば_また始められる(db):
    fill_sessions(db, DAILY_DIAGNOSIS_LIMIT)
    assert guard.can_start_diagnosis(db, NOW + timedelta(days=1)) is True


def test_UTCの日時を渡しても日本時間の日付で判定する(db):
    fill_sessions(db, DAILY_DIAGNOSIS_LIMIT)  # 日本時間 11/1 に上限まで使用
    # UTC 10/31 20:00 は 日本時間 11/1 5:00 → 同じ日なので始められない
    utc_now = datetime(2026, 10, 31, 20, 0, tzinfo=timezone.utc)
    assert guard.can_start_diagnosis(db, utc_now) is False
