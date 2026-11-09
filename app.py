"""AI-BIC の画面（Streamlit）。起動：streamlit run app.py

画面は表示と入力の受け取りだけを行い、業務ロジックは ai_bic パッケージに任せる（方針P3）。
1ページの中で「トップ → 業務情報 → ヒアリング → 資料の添付 → 診断結果」を切り替える（決定事項B1）。
見た目の部品（配色・書体・カード）は ai_bic/ui.py にまとめている。

Streamlit は操作のたびにこのファイルを最初から実行し直す。
AIの呼び出しは advance()（質問）と diagnose()（分析・提案）の2か所にまとめ、
ボタンが押されたときだけ呼ぶ（二重呼び出しの防止 K04）。
"""

import uuid
from datetime import datetime

import streamlit as st

from ai_bic import config, effect, guard, report, ui
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
ITEMS = {item.id: item for item in FRAMEWORK.hearing_items}
VIEWPOINT_NAMES = {v.id: v.name for v in FRAMEWORK.viewpoints}

NOTICE = "個人情報や社外秘の情報は入力しないでください。企業名は空欄または架空の名前をおすすめします。"
STEPS = [
    ("input", "01 業務情報"),
    ("hearing", "02 ヒアリング"),
    ("attach", "03 資料の添付"),
    ("result", "04 診断結果"),
]


def html(text: str) -> None:
    """ui.py の部品（HTML）を表示する。中の文字は ui.esc() でエスケープ済み。"""
    st.markdown(text, unsafe_allow_html=True)


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
        "analysis_cache": {},  # 成功した課題分析（再試行で同じ分析をやり直さないため。X01）
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


def start(mode: str, scenario: dict | None = None, session_id: str | None = None) -> None:
    """診断を始める。AI診断は、枠を確保したときの session_id をそのまま使う。"""
    reset()
    s = st.session_state
    s.mode = mode
    s.scenario = scenario
    s.session_id = session_id or uuid.uuid4().hex
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
                get_client(), s.business_info, s.hearing_state, FRAMEWORK, s.attachments,
                cache=s.analysis_cache,
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
    html(ui.CSS)
    html(ui.topbar(STEPS, s.stage))
    if s.mode == "demo":
        html(ui.demo_banner())


def render_error(retry) -> None:
    """AIエラーのメッセージと、再試行／トップに戻るボタン。retry は再試行で呼ぶ関数。"""
    kind = st.session_state.error_kind
    st.error(user_message(kind))
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
    html(ui.hero())
    html(ui.features())
    html(
        ui.note(
            f"ご利用上の注意　{NOTICE}　入力内容はAI（Anthropic社のClaude）に送信されます。"
            "診断結果は保存されないため、必要な場合は結果画面からExcelのレポートをダウンロードして控えてください。"
        )
    )

    col_ai, col_demo = st.columns(2, gap="large")

    with col_ai:
        html(ui.label("AI Diagnosis", "AI診断", tight=True))
        if guard.ai_available():
            password = st.text_input("アクセス用パスワード", type="password", key="password")
            if st.button("診断を始める", type="primary", key="start_ai"):
                if not guard.check_password(password):
                    st.error("パスワードが正しくありません。")
                elif not guard.reserve_diagnosis(config.DB_PATH, session_id := uuid.uuid4().hex):
                    st.error("本日の診断回数の上限に達しました。デモモードをお試しください。")
                else:
                    start("ai", session_id=session_id)
                    st.rerun()
        else:
            html(ui.note("AI診断は現在利用できません。デモモードをお試しください。"))

    with col_demo:
        html(ui.label("Demo", "デモで試す", tight=True))
        scenarios = load_scenarios()
        titles = [sc["title"] for sc in scenarios]
        chosen = st.selectbox("架空の業務シナリオ（APIキー不要）", titles, key="demo_scenario")
        if st.button("デモを始める", key="start_demo"):
            start("demo", scenarios[titles.index(chosen)])
            st.rerun()


