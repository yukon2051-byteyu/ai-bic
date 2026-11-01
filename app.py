"""AI-BIC の画面（Streamlit）。起動：streamlit run app.py

画面は表示と入力の受け取りだけを行い、業務ロジックは ai_bic パッケージに任せる（方針P3）。
1ページの中で「トップ → 業務情報 → ヒアリング → 資料の添付 → 診断結果」を切り替える（決定事項B1）。

Streamlit は操作のたびにこのファイルを最初から実行し直す。
AIの呼び出しは advance()（質問）と diagnose()（分析・提案）の2か所にまとめ、
ボタンが押されたときだけ呼ぶ（二重呼び出しの防止 K04）。
"""

import uuid

import streamlit as st

from ai_bic import config, guard
from ai_bic.ai_client import ClaudeClient
from ai_bic.attachments import load_attachments
from ai_bic.demo_client import DemoClient, demo_answer, demo_attachments, load_scenarios
from ai_bic.errors import AIError, is_retryable, user_message
from ai_bic.hearing import (
    add_answer,
    finish_by_user,
    request_next_question,
    run_diagnosis,
    start_hearing,
)
from ai_bic.validation import validate_answer, validate_business_info

st.set_page_config(page_title="AI-BIC 業務改善診断", page_icon="📋", layout="centered")

FRAMEWORK = config.load_framework("general")
ITEM_NAMES = {item.id: item.name for item in FRAMEWORK.hearing_items}
VIEWPOINT_NAMES = {v.id: v.name for v in FRAMEWORK.viewpoints}

NOTICE = "個人情報や社外秘の情報は入力しないでください。企業名は空欄または架空の名前をおすすめします。"
STEPS = [
    ("input", "① 業務情報"),
    ("hearing", "② ヒアリング"),
    ("attach", "③ 資料の添付"),
    ("result", "④ 診断結果"),
]
SEVERITY_LABEL = {"高": ":red[**重要度：高**]", "中": ":orange[**重要度：中**]", "低": ":gray[**重要度：低**]"}
EVIDENCE_LABEL = {
    "事実": "✅ 事実（発言で確認済み）",
    "推測": "💭 推測（発言からの推測）",
    "要確認": "❓ 要確認（判断材料が不足）",
}


