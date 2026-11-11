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

from ai_bic import config, facts, guard, overview, readiness, report, ui
from ai_bic.ai_client import ClaudeClient
from ai_bic.project import ProjectFileError, load_project, new_project, to_json_bytes
from ai_bic.project import file_name as project_file_name
from ai_bic.views import flows, result_top, review, simulator
from ai_bic.views import progress as progress_view
from ai_bic.views import trial as trial_view
from ai_bic.attachments import load_attachments
from ai_bic.demo_client import (
    DemoClient,
    answered_all_basic,
    demo_answer,
    demo_attachments,
    load_scenarios,
    sample_hearing,
)
from ai_bic.errors import AIError, is_retryable, user_message
from ai_bic.hearing import (
    add_answer,
    finish_by_user,
    judge_last_answers,
    request_extra_question,
    request_next_question,
    run_diagnosis,
    start_extra,
    start_hearing,
)
from ai_bic.validation import validate_answer, validate_business_info

st.set_page_config(page_title=config.APP_PAGE_TITLE, page_icon=str(config.APP_FAVICON), layout="wide")  # 幅は ui.CSS で抑え、結果画面だけ広げる

FRAMEWORK = config.load_framework("general")
ITEMS = {item.id: item for item in FRAMEWORK.hearing_items}
VIEWPOINT_NAMES = {v.id: v.name for v in FRAMEWORK.viewpoints}

NOTICE = "個人情報や社外秘の情報は入力しないでください。企業名は空欄または架空の名前をおすすめします。"
UNKNOWN_ANSWER = "分かりません。"  # 「分からない」ボタンの回答（item_rules.is_unknown_answer で、それ以上聞かない項目になる）
PLANNED_ANSWER = "あとで確認する予定です。"  # 「あとで確認する」ボタンで、回答欄が空のときに残す回答（確認予定）
HEARING_CARD = ".st-key-hearing_card"  # ヒアリングの現在の質問カード（画面を動かす先）
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
        "project": None,  # 診断後の案件データ（試算・業務フロー・レビュー。AI-BIC 2.0）
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
    if s.get("loaded"):
        # 読み込んだAI診断の案件では、AIを使わない（保存ファイルの回数の記録は書き換えられるため、
        # 費用の上限を守れない）。画面のボタンも出さないが、ここでも止める
        raise AIError("limit", "loaded project")
    return ClaudeClient(
        api_key=config.get_secret(config.SECRET_API_KEY),
        session_id=s.session_id,
        db_path=config.DB_PATH,
    )


def advance(spinner: str = "次の質問を準備しています…") -> None:
    """ヒアリングを1歩進める（次の質問を作る）。

    基本の質問が終わったら、3機能に必要な情報がそろっているかを判定し（readiness.py）、
    足りなければ「追加確認」の画面へ、そろっていれば「資料の添付」に進む。
    追加確認の間は、追加の質問を作る。エラーが起きたら種類を記録し、再試行ボタンから再度呼ぶ。
    """
    s = st.session_state
    s.error_kind = None
    state = s.hearing_state
    try:
        with st.spinner(spinner):
            client = get_client()
            if state.extra_started:
                turn = request_extra_question(client, s.business_info, state, FRAMEWORK)
            else:
                turn = request_next_question(client, s.business_info, state, FRAMEWORK)
                if turn is None:
                    # 最後の回答も充足状況に反映してから、情報がそろっているかを判定する
                    judge_last_answers(client, s.business_info, state, FRAMEWORK)
    except AIError as e:
        s.error_kind = e.kind
        return
    if turn is None:
        offer = not state.extra_started and readiness.should_offer_extra(state, FRAMEWORK)
        s.stage = "readiness" if offer else "attach"


