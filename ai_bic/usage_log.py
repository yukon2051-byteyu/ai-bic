"""利用状況の記録（F06、基本設計書 10.2）と、1日の診断の枠（K03）。

1回のAI呼び出しを1行として SQLite に記録する。
業務内容・回答・AIの回答本文は記録しない（S03・S06）。
record_call() には本文を渡す引数そのものがないため、誤って記録することもできない。

1日の診断回数は、「診断を始める」を押した時点で枠を確保して数える（X01）。
AIを呼んだ記録で数えると、AIを呼ぶ前に複数の診断を始めたときに、すべてが上限の確認を通ってしまうため。
"""

import sqlite3
from contextlib import closing
from datetime import date, datetime

from ai_bic.config import JST

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS usage_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT    NOT NULL,  -- 日本時間（例：2026-11-01T10:30:00+09:00）
    session_id    TEXT    NOT NULL,  -- 診断ごとのランダムなID（個人は特定しない）
    call_type     TEXT    NOT NULL CHECK (call_type IN ('question', 'analysis', 'proposal')),
    model         TEXT    NOT NULL,
    input_tokens  INTEGER NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
    output_tokens INTEGER NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
    status        TEXT    NOT NULL CHECK (status IN ('success', 'error')),
    error_type    TEXT              -- エラーの種類（errors.py の kind）。成功時は NULL
)
"""

CREATE_SLOT_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS diagnosis_slot (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    reserved_at   TEXT    NOT NULL,         -- 日本時間（例：2026-11-01T10:30:00+09:00）
    session_id    TEXT    NOT NULL UNIQUE   -- 診断ごとのランダムなID（同じ診断で2回確保しない）
)
"""


def _connect(db_path: str) -> sqlite3.Connection:
    return sqlite3.connect(db_path)


def init_db(db_path: str) -> None:
    """usage_log テーブルがなければ作る。何度呼んでもよい。"""
    with closing(_connect(db_path)) as conn, conn:
        conn.execute(CREATE_TABLE_SQL)
        conn.execute(CREATE_SLOT_TABLE_SQL)


def record_call(
    db_path: str,
    session_id: str,
    call_type: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    status: str,
    error_type: str | None = None,
    now: datetime | None = None,
) -> None:
    """1回のAI呼び出しを記録する。now はテストで日時を指定するときに使う。"""
    created_at = (now or datetime.now(JST)).isoformat(timespec="seconds")
    with closing(_connect(db_path)) as conn, conn:
        conn.execute(
            """
            INSERT INTO usage_log
                (created_at, session_id, call_type, model,
                 input_tokens, output_tokens, status, error_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (created_at, session_id, call_type, model,
             input_tokens, output_tokens, status, error_type),
        )


def count_sessions_on(db_path: str, day: date) -> int:
    """指定した日（日本時間）に、AIを1回以上呼んだ診断の数を返す（1日の回数制限 K03 に使う）。"""
    with closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT COUNT(DISTINCT session_id) FROM usage_log WHERE substr(created_at, 1, 10) = ?",
            (day.isoformat(),),
        ).fetchone()
    return row[0]


def reserve_slot(db_path: str, session_id: str, daily_limit: int, now: datetime | None = None) -> bool:
    """今日（日本時間）の診断の枠を1つ確保する。上限に達していれば確保せず False を返す（X01）。

    「数える」と「確保する」を1つのまとまった処理（トランザクション）として行う。
    BEGIN IMMEDIATE で書き込みの順番待ちを先に取るため、同時に呼ばれても、
    2つの処理が同じ残り枠を数えて両方とも確保してしまうことがない。
    """
    reserved_at = (now or datetime.now(JST)).astimezone(JST)
    conn = sqlite3.connect(db_path, isolation_level=None, timeout=10)  # 順番待ちは最大10秒
    try:
        conn.execute("BEGIN IMMEDIATE")
        used = conn.execute(
            "SELECT COUNT(*) FROM diagnosis_slot WHERE substr(reserved_at, 1, 10) = ?",
            (reserved_at.date().isoformat(),),
        ).fetchone()[0]
        if used >= daily_limit:
            conn.execute("ROLLBACK")
            return False
        conn.execute(
            "INSERT INTO diagnosis_slot (reserved_at, session_id) VALUES (?, ?)",
            (reserved_at.isoformat(timespec="seconds"), session_id),
        )
        conn.execute("COMMIT")
        return True
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def count_slots_on(db_path: str, day: date) -> int:
    """指定した日（日本時間）に確保された診断の枠の数を返す。"""
    with closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM diagnosis_slot WHERE substr(reserved_at, 1, 10) = ?",
            (day.isoformat(),),
        ).fetchone()
    return row[0]
