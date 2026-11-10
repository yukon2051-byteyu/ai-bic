"""結果画面の冒頭・不足情報・改善案の比較と、画面の中の移動（AI-BIC 3.0 第1段階）。

画面の情報の順：結論と次の操作 → 改善案の比較 → 詳細な根拠・業務フロー・試算・レビュー。
中身はプログラム（overview.py）が既存の診断結果から作る。AIは使わない。

【選んだ案の引き継ぎ】
比較で選んだ案を、業務フロー・試算・レビューの選択にもそろえる（focus）。
「業務フローを見る」などのボタンは、案をそろえたうえで、その見出しまで画面を動かす（go）。
"""

import streamlit as st

from ai_bic import overview, ui
from ai_bic import project as pj
from ai_bic.overview import Overview
from ai_bic.project import Project
from ai_bic.views import flows
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
def render_gaps(ov: Overview, assumptions: list[str]) -> None:
    anchor("gaps")
    html(ui.label("Check", "不足情報の確認"))
    st.caption("次の判断に必要なのに、まだ確認できていない情報です（重要なものから）。"
               "分かったことは、業務フロー・試算の表で直せます。")
    html(ui.gap_list(ov.gaps))
    for assumption in assumptions:
        html(ui.footnote(f"仮定条件：{assumption}"))


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
