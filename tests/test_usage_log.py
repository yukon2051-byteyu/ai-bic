"""usage_log.py のテスト（F06 利用状況の記録）。

毎回、一時フォルダに使い捨てのデータベースを作る（本番の usage.db には触れない）。
"""

import sqlite3
from contextlib import closing
from datetime import date, datetime

import pytest

from ai_bic.config import JST
from ai_bic.usage_log import count_sessions_on, init_db, record_call


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "usage_test.db")
    init_db(path)
    return path


def at(day: int, hour: int = 10) -> datetime:
    return datetime(2026, 11, day, hour, 0, tzinfo=JST)


def record(db, session_id, now, call_type="question", status="success", error_type=None):
    record_call(db, session_id, call_type, "claude-haiku-5-5", 1200, 300, status, error_type, now)


def test_呼び出しを記録できる(db):
    record(db, "s1", at(1))
    with closing(sqlite3.connect(db)) as conn:
        row = conn.execute(
            "SELECT created_at, session_id, call_type, input_tokens, output_tokens, status FROM usage_log"
        ).fetchone()
    assert row == ("2026-11-01T10:00:00+09:00", "s1", "question", 1200, 300, "success")


def test_エラーも種類つきで記録できる(db):
    record(db, "s1", at(1), status="error", error_type="rate_limit")
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT error_type FROM usage_log").fetchone() == ("rate_limit",)


def test_業務内容を保存する列がない(db):
    # 本文を記録しようがない構造であることを確認する（S03・S06）
    with closing(sqlite3.connect(db)) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(usage_log)")}
    assert columns == {
        "id", "created_at", "session_id", "call_type", "model",
        "input_tokens", "output_tokens", "status", "error_type",
    }


def test_1日の診断数は診断IDの種類で数える(db):
    # 1つの診断で何回AIを呼んでも「1診断」と数える
    for _ in range(3):
        record(db, "s1", at(1))
    record(db, "s2", at(1), call_type="analysis")
    assert count_sessions_on(db, date(2026, 11, 1)) == 2


def test_別の日の記録は数えない(db):
    record(db, "s1", at(1))
    record(db, "s2", at(2))
    assert count_sessions_on(db, date(2026, 11, 1)) == 1
    assert count_sessions_on(db, date(2026, 11, 3)) == 0


def test_日付は日本時間で判定する(db):
    # 日本時間 11/2 の 0:30 は、UTCでは 11/1 の 15:30。日本時間の 11/2 として数える
    record(db, "s1", at(2, hour=0).replace(minute=30))
    assert count_sessions_on(db, date(2026, 11, 2)) == 1
    assert count_sessions_on(db, date(2026, 11, 1)) == 0


def test_何度初期化しても記録は消えない(db):
    record(db, "s1", at(1))
    init_db(db)
    assert count_sessions_on(db, date(2026, 11, 1)) == 1


@pytest.mark.parametrize(
    ("call_type", "status", "tokens"),
    [
        ("chat", "success", 0),  # 決められていない呼び出し種別
        ("question", "ok", 0),  # 決められていない状態
        ("question", "success", -1),  # マイナスのトークン数
    ],
)
def test_ありえない値はデータベースが拒否する(db, call_type, status, tokens):
    with pytest.raises(sqlite3.IntegrityError):
        record_call(db, "s1", call_type, "m", tokens, 0, status, None, at(1))


# ---------------------------------------------------------------
# 呼び出しの種類が増えたとき（AI-BIC 2.0：業務フロー・レビュー・再生成）
# ---------------------------------------------------------------
OLD_TABLE_SQL = """
CREATE TABLE usage_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT    NOT NULL,
    session_id    TEXT    NOT NULL,
    call_type     TEXT    NOT NULL CHECK (call_type IN ('question', 'analysis', 'proposal')),
    model         TEXT    NOT NULL,
    input_tokens  INTEGER NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
    output_tokens INTEGER NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
    status        TEXT    NOT NULL CHECK (status IN ('success', 'error')),
    error_type    TEXT
)
"""


@pytest.mark.parametrize("call_type", ["flow", "review", "revise"])
def test_新しい種類の呼び出しも記録できる(tmp_path, call_type):
    db = str(tmp_path / "u.db")
    init_db(db)
    record_call(db, "s1", call_type, "m", 10, 5, "success")
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT call_type FROM usage_log").fetchone()[0] == call_type


def test_古い制約の表は_記録を引き継いで作り直す(tmp_path):
    """公開前に作られた usage.db でも、業務フローの呼び出しを記録できる（制約が古いままだとエラーになっていた）。"""
    db = str(tmp_path / "old.db")
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute(OLD_TABLE_SQL)
        conn.execute("INSERT INTO usage_log (created_at, session_id, call_type, model, status)"
                     " VALUES ('2026-11-01T10:00:00+09:00', 's0', 'analysis', 'm', 'success')")
    init_db(db)
    record_call(db, "s1", "flow", "m", 10, 5, "success")
    with closing(sqlite3.connect(db)) as conn:
        rows = conn.execute("SELECT session_id, call_type FROM usage_log ORDER BY id").fetchall()
    assert rows == [("s0", "analysis"), ("s1", "flow")]
    init_db(db)  # 2回目以降は作り直さない
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM usage_log").fetchone()[0] == 2