def diagnose() -> None:
    """課題分析と改善提案を行い、結果画面に進む。添付ファイルは課題分析にだけ使う。"""
    s = st.session_state
    s.error_kind = None
    # デモでヒアリングを途中で終えた場合：結果は全問回答を前提にした固定の内容のため、
    # 根拠もサンプル回答（全問）と照合する。途中までの入力は、判定に使わず別に持つ（AI-BIC 3.0）
    partial = None
    if s.mode == "demo" and not answered_all_basic(s.scenario, s.hearing_state):
        partial = s.hearing_state
        s.business_info, s.hearing_state = sample_hearing(s.scenario, FRAMEWORK)
    try:
        with st.spinner("AIが課題を分析し、改善案を考えています…（1分ほどかかることがあります）"):
            s.diagnosis = run_diagnosis(
                get_client(), s.business_info, s.hearing_state, FRAMEWORK, s.attachments,
                cache=s.analysis_cache,
            )
    except AIError as e:
        s.error_kind = e.kind
        return
    # 結果画面以降（試算・業務フロー・レビュー）は、案件データにまとめて持つ（AI-BIC 2.0）
    s.project = new_project(
        s.mode, s.business_info, s.hearing_state, s.diagnosis,
        demo_scenario_id=s.scenario["id"] if s.mode == "demo" else None,
    )
    s.project.partial_hearing = partial
    s.stage = "result"


# ---------------------------------------------------------------
# 共通の表示
# ---------------------------------------------------------------
def render_header() -> None:
    s = st.session_state
    html(ui.CSS)
    html(ui.topbar(STEPS, "hearing" if s.stage == "readiness" else s.stage))  # 追加確認はヒアリングの一部
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
    # トップだけ横幅を広げる。説明文・カード・注意事項の折り返しが減り、文字を小さくせずに
    # 「デモを始める」までを1画面に収められる（開発者の指摘）
    html("<style>.block-container, [data-testid='stMainBlockContainer'] { max-width: 1100px; }"
         " .bic-label.tight { margin-top: .35rem; } .bic-h2.tight { margin-bottom: .25rem; }</style>")
    html(ui.hero())
    html(ui.features())
    html(
        ui.notice(
            "ご利用上の注意",
            "個人情報や社外秘の情報は入力しないでください（企業名は空欄か架空の名前を推奨）。"
            "入力内容はAI（Anthropic社のClaude）に送信され、診断結果は保存されません。"
            "必要な場合は結果画面からExcelのレポートをダウンロードして控えてください。"
        )
    )

    # 2つの入口を、それぞれ1枚のカードにする（AI診断＝濃紺、デモで試す＝淡い金色）。
    # 入れ物の key がクラス名（st-key-…）になり、ui.CSS でカードの中だけ色を上書きする
    col_ai, col_demo = st.columns(2, gap="medium")

    with col_ai, st.container(key="entry_ai"):
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

    with col_demo, st.container(key="entry_demo"):
        html(ui.label("Demo", "デモで試す", tight=True))
        scenarios = load_scenarios()
        titles = [sc["title"] for sc in scenarios]
        chosen = st.selectbox("架空の業務シナリオ（APIキー不要）", titles, key="demo_scenario")
        if st.button("デモを始める", key="start_demo"):
            start("demo", scenarios[titles.index(chosen)])
            st.rerun()

    # 保存した案件の読み込み：2枚のカードの下に、控えめな白い横長の開閉行として置く
    with st.container(key="entry_resume"), st.expander("保存した案件を読み込んで再開する"):
        st.caption("結果画面の「案件を保存（JSON）」で保存したファイルを読み込みます。"
                   "AI診断の案件は、費用を管理するため、読み込んだあとはAIを使う操作（業務フローの作成・レビュー・作り直し）"
                   "ができません。表の編集・試算・指摘の採否・確定はできます。")
        uploaded = st.file_uploader("案件ファイル（.json）", type=["json"], key="project_file")
        if st.button("読み込んで再開する", key="load_project", disabled=uploaded is None):
            resume(uploaded.getvalue())
        if error := st.session_state.get("load_error"):
            st.error(error)