# ---------------------------------------------------------------
# 画面02：業務情報入力（F01）
# ---------------------------------------------------------------
def render_input() -> None:
    s = st.session_state
    html(ui.label("Step 01", "業務情報の入力"))
    html(ui.note(NOTICE))

    fields = [
        ("company_name", "企業名（任意）", "text", "例：株式会社サンプル"),
        ("department", "部署名", "text", "例：営業管理部"),
        ("task_name", "業務名", "text", "例：月次売上集計"),
        ("overview", "業務概要", "area", "例：各営業所から届くExcelを1つにまとめ、月次の報告書を作成している"),
        ("pain_points", "現在困っていること", "area", "例：転記に時間がかかり、毎月どこかで数字が合わない"),
    ]

    values = {}
    with st.form("info_form", border=False):
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

    html(ui.label("Step 02", "ヒアリング"))
    st.progress(asked / config.MAX_HEARING_TURNS, text=f"質問 {asked} / 最大 {config.MAX_HEARING_TURNS}")

    html("".join(ui.qa(turn) for turn in state.turns))

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
                max_chars=config.ANSWER_MAX_CHARS,
                key=f"answer_{current.turn_no}",
            )
            if current.answer_example:
                st.caption(f"回答の例　{current.answer_example}")
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
    html(ui.label("Step 03", "資料の添付（任意）"))
    st.markdown(
        "業務で使っている集計表・記録・手順書などがあれば添付してください。"
        "AIが課題分析の根拠として使います。添付しなくても診断できます。"
    )
    html(
        ui.note(
            f"対応形式：CSV・テキスト・Excel（.xlsx）／{config.MAX_ATTACHMENTS}ファイルまで／"
            f"合計{config.MAX_ATTACHMENT_CHARS:,}文字まで。"
            "個人情報（氏名・住所・電話番号など）や社外秘の情報を含むファイルは添付しないでください。"
            "添付ファイルは保存されません。"
        )
    )

    if s.error_kind:
        render_error(retry=diagnose)
        return

    if s.mode == "demo":
        # デモでは架空の見本ファイルを添付済みの状態で表示する（決定事項）
        attachments = demo_attachments(s.scenario)
        st.caption("デモでは、架空の見本ファイルを添付済みです。")
        for a in attachments:
            with st.expander(f"添付ファイル　{a.name}"):
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

    if st.button("診断を中止する", key="cancel", help="入力内容を消してトップに戻ります"):
        reset()
        st.rerun()


# ---------------------------------------------------------------
# 画面04：診断結果（F03・F04）
# ---------------------------------------------------------------
def render_report_download(data: bytes, name: str, key: str) -> None:
    """診断レポート（Excel）のダウンロードボタン（B05）。結果の冒頭と末尾の2か所に置く。"""
    st.download_button(
        "診断レポートをダウンロード（Excel）",
        data=data,
        file_name=name,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key=key,
    )


# 効果の試算の入力欄（B06）：画面の状態の名前 → (入力欄の名前, 1回の増減, 最大値, 表示形式)
EFFECT_INPUTS = {
    "effect_current": (effect.LABEL_CURRENT, 1.0, None, "%.1f"),
    "effect_rate": (effect.LABEL_RATE, 5.0, 100.0, "%.0f"),
    "effect_hourly": (effect.LABEL_HOURLY, 100.0, None, "%.0f"),
    "effect_setup": (effect.LABEL_SETUP, 1.0, None, "%.1f"),
    "effect_running": (effect.LABEL_RUNNING, 1000.0, None, "%.0f"),
}


def effect_assumptions(analysis) -> effect.EffectAssumptions:
    """画面の入力欄の値から、試算の仮定を作る。

    初めて結果を表示したときだけ、現状の作業時間に、課題分析の計算結果を初期値として入れる。
    ほかの仮定は空欄のまま（根拠のない数値を最初から入れない。開発者の判断）。
    """
    s = st.session_state
    s.setdefault("effect_current", effect.default_current_hours(analysis)[0])
    for key in EFFECT_INPUTS:
        s.setdefault(key, None)
    return effect.EffectAssumptions(
        current_hours=s.effect_current, reduction_rate=s.effect_rate, hourly_cost=s.effect_hourly,
        setup_hours=s.effect_setup, running_cost=s.effect_running,
    )


