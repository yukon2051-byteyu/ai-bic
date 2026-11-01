"""利用制限（S05 アクセス用パスワード・K03 1日の診断回数）。

公開環境で第三者に大量に使われ、料金が発生するのを抑える。
ただし、これはアプリの中の対策にすぎない。確実な上限は
Anthropic Console の利用上限（Spend limit）で設定する（K06）。
"""

import hmac
from datetime import datetime

from ai_bic.config import (
    DAILY_DIAGNOSIS_LIMIT,
    JST,
    SECRET_API_KEY,
    SECRET_APP_PASSWORD,
    get_secret,
)
from ai_bic.usage_log import count_sessions_on, init_db


def ai_available() -> bool:
    """AI診断を使える設定か（APIキーとアクセス用パスワードの両方があるか）を返す。

    パスワードが未設定なら、AI診断は使えない（決定事項B2：設定漏れで誰でも使える状態にしない）。
    """
    return bool(get_secret(SECRET_API_KEY) and get_secret(SECRET_APP_PASSWORD))


def check_password(entered: str | None) -> bool:
    """入力されたパスワードが正しいかを返す。パスワードが未設定なら常に False。

    hmac.compare_digest は、比較にかかる時間から正解を推測されないようにする比較方法。
    """
    expected = get_secret(SECRET_APP_PASSWORD)
    if not expected or not entered:
        return False
    return hmac.compare_digest(entered.encode("utf-8"), expected.encode("utf-8"))


def can_start_diagnosis(db_path: str, now: datetime | None = None) -> bool:
    """今日（日本時間）の診断回数が上限未満かを返す。now はテストで日時を指定するときに使う。"""
    init_db(db_path)
    today = (now or datetime.now(JST)).astimezone(JST).date()
    return count_sessions_on(db_path, today) < DAILY_DIAGNOSIS_LIMIT
