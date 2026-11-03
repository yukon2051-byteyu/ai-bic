"""guard.py のテスト（S05 アクセス用パスワード・K03 1日の診断回数）。

本物の secrets.toml の内容に左右されないよう、get_secret を偽物に差し替える。
"""

import threading
from datetime import datetime, timedelta, timezone

import pytest

from ai_bic import guard
from ai_bic.config import DAILY_DIAGNOSIS_LIMIT, JST
from ai_bic.usage_log import count_slots_on, init_db


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


def fill_slots(db, count, day=NOW):
    for n in range(count):
        assert guard.reserve_diagnosis(db, f"s{n}", day) is True


def test_記録がなければ診断を始められる(db):
    assert guard.reserve_diagnosis(db, "new", NOW) is True
    assert count_slots_on(db, NOW.date()) == 1  # 始めた時点で枠を確保する


def test_上限の1つ手前までは始められる(db):
    fill_slots(db, DAILY_DIAGNOSIS_LIMIT - 1)
    assert guard.reserve_diagnosis(db, "new", NOW) is True


def test_上限に達したら始められず_枠も増えない(db):
    fill_slots(db, DAILY_DIAGNOSIS_LIMIT)
    assert guard.reserve_diagnosis(db, "new", NOW) is False
    assert count_slots_on(db, NOW.date()) == DAILY_DIAGNOSIS_LIMIT


def test_日付が変われば_また始められる(db):
    fill_slots(db, DAILY_DIAGNOSIS_LIMIT)
    assert guard.reserve_diagnosis(db, "new", NOW + timedelta(days=1)) is True


def test_UTCの日時を渡しても日本時間の日付で判定する(db):
    fill_slots(db, DAILY_DIAGNOSIS_LIMIT)  # 日本時間 11/1 に上限まで使用
    # UTC 10/31 20:00 は 日本時間 11/1 5:00 → 同じ日なので始められない
    utc_now = datetime(2026, 10, 31, 20, 0, tzinfo=timezone.utc)
    assert guard.reserve_diagnosis(db, "new", utc_now) is False


def test_AIを呼ぶ前の診断も_1回として数える(db):
    # 以前は「AIを呼んだ診断」だけを数えたため、AIを呼ぶ前に複数始めると上限をすり抜けられた（X01）
    for n in range(DAILY_DIAGNOSIS_LIMIT):
        guard.reserve_diagnosis(db, f"tab{n}", NOW)  # タブを開いて始めただけ（AIはまだ呼んでいない）
    assert guard.reserve_diagnosis(db, "one_more", NOW) is False


def test_同時に始めても_上限を超えて確保しない(db):
    # 30人が同時に「診断を始める」を押した場面を、スレッドで再現する（X01）
    barrier = threading.Barrier(30)
    results = []

    def press(n):
        barrier.wait()  # 30個のスレッドがそろってから、一斉に確保する
        results.append(guard.reserve_diagnosis(db, f"user{n}", NOW))

    threads = [threading.Thread(target=press, args=(n,)) for n in range(30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(True) == DAILY_DIAGNOSIS_LIMIT
    assert count_slots_on(db, NOW.date()) == DAILY_DIAGNOSIS_LIMIT