def render_effect(analysis) -> None:
    """効果の試算（B06）。現状の作業時間をそのまま削減効果にせず、利用者が置いた仮定から計算する。"""
    html(ui.label("Estimate", "効果の試算"))
    st.markdown(
        "現状の作業時間が、そのまま削減できるわけではありません。"
        "削減できる割合などの仮定を入れると、効果を**プログラムで計算**します（AIは計算していません）。"
        "仮定を変えると、試算はすぐに更新されます。"
    )
    assumptions = effect_assumptions(analysis)
    _, source = effect.default_current_hours(analysis)
    if source:
        html(ui.footnote(f"現状の作業時間の初期値は、{source} から入れています。月あたりの時間か確かめてください。"))
    else:
        html(ui.footnote("診断結果から月の作業時間を計算できなかったため、現状の作業時間も入力してください。"))

    items = list(EFFECT_INPUTS.items())
    for no, (key, (name, step, max_value, fmt)) in enumerate(items):
        if no % 3 == 0:  # 1行に3つまで（狭い画面で入力欄の名前が切れないように）
            columns = st.columns(3)
        with columns[no % 3]:
            st.number_input(
                name, min_value=0.0, max_value=max_value, step=step, format=fmt,
                key=key, placeholder="未入力",
            )
    html(ui.effect_card(effect.estimate(assumptions)))
    html(ui.footnote("試算は、入力した仮定による想定です。実測の結果ではありません。"))


def render_result() -> None:
    s = st.session_state
    result = s.diagnosis
    analysis = result.analysis
    info = s.business_info
    issues_by_id = {i.issue_id: i for i in analysis.issues}

    # 診断レポート（B05）。その場で作って渡すだけで、サーバーには保存しない
    created_at = datetime.now(config.JST)
    report_data = report.build_report(
        info, s.hearing_state, result, FRAMEWORK, created_at, demo=s.mode == "demo"
    )
    report_name = report.file_name(created_at)

    # --- 診断サマリー（最初の数秒で価値が伝わるように） ---
    html(ui.label("Summary", "診断サマリー"))
    if result.proposals:
        top = issues_by_id.get(result.proposals.summary.top_issue_id)
        html(
            ui.summary_card(
                result.proposals.summary, top, analysis.issues, len(result.proposals.proposals)
            )
        )
    else:
        html(ui.note("お聞きした範囲では、明確な課題は確認できませんでした。"))
    render_report_download(report_data, report_name, key="download_report_top")

    # --- 業務の概要 ---
    html(ui.label("Overview", "業務の概要"))
    html(f'<span class="bic-tag">{ui.esc(info.department)}</span><span class="bic-tag">{ui.esc(info.task_name)}</span>')
    st.write(analysis.business_summary)
    meta = [s.hearing_state.finished_reason]
    if result.attachment_names:
        meta.append("診断に使った資料：" + "、".join(result.attachment_names))
    html(ui.footnote("　／　".join(meta)))
    if result.missing_item_ids:
        html(ui.note("次の項目は情報が不足しています。結果は参考としてご覧ください。"))
        for item_id in result.missing_item_ids:
            html(ui.footnote(ui.missing_item_text(ITEMS[item_id], s.hearing_state.item_status.get(item_id))))

    # --- 課題（F03） ---
    html(ui.label("Issues", f"抽出された課題（{len(analysis.issues)}件）"))
    for no, issue in enumerate(analysis.issues, start=1):
        viewpoints = "観点：" + "・".join(VIEWPOINT_NAMES.get(v, v) for v in issue.viewpoint_ids)
        sources = [ui.evidence_source(ev, result.attachment_names) for ev in issue.evidences]
        downgraded = result.downgraded.get(issue.issue_id)
        html(ui.issue_card(no, issue, viewpoints, sources, downgraded))
    if analysis.no_issue_viewpoints:
        names = "、".join(VIEWPOINT_NAMES.get(v, v) for v in analysis.no_issue_viewpoints)
        html(ui.footnote(f"問題が確認できなかった観点：{names}"))

    # --- 改善案（F04） ---
    html(ui.label("Proposals", "改善案"))
    if result.proposals is None:
        html(ui.note("課題が確認できなかったため、改善案は作成していません。"))
    else:
        for no, p in enumerate(result.proposals.proposals, start=1):
            html(ui.proposal_card(no, p))
        render_effect(analysis)

    html(ui.footnote(ui.DISCLAIMER))
    render_report_download(report_data, report_name, key="download_report_bottom")
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
