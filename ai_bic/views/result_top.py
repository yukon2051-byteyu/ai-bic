"""結果画面の冒頭・不足情報・改善案の比較と、画面の中の移動（AI-BIC 3.0 第1段階）。

画面の情報の順：結論と次の操作 → 改善案の比較 → 詳細な根拠・業務フロー・試算・レビュー。
中身はプログラム（overview.py）が既存の診断結果から作る。AIは使わない。

【選んだ案の引き継ぎ】
比較で選んだ案を、業務フロー・試算・レビューの選択にもそろえる（focus）。
「業務フローを見る」などのボタンは、案をそろえたうえで、その見出しまで画面を動かす（go）。
"""

import streamlit as st

from ai_bic import config, facts, overview, ui
from ai_bic import project as pj
from ai_bic import review as review_logic
from ai_bic import simulation as sim
from ai_bic.errors import AIError, user_message
from ai_bic.overview import Overview
from ai_bic.project import Project
from ai_bic.views import flows, simulator
from ai_bic.views.simulator import PLAN_KEY as SIM_PLAN_KEY

# 移動先（見出しの前に置く目印の id）
SECTIONS = {"gaps": "sec-gaps", "compare": "sec-compare", "flows": "sec-flows", "sim": "sec-sim", "review": "sec-review"}
COMPARE_KEY = "compare_plan"  # 比較で選んでいる案（P1 など）


def html(text: str) -> None:
    st.markdown(text, unsafe_allow_html=True)


def anchor(section: str) -> None:
    html(f'<div id="{SECTIONS[section]}"></div>')


def focus(pid: str) -> None:
    """選んだ案を、比較・業務フロー・試算・レビューのすべての選択にそろえる（利用者に選び直させない）。"""
    s = st.session_state
    for key in (COMPARE_KEY, SIM_PLAN_KEY, "flow_pid", "review_pid"):
        s[key] = pid


def go(section: str, pid: str | None = None) -> None:
    """ボタンのコールバック：案をそろえ、次の表示でその見出しまで画面を動かす。"""
    s = st.session_state
    if pid:
        focus(pid)
        if section == "flows":
            s["flow_view"] = flows.VIEWS[2]  # 選んだ案の改善後を、現状と並べて見せる
    s["scroll_to"] = SECTIONS[section]
    s["scroll_count"] = s.get("scroll_count", 0) + 1


def render_scroll() -> None:
    """go で頼まれた見出しまで画面を動かす（ページの最後に置く。描き終わるのを少し待つ）。"""
    target = st.session_state.pop("scroll_to", None)
    if not target:
        return
    count = st.session_state.get("scroll_count", 0)
    # 回数を入れて毎回違う中身にする（同じ中身だと、2回目に同じ移動先を押したとき動かないため）
    st.html(
        f'<span data-scroll="{count}"></span><script>setTimeout(function () {{'
        f' var el = document.getElementById("{target}"); if (el) el.scrollIntoView({{block: "start"}}); }}, 250);</script>',
        unsafe_allow_javascript=True,
    )


# ---------------------------------------------------------------
# 1. 結論と次の操作
# ---------------------------------------------------------------
def render_decision(ov: Overview, has_proposals: bool) -> None:
    html(ui.decision_card(ov, overview.MAX_TOP_GAPS))
    # 主ボタン：重要な不足情報があれば「不足情報を確認する」、なければ「改善案を比較する」
    buttons = []
    if ov.has_important_gaps:
        buttons.append(("不足情報を確認する", "gaps"))
    if has_proposals:
        buttons.append(("改善案を比較する", "compare"))
    if not ov.has_important_gaps and ov.gaps:
        buttons.append(("不足情報を確認する", "gaps"))
    cols = st.columns(len(buttons)) if buttons else []
    for i, (col, (label, section)) in enumerate(zip(cols, buttons)):
        col.button(label, key=f"go_{section}", type="primary" if i == 0 else "secondary",
                   on_click=go, args=(section,), width="stretch")


# ---------------------------------------------------------------
# 不足情報
# ---------------------------------------------------------------
def render_gaps(project: Project, ov: Overview, assumptions: list[str], get_client) -> None:
    """不足情報の確認（AI-BIC 3.0）：重要なものから示し、その場で答えて関係する結果だけを更新する。"""
    anchor("gaps")
    html(ui.label("Check", "不足情報の確認"))
    st.caption("次の判断に必要なのに、まだ確認できていない情報です（重要なものから）。その場で答えると、関係する結果だけを更新します。"
               "あとで確認する項目は、答えずにそのままにしてください（「要確認」のまま残ります）。")
    s = st.session_state
    for kind, text in s.pop("gap_messages", []):
        (st.success if kind == "ok" else st.warning if kind == "warn" else st.error)(text)
    for g in ov.gaps:
        html(ui.gap_card(g))
        if g.numeric:
            _numbers_form(project)
        else:
            _answer_form(project, g)
    for assumption in assumptions:
        html(ui.footnote(f"仮定条件：{assumption}"))
    _render_rethink(project, get_client)


