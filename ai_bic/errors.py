"""AI呼び出しのエラーの種類と、利用者向けメッセージ（詳細設計書 3.3）。

本物のAI・デモ・テスト用の偽物のすべてがこの AIError を使う。
画面には技術的な詳細を出さず、user_message() の日本語メッセージだけを表示する（N05）。
"""

# エラーの種類 → (利用者向けメッセージ, 再試行ボタンを出すか)
ERROR_INFO: dict[str, tuple[str, bool]] = {
    "auth": (
        "AIサービスに接続できませんでした（設定の問題）。管理者にお問い合わせください。",
        False,
    ),
    "rate_limit": (
        "AIサービスが混み合っています。少し待ってから再試行してください。",
        True,
    ),
    "server": (
        "AIサービスで一時的な障害が発生しています。少し待ってから再試行してください。",
        True,
    ),
    "connection": (
        "AIサービスと通信できませんでした。インターネット接続を確認して再試行してください。",
        True,
    ),
    "refusal": (
        "この内容ではAI診断を行えませんでした。入力内容を見直してください。",
        False,
    ),
    "truncated": (
        "AIの回答を正しく読み取れませんでした。もう一度お試しください。",
        True,
    ),
    "invalid_output": (
        "AIの回答を正しく読み取れませんでした。もう一度お試しください。",
        True,
    ),
    "bad_request": (
        "システムの不具合により処理できませんでした。管理者にお問い合わせください。",
        False,
    ),
}

# 一覧にない種類が来たときの表示（プログラムの不具合に備える）
UNKNOWN_ERROR = ("予期しないエラーが発生しました。もう一度お試しください。", True)


class AIError(Exception):
    """AI呼び出しの失敗。kind にエラーの種類（ERROR_INFO のキー）を持つ。

    detail は開発者向けの補足（画面には出さない）。業務内容などの本文は入れないこと。
    """

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind
        self.detail = detail


def user_message(kind: str) -> str:
    """エラーの種類に対応する、利用者向けのメッセージを返す。"""
    return ERROR_INFO.get(kind, UNKNOWN_ERROR)[0]


def is_retryable(kind: str) -> bool:
    """再試行ボタンを出すべきエラーかを返す。"""
    return ERROR_INFO.get(kind, UNKNOWN_ERROR)[1]