def resume(data: bytes) -> None:
    """保存した案件を読み込み、結果画面から再開する。読めなければ理由を表示する（中身は表示しない）。"""
    try:
        project = load_project(data)
        scenario = None
        if project.mode == "demo":
            scenario = next((sc for sc in load_scenarios() if sc["id"] == project.demo_scenario_id), None)
            if scenario is None:
                raise ProjectFileError("このデモのシナリオは、今のアプリにはありません")
    except ProjectFileError as e:
        st.session_state["load_error"] = str(e)
        st.rerun()
    reset()  # 前の診断の内容と入力欄の状態を消す
    s = st.session_state
    s.mode, s.scenario, s.session_id = project.mode, scenario, uuid.uuid4().hex
    s.business_info, s.hearing_state, s.diagnosis = project.business_info, project.hearing, project.diagnosis
    s.project, s.loaded, s.stage = project, project.mode == "ai", "result"
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
    """ヒアリング画面（AI-BIC 3.1）：現在の質問を1枚のカードにまとめ、これまでの回答は折りたたむ。

    カードの中：質問番号・進み具合 → 質問文 → 回答欄 → 回答例 → 操作ボタン。
    回答を送ったら、ボタンを消して「次の質問を準備しています」と表示してからAIを呼ぶ（二重送信を防ぐ）。
    失敗しても回答は記録に残り、再試行できる。
    """
    s = st.session_state
    state = s.hearing_state
    html(ui.label("Step 02", "より正確な分析のための追加確認" if state.extra_started else "ヒアリング"))

    current = state.turns[-1] if state.turns and state.turns[-1].answer is None else None
    preparing = s.get("hearing_pending") or (current is None and not s.error_kind)
    with st.container(key="hearing_card"):
        html(f'<div class="bic-qhead">{ui.esc(_progress_text(state, current))}</div>')
        st.progress(_progress_value(state))
        if s.error_kind:
            render_error(retry=_retry_question)
        elif preparing:
            # 回答を受け取ったあと：ボタンを出さずにAIを呼ぶ（同じ回答を2回送れないようにする）
            st.markdown('<div class="bic-preparing">次の質問を準備しています…</div>', unsafe_allow_html=True)
            s.hearing_pending = False
            advance(spinner="次の質問を準備しています…")
            result_top.request_scroll(HEARING_CARD)
            st.rerun()
        else:
            _render_question(current)

    # 途中で終える・中止する操作は、質問カードの外に置く
    col_finish, col_cancel = st.columns(2)
    with col_finish:
        if st.button("ヒアリングを終了して資料添付へ", key="finish", help="残りの質問をせずに、資料の添付に進みます"):
            finish_by_user(state)
            s.stage = "attach"
            st.rerun()
        st.caption("資料の添付は任意です。未確認の項目は結果画面に表示します。")
    with col_cancel:
        if st.button("診断を中止する", key="cancel", help="入力内容を消してトップに戻ります"):
            reset()
            st.rerun()

    answered = [turn for turn in state.turns if turn.answer is not None]
    if answered:
        with st.expander(f"これまでの回答（{len(answered)}件）", expanded=False):
            html("".join(ui.qa(turn) for turn in answered))
    result_top.render_scroll()  # 次の質問に進んだら、質問カードの先頭が見える位置へ


def _progress_text(state, current) -> str:
    if state.extra_started:
        n = readiness.extra_count(state)
        return f"追加確認 {n} / 最大{config.MAX_EXTRA_QUESTIONS}問（必要な情報がそろえば早く終わります）"
    n = len(state.turns)
    return f"質問 {n} / 最大{config.MAX_HEARING_TURNS}問（必要な情報がそろえば早く終わります）"


def _progress_value(state) -> float:
    if state.extra_started:
        return min(readiness.extra_count(state) / config.MAX_EXTRA_QUESTIONS, 1.0)
    return min(len(state.turns) / config.MAX_HEARING_TURNS, 1.0)


def _next_label(state) -> str:
    """主ボタンの文言。送ったあと資料の添付に進むことが決まっているときだけ「資料添付へ」にする。"""
    last_extra = state.extra_started and readiness.extra_count(state) >= config.MAX_EXTRA_QUESTIONS
    return "回答して資料添付へ" if last_extra else "回答して次へ"