SOURCES = ["本人の申告", "添付資料"]


def _form_key(key: str) -> str:
    return key.replace(":", "_")


def _answer_form(project: Project, g) -> None:
    fk = _form_key(g.key)
    with st.expander("回答を直す" if g.answered else "この場で答える"):
        with st.form(f"gap_form_{fk}", clear_on_submit=False):
            st.text_area("回答", key=f"gap_answer_{fk}", max_chars=config.ANSWER_MAX_CHARS)
            st.radio("出どころ", SOURCES, key=f"gap_source_{fk}", horizontal=True,
                     help="ご本人が知っていること（本人の申告）か、資料で確かめたこと（添付資料）かを区別します")
            st.text_input("資料の名前（添付資料で確かめた場合）", key=f"gap_note_{fk}", max_chars=100)
            st.form_submit_button("回答を保存して、関係する結果を更新する", key=f"gap_submit_{fk}",
                                  on_click=_save_answer, args=(project, g))


def _save_answer(project: Project, g) -> None:
    s = st.session_state
    fk = _form_key(g.key)
    try:
        pids = facts.answer_gap(project, g.key, g.question, s[f"gap_answer_{fk}"], s[f"gap_source_{fk}"],
                                g.affects, s[f"gap_note_{fk}"])
    except ValueError as e:
        s["gap_messages"] = [("error", str(e))]
        return
    names = "・".join(f"案{pid[1:]}" for pid in pids)
    s["gap_messages"] = [("ok", f"回答を保存しました（{s[f'gap_source_{fk}']}）。{names}の前提を更新しました。"
                                "過去のレビューは「再レビューが必要」になります。改善案の内容を見直す場合は、下の「回答を反映して改善案を見直す」を使ってください。")]


def _numbers_form(project: Project) -> None:
    """試算の元になる数値の確認。計算はプログラムが行い、関係する試算だけを更新する。"""
    with st.expander("数値を確認して入力する", expanded=False):
        with st.form("gap_form_numbers", clear_on_submit=True):
            cols = st.columns(2)
            for i, (name, (label, unit, limit)) in enumerate(facts.FACTS.items()):
                cols[i % 2].number_input(f"{label}（{unit}）", min_value=0.0, max_value=limit, value=None,
                                         key=f"fact_{name}", placeholder="未確認（空欄のまま）")
            st.radio("出どころ", SOURCES, key="fact_source", horizontal=True)
            st.text_input("資料の名前（添付資料で確かめた場合）", key="fact_note", max_chars=100)
            st.form_submit_button("数値を保存して、関係する結果を更新する", key="gap_submit_numbers",
                                  on_click=_save_numbers, args=(project,))
        st.caption("入力した項目だけを記録します。空欄の項目は、前に確認した値のまま（なければ未確認）です。")
    html(numbers_status(project))


def _save_numbers(project: Project) -> None:
    s = st.session_state
    source, note = s["fact_source"], s["fact_note"]
    entered = {name: s[f"fact_{name}"] for name in facts.FACTS if s.get(f"fact_{name}") is not None}
    if not entered:
        s["gap_messages"] = [("error", "数値が入力されていません。確認できた項目だけ入力してください。")]
        return
    changed = False
    for name, value in entered.items():
        changed |= facts.record_fact(project, name, value, source, note)
    messages = []
    try:
        changes = facts.apply_to_simulations(project)
    except ValueError as e:
        s["gap_messages"] = [("error", f"試算に反映できませんでした：{e}")]
        return
    simulator.sync_widgets(project, [(c.pid, c.field) for c in changes if c.applied])
    if changed:
        facts.mark_premise(project, pj.proposal_ids(project))
    applied = [c for c in changes if c.applied]
    kept = [c for c in changes if not c.applied]
    messages.append(("ok", f"数値を保存しました（{source}）。"
                     + (f"試算を計算し直した案：{'・'.join(sorted({f'案{c.pid[1:]}' for c in applied}))}。" if applied
                        else "試算の元の値は変わりませんでした。")))
    for c in kept:
        messages.append(("warn", f"案{c.pid[1:]}の{sim.label(c.field)}は手入力の値を残しました（確認した値：{sim.fmt(c.value)}）。"
                                 "試算の画面で「確認した値に戻す」を押すと切り替わります。"))
    conflicts = [facts.FACTS[n][0] for n in facts.FACTS if facts.state_of(project, n).conflict]
    if conflicts:
        messages.append(("warn", f"申告と資料の値が食い違っている項目があります（{'・'.join(conflicts)}）。"
                                 "食い違いが解消するまで、その値は試算に使いません。"))
    s["gap_messages"] = messages


