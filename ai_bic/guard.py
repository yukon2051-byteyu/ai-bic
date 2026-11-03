"""利用制限（S05 アクセス用パスワード・K03 1日の診断回数）。

公開環境で第三者に大量に使われ、料金が発生するのを抑える。
ただし、これはアプリの中の対策にすぎない。確実な上限は
Anthropic Console の利用上限（Spend limit）で設定する（K06）。

1日の回数は、パスワードを知っている人全員の合計で数える（人ごとではない）。
記録は SQLite のファイルにあるため、公開環境の再起動で消えると、その日の回数も0に戻る（X01）。
"""

import hmac
from datetime import datetime

from ai_bic.config import (
    DAILY_DIAGNOSIS_LIMIT,
    SECRET_API_KEY,
    SECRET_APP_PASSWORD,
    get_secret,
)
from ai_bic.usage_log import init_db, reserve_slot


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


def reserve_diagnosis(db_path: str, session_id: str, now: datetime | None = None) -> bool:
    """今日（日本時間）の診断の枠を1つ確保できたかを返す（上限に達していれば False）。

    確認と確保を同時に行うため、同時に「診断を始める」が押されても上限を超えない（X01）。
    now はテストで日時を指定するときに使う。
    """
    init_db(db_path)
    return reserve_slot(db_path, session_id, DAILY_DIAGNOSIS_LIMIT, now)