def _render_question(current) -> None:
    s = st.session_state
    state = s.hearing_state
    html(f'<div class="bic-question">{ui.esc(current.question)}</div>')
    if current.extra:
        if current.decides:
            html(ui.note(f"この回答で判断できること　{current.decides}"))
        st.caption(f"確認する理由：{readiness.reason_for(state, FRAMEWORK, current.item_id)}")
    key = f"answer_{current.turn_no}"
    if s.mode == "demo":
        s.setdefault(key, demo_answer(s.scenario, current.turn_no, current.question))
    with st.form("answer_form", border=False):
        # 回答例は入力欄の下に1回だけ出す（入力欄の中の薄い文字とは重ねない）
        st.text_area("回答", key=key, max_chars=config.ANSWER_MAX_CHARS, height=110)
        if current.answer_example:
            st.caption(f"回答例　{current.answer_example}")
        c1, c2, c3, _ = st.columns([2.2, 1.4, 1.8, 1.6])
        c1.form_submit_button(_next_label(state), type="primary", key="submit_answer", width="stretch",
                              on_click=_submit_answer, args=(key, "回答"))
        # 分からない項目は、それ以上質問しない（item_rules.is_unknown_answer で判定される回答を入れる）
        c2.form_submit_button("分からない", key="submit_unknown", width="stretch",
                              help="この点は分からないと回答し、次に進みます（未確認の前提として結果に示します）",
                              on_click=_submit_answer, args=(key, "分からない"))
        # あとで確かめる項目も聞き直さない。回答欄に予定を書けば、それを残す（AI-BIC 3.2）
        c3.form_submit_button("あとで確認する", key="submit_planned", width="stretch",
                              help="今は分からないが、あとで確かめる予定です（例：1か月記録して確かめる）。回答欄に予定を書けば残します",
                              on_click=_submit_answer, args=(key, "確認予定"))
    if s.answer_error:
        st.error(s.answer_error)


def _submit_answer(key: str, kind: str) -> None:
    """回答を記録し、次の表示で質問を準備する（ここではAIを呼ばない）。kind：回答・分からない・確認予定"""
    s = st.session_state
    state = s.hearing_state
    if not state.turns or state.turns[-1].answer is not None:
        return  # すでに送った回答（二重送信）は無視する
    typed = (s.get(key) or "").strip()
    if kind == "分からない":
        answer, error = UNKNOWN_ANSWER, None
    elif kind == "確認予定":
        answer, error = (typed or PLANNED_ANSWER), None
    else:
        answer, error = typed, validate_answer(typed)
    s.answer_error = error
    if error is None:
        add_answer(state, answer, None if kind == "回答" else kind)
        s.hearing_pending = True


def _retry_question() -> None:
    advance(spinner="次の質問を準備しています…")
    result_top.request_scroll(HEARING_CARD)


# ---------------------------------------------------------------
# 追加確認の提案（AI-BIC 2.0）：基本の質問のあと、3機能に必要な情報が足りないときだけ表示する
# ---------------------------------------------------------------
def render_readiness() -> None:
    s = st.session_state
    state = s.hearing_state
    html(ui.label("Check", "より正確な分析のための追加確認"))
    st.markdown("基本のヒアリングが終わりました。分析の3つの機能に必要な情報がそろっているかを、"
                "回答の内容と決まった基準で確認しました（AIではなくプログラムで判定しています）。")
    names = {item.id: item.name for item in FRAMEWORK.hearing_items}
    rows = [
        {"機能": f.name, "判定": readiness.LEVEL_MARK[f.level],
         "足りない情報": "、".join(names[i] for i in f.missing_required + f.missing_helpful) or "—"}
        for f in readiness.evaluate(state)
    ]
    st.dataframe(rows, hide_index=True, width="stretch")
    st.markdown("**追加で確認したいこと**")
    st.markdown("\n".join(f"- {ui.esc(r)}" for r in readiness.reasons(state, FRAMEWORK)))
    st.caption(f"追加の質問は最大{config.MAX_EXTRA_QUESTIONS}問です。分からない点は「分からない」と答えられます。"
               "このまま分析することもでき、その場合は結果に未確認事項と仮定条件を表示します。")
    c1, c2 = st.columns(2)
    if c1.button(f"追加で確認する（最大{config.MAX_EXTRA_QUESTIONS}問）", type="primary", key="extra_start"):
        start_extra(state, FRAMEWORK)
        s.stage = "hearing"
        advance()
        st.rerun()
    if c2.button("このまま分析する", key="extra_skip"):
        s.stage = "attach"
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


