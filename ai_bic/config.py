"""設定値の一元管理（詳細設計書 4章）。

数値やモデル名をプログラムのあちこちに書かず、ここにまとめる。
値を変えるときは、このファイルだけを直せばよい（保守性 M02）。
"""

import os
from datetime import timedelta, timezone
from pathlib import Path

from ai_bic.models import Framework

# --- フォルダの場所 ---
BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"

# --- AIモデル（決定事項Q2。2026-11-01 に分析・提案を Sonnet 5.5 から Opus 5.5 に変更） ---
MODEL_QUESTION = "claude-haiku-5-5"  # 1問ごとに利用者を待たせるため、速いモデル
MODEL_ANALYSIS = "claude-opus-5-5"  # 成果物の価値を決める部分のため、最も高性能なモデル
MODEL_PROPOSAL = "claude-opus-5-5"

# --- AIの考える深さ（詳細設計 2.7。品質評価の結果で調整する） ---
EFFORT_QUESTION = "low"
EFFORT_ANALYSIS = "medium"
EFFORT_PROPOSAL = "medium"

# --- 出力の上限（暴走したときの歯止め。料金は実際に出力した分だけかかる） ---
MAX_TOKENS_QUESTION = 4_000
MAX_TOKENS_ANALYSIS = 16_000
MAX_TOKENS_PROPOSAL = 16_000

# --- 件数・回数の上限 ---
MAX_HEARING_TURNS = 5  # ヒアリングの質問回数（R5）。基本の質問の上限

# --- 情報の充足度（AI-BIC 2.0）：3機能それぞれに必要な診断項目（開発者が承認した対応表） ---
# required がすべて「充足」なら、その機能に必要な情報はそろっている。helpful は、あると精度が上がる項目
FEATURE_REQUIREMENTS = {
    "flow": {"name": "業務フロー", "required": ["H2"], "helpful": ["H4"]},
    "simulation": {"name": "改善効果の試算", "required": ["H3"], "helpful": ["H2"]},
    "review": {"name": "改善案レビュー", "required": ["H5", "H6"], "helpful": ["H1"]},
}
# 機能に必要な情報が足りないとき、利用者が選べば行う追加確認の上限（質問を増やすこと自体を目的にしない）
MAX_EXTRA_QUESTIONS = 3
# 情報が足りないとき、未確認のまま分析した場合に結果に示す「仮定条件」
FEATURE_ASSUMPTIONS = {
    "flow": "業務の手順が未確認のため、業務フローの工程には推測が含まれます（「要確認」の工程を確かめてください）。",
    "simulation": "作業時間・頻度が未確認のため、効果試算の作業時間・件数は仮定（AI推定や入力した値）に基づきます。",
    "review": "業務上の問題点または改善の制約条件が未確認のため、改善案が実行できるかの評価は仮定に基づきます。",
}
MAX_QUESTIONS_PER_ITEM = 2  # 同じ診断項目への質問回数の上限（同じことを聞き続けるのを防ぐ。R3）
# 「分からない」と答えた項目は、それ以上質問しない（R3）。
# 回答からこれらの言葉と決まり文句を取り除き、中身がほとんど残らなければ「分からない」とみなす
UNKNOWN_ANSWER_WORDS = ("分かりません", "わかりません", "分からない", "わからない", "不明", "把握していません", "把握していない", "知りません")
UNKNOWN_ANSWER_FILLERS = (
    "すみません", "すいません", "申し訳ありません", "その点は", "そこは", "それは", "ちょっと", "よく",
    "正直", "私には", "私は", "今は", "です", "ね", "、", "。", "…", "！", "!",
)
UNKNOWN_ANSWER_MAX_REST_CHARS = 2  # 取り除いたあとに残ってよい文字数
MAX_ISSUES = 5  # 課題の件数（決定事項C2）
MAX_PROPOSALS = 3  # 改善案の件数

# --- 改善効果シミュレーター（AI-BIC 2.0 機能A） ---
# 悲観・楽観シナリオの初期値：(削減率の倍率, 費用の倍率)。根拠のある予測ではなく「仮の幅」。
# 画面では利用者が自由に変えられる（開発者の判断：まず幅を見せ、自社の事情で直してもらう）
SCENARIO_FACTORS = {"悲観": (0.5, 1.5), "楽観": (1.5, 1.0)}

# --- 業務フロー（AI-BIC 2.0 機能B） ---
MAX_FLOW_STEPS = 30  # 1つのフローの工程数の上限（図が読めなくなるのと、AIの出力の暴走を防ぐ）
MODEL_FLOW = "claude-opus-5-5"  # 課題分析・改善提案と同じモデル（工程の抽出と改善後の設計の品質が成果物の価値を決めるため）
EFFORT_FLOW = "medium"
MAX_TOKENS_FLOW = 16_000
MAX_FLOW_CALLS = 1  # 1案件で業務フローをAIで作れる回数（決定事項：費用を抑える。結果は画面で修正できる）