def numbers_status(project: Project) -> str:
    """確認した数値と、プログラムによる計算の結果（式つき）。値が足りない・おかしいときは計算せずに理由を示す。"""
    rows = []
    states = {name: facts.state_of(project, name) for name in facts.FACTS}
    for name, (label, unit, _) in facts.FACTS.items():
        st_ = states[name]
        if st_.conflict:
            value = f"？ 食い違い（{st_.conflict_text}）。どちらが正しいか確認してください"
        elif st_.value is None:
            value = "？ 未確認"
        else:
            note = "、".join(e.note for e in st_.entries if e.note)
            value = f"✓ {sim.fmt(st_.value)}{unit}（{st_.source}{f'：{note}' if note else ''}）"
        rows.append((label, value))
    share = facts.ratio(states["total_hours"].value, states["part_hours"].value)
    rows.append(("最も時間がかかる作業の割合", share.formula or share.message))
    per_case = facts.minutes_per_case(states["total_hours"].value, states["cases_per_month"].value)
    if per_case.formula or per_case.message:
        rows.append(("1件あたりの作業時間（試算に使う）", per_case.formula or per_case.message))
    body = "".join(f"<dt>{ui.esc(k)}</dt><dd>{ui.esc(v)}</dd>" for k, v in rows)
    return f'<dl class="bic-dl bic-facts">{body}</dl>'


def _render_rethink(project: Project, get_client) -> None:
    """回答を反映して改善案を見直す（AIは利用者が押したときだけ。対象と影響範囲を先に示す）。"""
    if not project.gap_answers or project.diagnosis.proposals is None:
        return
    targets = sorted({pid for a in project.gap_answers.values() for pid in facts.affected_pids(project, a.affects)})
    st.markdown("**回答を反映して改善案を見直す**")
    st.caption("数値の更新だけならAIは使いません。課題の説明や改善案の内容を回答に合わせて直す場合だけ、ここからAIで見直します。")
    numbers = {pid: no for no, pid in enumerate(pj.proposal_ids(project), start=1)}
    pid = st.selectbox("見直す改善案", targets, key="rethink_pid", format_func=lambda x: f"案{numbers[x]}：{project.diagnosis.proposals.proposals[numbers[x] - 1].title}")
    used = [a for a in project.gap_answers.values() if pid in facts.affected_pids(project, a.affects)]
    html(ui.note(f"対象：案{numbers[pid]}　／　反映する回答：{len(used)}件（"
                 + "、".join(a.question[:30] for a in used) + "）　／　影響範囲：見直すと、この案の改善後フロー・試算・レビューが「要更新」になります。"
                 "見直す前の改善案は記録に残ります。"))
    if error := st.session_state.get("rethink_error"):
        st.error(user_message(error) + "　回答と直前の診断結果は残っています。もう一度試せます。")
    if project.mode == "demo":
        label, note = "回答を反映して改善案を見直す", "デモ：AIを使わず、回答を前提条件に加えた例を作ります。"
    else:
        left = 0 if st.session_state.get("loaded") else review_logic.calls_left(project, "revise")
        if left <= 0:
            html(ui.note("この案件では、AIで改善案を見直せる回数の上限に達しました（または読み込んだ案件のため使えません）。"))
            return
        label, note = "回答を反映して改善案を見直す（AI）", f"AIを1回呼びます（作り直しと合わせて、この案件で残り{left}回）。"
    if st.button(label, key="rethink_run"):
        st.session_state.pop("rethink_error", None)
        try:
            with st.spinner("改善案を見直しています…（1分ほどかかることがあります）"):
                review_logic.revise(get_client(), project, pid, with_answers=True)
        except AIError as e:
            st.session_state["rethink_error"] = e.kind
        st.rerun()
    st.caption(note)


# ---------------------------------------------------------------
# 2. 改善案の比較 → 選んだ案の詳細と、次の作業への移動
# ---------------------------------------------------------------
def render_compare(project: Project) -> None:
    result = project.diagnosis
    proposals = result.proposals.proposals
    pids = pj.proposal_ids(project)
    anchor("compare")
    html(ui.label("Compare", "改善案の比較"))
    html(ui.compare_table(overview.compare_rows(result)))
    relations = overview.valid_relations(result.proposals.relations, len(proposals))
    if relations:
        html(ui.relation_list(relations))

    s = st.session_state
    if s.get(COMPARE_KEY) not in pids:
        s[COMPARE_KEY] = pids[0]
    numbers = {pid: no for no, pid in enumerate(pids, start=1)}
    by_pid = dict(zip(pids, proposals))
    st.markdown("**詳しく見る案**")
    with st.container(key="plans_compare"):
        pid = st.radio("詳しく見る案", pids, key=COMPARE_KEY, horizontal=True, label_visibility="collapsed",
                       format_func=lambda x: f"案{numbers[x]}",
                       captions=[ui.md_text(overview.tab_name(by_pid[x])) for x in pids],
                       on_change=lambda: focus(st.session_state[COMPARE_KEY]))
    html(ui.proposal_card(numbers[pid], by_pid[pid]))
    st.markdown(f"**案{numbers[pid]}で次にすること**")
    c1, c2, c3 = st.columns(3)
    c1.button("業務フローを見る", key="go_flows", on_click=go, args=("flows", pid), width="stretch")
    c2.button("効果を試算する", key="go_sim", on_click=go, args=("sim", pid), width="stretch")
    c3.button("この案の弱点を確認する", key="go_review", on_click=go, args=("review", pid), width="stretch")