def render_result() -> None:
    s = st.session_state
    # 結果画面だけ横幅を広げる（業務フロー図を狭いカードに押し込まないため。指示書 7.2）
    html("<style>.block-container { max-width: 1200px; }</style>")
    result = s.diagnosis
    info = s.business_info
    result_top.keep_inputs()  # タブを切り替えても、入力途中の値を消さない（AI-BIC 3.2）

    # 診断レポート（B05）。その場で作って渡すだけで、サーバーには保存しない
    created_at = datetime.now(config.JST)
    # デモを途中で終えた場合は、全問回答のサンプルによる結果（AI-BIC 3.0）。何と照合したかを、画面とレポートで示す
    partial = s.project.partial_hearing
    answered = sum(1 for t in partial.turns if t.answer is not None) if partial else None
    report_data = report.build_report(
        info, s.hearing_state, result, FRAMEWORK, created_at, demo=s.mode == "demo", sample_of=answered,
        project=s.project,
    )
    report_name = report.file_name(created_at)

    # 試算の初期値は下の試算の画面で入るため、先に入れてから状態を判定する（入れる前だと「未着手」と誤って表示した）
    if result.proposals is not None:
        simulator.ensure_inputs(s.project)

    # --- 1. 結論と次の操作（最大の課題・最初にやること・次の判断に必要な情報 → 主ボタン。AI-BIC 3.0） ---
    if partial:
        html(ui.sample_banner(answered, len(s.hearing_state.turns)))
    ov = overview.build(result, s.hearing_state, FRAMEWORK, s.project.gap_answers, facts.numbers_summary(s.project),
                        facts.needs(s.project))
    result_top.render_decision(ov, has_proposals=result.proposals is not None)
    if s.get("loaded"):
        html(ui.note("読み込んだAI診断の案件です。費用を管理するため、AIを使う操作はできません。"))
    # 6ステップの進み具合と案ごとの状態は、上部の4ステップと競わないよう、開いたときだけ表示する
    with st.expander("作業状況を見る"):
        progress_view.render(s.project)
    c1, c2 = st.columns(2)
    with c1:
        render_report_download(report_data, report_name, key="download_report_top")
    # 案件の保存（AI-BIC 2.0 9.1）。サーバーには保存しないため、利用者がファイルで持ち帰って再開する
    c2.download_button(
        "案件を保存（JSON）", data=to_json_bytes(s.project), file_name=project_file_name(s.project),
        mime="application/json", key="download_project",
        help="TOP画面の「保存した案件を読み込んで再開する」で、続きから作業できます。入力した内容が含まれるため、取り扱いに注意してください。",
    )

    # --- 2. 作業目的ごとの4つのタブ（AI-BIC 3.2）。選んでいないタブの中身は描かない（タブを開いてもAIは動かない） ---
    tabs = result_top.render_tabs()
    with tabs["choose"]:
        if tabs["choose"].open:
            if result.proposals is None:
                html(ui.label("Proposals", "改善案", first=True))
                html(ui.note("課題が確認できなかったため、改善案は作成していません。"))
            else:
                result_top.render_compare(s.project)
                # 作業領域：業務フロー・試算・レビュー（と確定）のうち、選んだ1つだけをその場に出す（AI-BIC 3.2）
                area = result_top.work_area()
                if area != "brief":
                    {"flows": lambda: flows.render(s.project, get_client), "sim": lambda: simulator.render(s.project),
                     "review": lambda: review.render(s.project, get_client)}[area]()
                    result_top.render_back()
    with tabs["gaps"]:
        if tabs["gaps"].open:
            quantity_lines = facts.assumption_lines(s.project) if result.analysis.quantities else None
            assumptions = overview.open_assumptions(s.hearing_state, s.project.gap_answers, quantity_lines)
            result_top.render_gaps(s.project, ov, assumptions, get_client)
    with tabs["evidence"]:
        if tabs["evidence"].open:
            render_evidence(partial, answered)
    with tabs["trial"]:
        if tabs["trial"].open:
            trial_view.render(s.project)

    html(ui.footnote(ui.DISCLAIMER))
    render_report_download(report_data, report_name, key="download_report_bottom")
    if st.button("新しい診断を始める", type="primary", key="new"):
        reset()
        st.rerun()
    result_top.render_scroll()  # ボタンで頼まれた見出しまで画面を動かす（ページの最後に置く）