# --- 改善案チャレンジレビュー（AI-BIC 2.0 機能D） ---
# 改善案を作ったAIとは別の指示文で評価する。同じモデルでも、指示文と処理を分ける（指示書 5.3）
MODEL_REVIEW = "claude-opus-5-5"
EFFORT_REVIEW = "medium"
MAX_TOKENS_REVIEW = 16_000
MAX_REVIEW_CALLS = 3  # 1案件でレビューできる回数（決定事項。利用者のボタン操作だけで実行し、自動で繰り返さない）
MAX_REVISE_CALLS = 2  # 1案件で改善案を作り直せる回数（決定事項）
DOT_TIMEOUT_SECONDS = 20  # フロー図を画像にする Graphviz の待ち時間の上限
MAX_STEP_TEXT = 60  # 工程名・担当者・システム名の文字数の上限
MAX_STEP_NOTE = 200  # 作業内容・課題・改善内容などの文字数の上限

# --- 案件の保存・読み込み（AI-BIC 2.0 9.1） ---
MAX_PROJECT_FILE_BYTES = 1 * 1024 * 1024  # 読み込む案件ファイルの大きさの上限（1MB）
MAX_FIRST_STEPS = 3  # 改善案ごとの「最初の一歩」の件数
DAILY_DIAGNOSIS_LIMIT = 20  # 1日の診断回数（決定事項Q4）
INVALID_OUTPUT_RETRY = 1  # AI出力のチェックに失敗したときの呼び直し回数
MIN_QUOTE_CHARS = 4  # 根拠の引用の最小文字数。「時間」のような短すぎる引用は根拠と認めない
# 引用のすぐ後にこれが続くと、否定を切り取った引用とみなす（例：「ミスがある」＋「ことはない」。B04）
NEGATION_SUFFIXES = (
    "ない", "なかった", "なく", "ません", "ずに",
    "ことはない", "ことはありません", "ことはなかった", "ことはほとんどない",
    "わけではない", "わけではありません", "とは限らない", "とは限りません",
)
# 「計算結果」の計算式で、入力になくても使ってよい数値（単位の換算。例：90分÷60＝1.5時間。B04）
CALCULATION_CONSTANTS = {60, 24, 7, 12, 100, 1000}

# --- API呼び出し ---
API_MAX_RETRIES = 2  # SDKによる自動再試行（利用制限・サーバー障害・通信エラー）
API_TIMEOUT_SECONDS = 90

# --- 入力文字数の上限（F01） ---
FIELD_LIMITS = {
    "company_name": 50,
    "department": 50,
    "task_name": 50,
    "overview": 1_000,
    "pain_points": 1_000,
}
ANSWER_MAX_CHARS = 1_000

# --- 添付ファイル（F09） ---
ATTACHMENT_EXTENSIONS = (".csv", ".txt", ".xlsx")
MAX_ATTACHMENTS = 3  # ファイル数の上限
MAX_ATTACHMENT_CHARS = 10_000  # 全ファイルの合計文字数の上限（超えたら切り捨てずにエラー）
MAX_ATTACHMENT_BYTES = 2 * 1024 * 1024  # 1ファイルの大きさの上限（2MB。読み込みの負荷を抑える）

# --- 日本時間（日本にはサマータイムがないため、UTC+9の固定値で扱う） ---
JST = timezone(timedelta(hours=9), "JST")

# --- 利用状況の記録先（テストでは環境変数で一時ファイルを指定する） ---
DB_PATH = os.environ.get("AI_BIC_DB_PATH", str(BASE_DIR / "usage.db"))

# --- Secrets の名前（値は .streamlit/secrets.toml に書く。Gitには含めない） ---
SECRET_API_KEY = "ANTHROPIC_API_KEY"
SECRET_APP_PASSWORD = "APP_PASSWORD"


def get_secret(name: str) -> str | None:
    """Streamlit Secrets → 環境変数 の順に探して値を返す。見つからなければ None。

    空文字（設定はあるが中身がない）も「未設定」として None を返す。
    """
    value = None
    try:
        import streamlit as st

        value = st.secrets.get(name)
    except Exception:
        # secrets.toml がない場合などは例外になるので、環境変数を探しに行く
        value = None

    if not value:
        value = os.environ.get(name)

    return value or None


def load_framework(framework_id: str = "general") -> Framework:
    """config/framework_<id>.json を読み込んで返す。

    ファイルがなければ FileNotFoundError、形が正しくなければ
    pydantic の ValidationError が発生する。
    """
    path = CONFIG_DIR / f"framework_{framework_id}.json"
    return Framework.model_validate_json(path.read_text(encoding="utf-8"))