# ---------------------------------------------------------------
# 画面が保持するデータ（詳細設計 3.4）
# ---------------------------------------------------------------
def init_state() -> None:
    defaults = {
        "stage": "top",  # top / input / hearing / attach / result
        "mode": None,  # ai / demo
        "scenario": None,  # デモのシナリオ
        "session_id": None,  # 診断ごとのランダムなID
        "raw_input": {},  # 入力欄の値（エラー時に入力を消さないため）
        "input_errors": {},
        "business_info": None,
        "hearing_state": None,
        "attachments": [],  # 読み込んだ添付ファイル（保存はしない）
        "attach_errors": [],
        "diagnosis": None,
        "error_kind": None,  # 直前のAIエラーの種類
        "answer_error": None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def reset() -> None:
    """診断の内容をすべて消して、トップに戻る。"""
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    init_state()


def start(mode: str, scenario: dict | None = None) -> None:
    reset()
    s = st.session_state
    s.mode = mode
    s.scenario = scenario
    s.session_id = uuid.uuid4().hex
    s.raw_input = dict(scenario["business_info"]) if scenario else {}
    s.stage = "input"


def get_client():
    s = st.session_state
    if s.mode == "demo":
        return DemoClient(s.scenario)
    return ClaudeClient(
        api_key=config.get_secret(config.SECRET_API_KEY),
        session_id=s.session_id,
        db_path=config.DB_PATH,
    )


def advance() -> None:
    """ヒアリングを1歩進める（次の質問を作る）。終われば「資料の添付」に進む。

    エラーが起きたら種類を記録し、再試行ボタンから再度呼ぶ。
    """
    s = st.session_state
    s.error_kind = None
    try:
        with st.spinner("AIが次の質問を考えています…"):
            turn = request_next_question(get_client(), s.business_info, s.hearing_state, FRAMEWORK)
    except AIError as e:
        s.error_kind = e.kind
        return
    if turn is None:
        s.stage = "attach"  # ヒアリング終了 → 資料の添付へ（ここではAIを呼ばない）


def diagnose() -> None:
    """課題分析と改善提案を行い、結果画面に進む。添付ファイルは課題分析にだけ使う。"""
    s = st.session_state
    s.error_kind = None
    try:
        with st.spinner("AIが課題を分析し、改善案を考えています…（1分ほどかかることがあります）"):
            s.diagnosis = run_diagnosis(
                get_client(), s.business_info, s.hearing_state, FRAMEWORK, s.attachments
            )
    except AIError as e:
        s.error_kind = e.kind
        return
    s.stage = "result"


# ---------------------------------------------------------------
# 共通の表示
# ---------------------------------------------------------------
def render_header() -> None:
    s = st.session_state
    st.title("📋 AI-BIC 業務改善診断")
    st.caption("AIが業務改善コンサルタントとしてヒアリングし、課題と改善案を根拠つきで整理します")

    if s.mode == "demo":
        st.info(
            "**デモモード**：AIは使用していません。架空の業務と、あらかじめ用意した固定の結果を表示しています。",
            icon="🧪",
        )

    if s.stage != "top":
        current = [key for key, _ in STEPS].index(s.stage)
        labels = [
            f"**{label}**" if i == current else (f"~~{label}~~" if i < current else f":gray[{label}]")
            for i, (_, label) in enumerate(STEPS)
        ]
        st.markdown("　→　".join(labels))
        st.divider()


def render_error(retry) -> None:
    """AIエラーのメッセージと、再試行／トップに戻るボタン。retry は再試行で呼ぶ関数。"""
    kind = st.session_state.error_kind
    st.error(user_message(kind), icon="⚠️")
    if is_retryable(kind):
        if st.button("再試行する", type="primary", key="retry"):
            retry()
            st.rerun()
    elif st.button("トップに戻る", key="error_back"):
        reset()
        st.rerun()


# ---------------------------------------------------------------
# 画面01：トップ
# ---------------------------------------------------------------
def render_top() -> None:
    st.markdown(
        """
業務の担当者にAIが**業務改善コンサルタントとして質問**し、
業務の課題と改善案を整理します。

1. **業務情報を入力**（部署、業務名、困っていること）
2. **AIの質問に回答**（最大5問。1問ずつ）
3. **診断結果を確認**（課題・重要度・根拠と、最大3つの改善案）

課題は「事実」「推測」「要確認」に分けて表示し、根拠を確認できない内容は事実として扱いません。
改善案は、業務ルールの見直しなど**費用のかからない方法から**検討します。
"""
    )
    st.warning(
        f"**ご利用上の注意**　{NOTICE}　入力内容はAI（Anthropic社のClaude）に送信されます。"
        "診断結果は保存されないため、必要な場合は画面を印刷するなどして控えてください。",
        icon="🔒",
    )

    col_ai, col_demo = st.columns(2, gap="large")

    with col_ai:
        st.subheader("AI診断")
        if guard.ai_available():
            password = st.text_input("アクセス用パスワード", type="password", key="password")
            if st.button("診断を始める", type="primary", key="start_ai"):
                if not guard.check_password(password):
                    st.error("パスワードが正しくありません。")
                elif not guard.can_start_diagnosis(config.DB_PATH):
                    st.error("本日の診断回数の上限に達しました。デモモードをお試しください。")
                else:
                    start("ai")
                    st.rerun()
        else:
            st.info("AI診断は現在利用できません。デモモードをお試しください。")

    with col_demo:
        st.subheader("デモで試す")
        st.caption("APIキー不要。架空の業務で、画面の流れを確認できます。")
        scenarios = load_scenarios()
        titles = [sc["title"] for sc in scenarios]
        chosen = st.selectbox("シナリオ", titles, key="demo_scenario")
        if st.button("デモを始める", key="start_demo"):
            start("demo", scenarios[titles.index(chosen)])
            st.rerun()


# ---------------------------------------------------------------
# 画面02：業務情報入力（F01）
# ---------------------------------------------------------------
def render_input() -> None:
    s = st.session_state
    st.subheader("業務情報の入力")
    st.caption(f"🔒 {NOTICE}")

    fields = [
        ("company_name", "企業名（任意）", "text", "例：株式会社サンプル"),
        ("department", "部署名", "text", "例：営業管理部"),
        ("task_name", "業務名", "text", "例：月次売上集計"),
        ("overview", "業務概要", "area", "例：各営業所から届くExcelを1つにまとめ、月次の報告書を作成している"),
        ("pain_points", "現在困っていること", "area", "例：転記に時間がかかり、毎月どこかで数字が合わない"),
    ]

    values = {}
    with st.form("info_form"):
        for field, label, kind, placeholder in fields:
            widget = st.text_input if kind == "text" else st.text_area
            values[field] = widget(
                label,
                value=s.raw_input.get(field) or "",
                placeholder=placeholder,
                max_chars=config.FIELD_LIMITS[field],
                key=f"f_{field}",
            )
            if field in s.input_errors:
                st.error(s.input_errors[field])
        submitted = st.form_submit_button("ヒアリングを始める", type="primary", key="submit_info")

    if st.button("トップに戻る", key="input_back"):
        reset()
        st.rerun()

    if submitted:
        s.raw_input = values
        info, errors = validate_business_info(values)
        s.input_errors = errors
        if info is None:
            st.rerun()  # 項目ごとのエラーを表示し直す
        s.business_info = info
        s.hearing_state = start_hearing(FRAMEWORK)
        s.stage = "hearing"
        advance()  # 1回目の質問を作る
        st.rerun()


# ---------------------------------------------------------------
# 画面03：AIヒアリング（F02・F07）
# ---------------------------------------------------------------
def render_hearing() -> None:
    s = st.session_state
    state = s.hearing_state
    asked = len(state.turns)

    st.progress(asked / config.MAX_HEARING_TURNS, text=f"質問 {asked} / 最大 {config.MAX_HEARING_TURNS}")

    for turn in state.turns:
        with st.chat_message("assistant", avatar="🧑‍💼"):
            st.write(turn.question)
        if turn.answer is not None:
            with st.chat_message("user"):
                st.write(turn.answer)

    if s.error_kind:
        render_error(retry=advance)
        return

    current = state.turns[-1] if state.turns and state.turns[-1].answer is None else None
    if current:
        default = demo_answer(s.scenario, current.turn_no) if s.mode == "demo" else ""
        with st.form("answer_form", clear_on_submit=True):
            answer = st.text_area(
                "回答",
                value=default,
                placeholder=current.answer_example or "",
                help=current.answer_example,
                max_chars=config.ANSWER_MAX_CHARS,
                key=f"answer_{current.turn_no}",
            )
            if current.answer_example:
                st.caption(f"💡 {current.answer_example}")
            submitted = st.form_submit_button("回答する", type="primary", key="submit_answer")
        if s.answer_error:
            st.error(s.answer_error)

        if submitted:
            error = validate_answer(answer)
            s.answer_error = error
            if error is None:
                add_answer(state, answer)
                advance()
            st.rerun()

    st.divider()
    col_finish, col_cancel = st.columns(2)
    with col_finish:
        if st.button("ここまでの情報で診断する", key="finish", help="残りの質問をせずに、資料の添付に進みます"):
            finish_by_user(state)
            s.stage = "attach"
            st.rerun()
    with col_cancel:
        if st.button("診断を中止する", key="cancel", help="入力内容を消してトップに戻ります"):
            reset()
            st.rerun()


# ---------------------------------------------------------------
# 資料の添付（F09。任意）
# ---------------------------------------------------------------
def render_attach() -> None:
    s = st.session_state
    st.subheader("資料の添付（任意）")
    st.markdown(
        "業務で使っている集計表・記録・手順書などがあれば添付してください。"
        "AIが課題分析の根拠として使います。添付しなくても診断できます。"
    )
    st.caption(
        f"対応形式：CSV・テキスト・Excel（.xlsx）／{config.MAX_ATTACHMENTS}ファイルまで／"
        f"合計{config.MAX_ATTACHMENT_CHARS:,}文字まで"
    )
    st.warning(
        "個人情報（氏名・住所・電話番号など）や社外秘の情報を含むファイルは添付しないでください。"
        "必要な部分だけを残したファイルをおすすめします。添付ファイルは保存されません。",
        icon="🔒",
    )

    if s.error_kind:
        render_error(retry=diagnose)
        return

    if s.mode == "demo":
        # デモでは架空の見本ファイルを添付済みの状態で表示する（決定事項）
        attachments = demo_attachments(s.scenario)
        st.info("デモでは、架空の見本ファイルを添付済みです。")
        for a in attachments:
            with st.expander(f"📎 {a.name}"):
                st.text(a.text)
        if st.button("この資料で診断する", type="primary", key="diagnose"):
            s.attachments = attachments
            diagnose()
            st.rerun()
    else:
        files = st.file_uploader(
            "ファイルを選ぶ",
            type=[ext.lstrip(".") for ext in config.ATTACHMENT_EXTENSIONS],
            accept_multiple_files=True,
            key="uploader",
        )
        for message in s.attach_errors:
            st.error(message)

        col_with, col_without = st.columns(2)
        with col_with:
            if st.button("添付して診断する", type="primary", key="diagnose", disabled=not files):
                attachments, errors = load_attachments([(f.name, f.getvalue()) for f in files])
                s.attach_errors = errors
                if not errors:
                    s.attachments = attachments
                    diagnose()
                st.rerun()
        with col_without:
            if st.button("添付せずに診断する", key="diagnose_without"):
                s.attachments, s.attach_errors = [], []
                diagnose()
                st.rerun()

    st.divider()
    if st.button("診断を中止する", key="cancel", help="入力内容を消してトップに戻ります"):
        reset()
        st.rerun()


# ---------------------------------------------------------------
# 画面04：診断結果（F03・F04）
# ---------------------------------------------------------------
def evidence_source(ev, attachment_names: list[str]) -> str:
    """根拠の出どころの表示（例：「2問目の回答」「添付ファイル『記録.csv』」）。"""
    if ev.source == "回答":
        return f"{ev.turn_no}問目の回答"
    if ev.source == "添付ファイル":
        valid = ev.file_no is not None and 1 <= ev.file_no <= len(attachment_names)
        return f"添付ファイル『{attachment_names[ev.file_no - 1]}』" if valid else "添付ファイル"
    return "業務情報"


def render_result() -> None:
    s = st.session_state
    result = s.diagnosis
    analysis = result.analysis
    info = s.business_info

    st.subheader("業務の概要")
    st.markdown(f"**{info.department}／{info.task_name}**")
    st.write(analysis.business_summary)
    st.caption(s.hearing_state.finished_reason)
    if result.attachment_names:
        st.caption("診断に使った資料：" + "、".join(f"📎 {n}" for n in result.attachment_names))

    if result.missing_item_ids:
        names = "、".join(ITEM_NAMES[i] for i in result.missing_item_ids)
        st.warning(f"次の項目は情報が不足しています。結果は参考としてご覧ください：{names}", icon="📝")

    # --- 課題（F03） ---
    st.subheader(f"抽出された課題（{len(analysis.issues)}件）")
    if not analysis.issues:
        st.info("お聞きした範囲では、明確な課題は確認できませんでした。")

    for issue in analysis.issues:
        with st.container(border=True):
            st.markdown(f"#### {issue.issue_id}. {issue.title}")
            viewpoints = "・".join(VIEWPOINT_NAMES.get(v, v) for v in issue.viewpoint_ids)
            st.markdown(f"{SEVERITY_LABEL[issue.severity]}　{EVIDENCE_LABEL[issue.evidence_type]}　:gray[観点：{viewpoints}]")
            if issue.issue_id in result.downgraded_issue_ids:
                st.caption("⚠️ AIは「事実」と判断しましたが、根拠となる発言を入力内容の中に確認できなかったため「要確認」としています。")
            st.write(issue.summary)

            st.markdown("**判断根拠（ご本人の発言・添付資料）**")
            if issue.evidences:
                for ev in issue.evidences:
                    where = evidence_source(ev, result.attachment_names)
                    st.markdown(f"> {ev.quote}　:gray[（{where}）]")
            else:
                st.caption("根拠となる発言はありません")

            st.markdown(f"**想定原因（推測）**：{issue.assumed_cause}")
            st.markdown(f"**業務への影響**：{issue.impact}")
            st.markdown(f"**重要度の理由**：{issue.severity_reason}")
            if issue.followup_questions:
                st.markdown("**追加で確認したいこと**")
                for q in issue.followup_questions:
                    st.markdown(f"- {q}")

    if analysis.no_issue_viewpoints:
        names = "、".join(VIEWPOINT_NAMES.get(v, v) for v in analysis.no_issue_viewpoints)
        st.caption(f"問題が確認できなかった観点：{names}")

    # --- 改善案（F04） ---
    st.subheader("改善案")
    if result.proposals is None:
        st.info("課題が確認できなかったため、改善案は作成していません。")
    else:
        for n, p in enumerate(result.proposals.proposals, start=1):
            with st.container(border=True):
                st.markdown(f"#### 案{n}. {p.title}")
                targets = "、".join(p.target_issue_ids)
                st.markdown(f"`{p.category}`　**導入難易度：{p.difficulty}**　:gray[対応する課題：{targets}]")
                st.write(p.description)
                st.markdown(f"**推奨理由**：{p.reason}")
                if p.why_not_simpler:
                    st.markdown(f"**より簡単な方法では不十分な理由**：{p.why_not_simpler}")
                st.markdown(f"**期待できる効果（想定）**：{p.expected_effect}")
                if p.risks:
                    st.markdown("**想定リスク**")
                    for r in p.risks:
                        st.markdown(f"- {r}")
                if p.prerequisites:
                    st.markdown("**実施前に確認すべき事項**")
                    for r in p.prerequisites:
                        st.markdown(f"- {r}")

    st.caption("※ 診断結果はAIによる参考情報です。効果は想定であり、実施前に関係者と事実を確認してください。")
    if st.button("新しい診断を始める", type="primary", key="new"):
        reset()
        st.rerun()


# ---------------------------------------------------------------
# メイン
# ---------------------------------------------------------------
def main() -> None:
    init_state()
    render_header()
    {
        "top": render_top,
        "input": render_input,
        "hearing": render_hearing,
        "attach": render_attach,
        "result": render_result,
    }[st.session_state.stage]()


main()