def render_evidence(partial, answered) -> None:
    """診断の根拠を見る：業務の概要・分かっている数値・課題（引用・計算式・原因・影響・重要度の理由）。"""
    s = st.session_state
    result, info = s.diagnosis, s.business_info
    analysis = result.analysis
    html(ui.label("Overview", "業務の概要", first=True, anchor="evidence"))
    html(f'<span class="bic-tag">{ui.esc(info.department)}</span><span class="bic-tag">{ui.esc(info.task_name)}</span>')
    st.write(analysis.business_summary)
    if analysis.quantities:  # 本文・不足情報・試算で同じ値と出どころを使う（AI-BIC 3.1）
        html(result_top.quantity_panel(s.project))
        result_top.render_fix_form(s.project)
    meta = [s.hearing_state.finished_reason]
    if s.hearing_state.extra_finished_reason:
        meta.append(s.hearing_state.extra_finished_reason)
    if result.attachment_names:
        meta.append("診断に使った資料：" + "、".join(result.attachment_names))
    html(ui.footnote("　／　".join(meta)))
    if partial:
        with st.expander(f"今回の入力（途中終了時点：{answered}問回答。結果の判定には使っていません）"):
            html("".join(ui.qa(turn) for turn in partial.turns if turn.answer is not None)
                 or ui.footnote("回答した質問はありません。"))
    if result.missing_item_ids:
        html(ui.note("未確認事項：次の項目は情報が不足しています。結果は参考としてご覧ください。"))
        for item_id in result.missing_item_ids:
            html(ui.footnote(ui.missing_item_text(ITEMS[item_id], s.hearing_state.item_status.get(item_id))))

    # --- 課題（F03） ---
    html(ui.label("Issues", f"抽出された課題（{len(analysis.issues)}件）"))
    for no, issue in enumerate(analysis.issues, start=1):
        viewpoints = "観点：" + "・".join(VIEWPOINT_NAMES.get(v, v) for v in issue.viewpoint_ids)
        sources = [ui.evidence_source(ev, result.attachment_names, sample=partial is not None) for ev in issue.evidences]
        downgraded = result.downgraded.get(issue.issue_id)
        # 一覧は課題名・重要度・確認状態・短い説明。引用・計算式・原因・影響・重要度の理由は開いて読む（AI-BIC 3.2）
        html(ui.issue_row(no, issue, downgraded))
        with st.expander("引用・計算式・原因・影響・重要度の理由を見る"):
            html(ui.issue_card(no, issue, viewpoints, sources, downgraded))
    if analysis.no_issue_viewpoints:
        names = "、".join(VIEWPOINT_NAMES.get(v, v) for v in analysis.no_issue_viewpoints)
        html(ui.footnote(f"問題が確認できなかった観点：{names}"))


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
        "readiness": render_readiness,
        "attach": render_attach,
        "result": render_result,
    }[st.session_state.stage]()


main()
