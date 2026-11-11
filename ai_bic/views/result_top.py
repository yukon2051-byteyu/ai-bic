"""結果画面の冒頭・不足情報・改善案の比較と、画面の中の移動（AI-BIC 3.0 第1段階）。

画面の情報の順：結論と次の操作 → 改善案の比較 → 詳細な根拠・業務フロー・試算・レビュー。
中身はプログラム（overview.py）が既存の診断結果から作る。AIは使わない。

【選んだ案の引き継ぎ】
比較で選んだ案を、業務フロー・試算・レビューの選択にもそろえる（focus）。
「業務フローを見る」などのボタンは、案をそろえたうえで、その見出しまで画面を動かす（go）。
"""

import json
import re

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

SECTIONS = ui.SECTIONS  # 移動先（見出しに付けた id）

# 結果画面の4つのタブ（AI-BIC 3.2）：キー → 表示名。初期表示は「改善案を選ぶ」
TABS = {"choose": "改善案を選ぶ", "gaps": "不足情報を確認", "evidence": "診断の根拠を見る", "trial": "小さく試す"}
TAB_KEY = "result_tab"
TAB_OF = {"compare": "choose", "flows": "choose", "sim": "choose", "review": "choose", "gaps": "gaps",
          "evidence": "evidence", "trial": "trial"}
TAB_BAR = ".st-key-result_tab"  # タブの並び（画面を動かす先）

# タブを切り替えても消さない入力欄（未送信の回答・試算の値・試行の入力など）。ボタンや表の編集は含めない
KEEP_PREFIXES = ("gap_answer_", "gap_source_", "gap_note_", "gap_value_", "qfix_qid", "qfix_value", "qfix_source",
                 "qfix_note", "fact_", "trial_scope", "trial_owner", "trial_due", "trial_m", "trial_add_name",
                 "trial_add_kind", "trial_add_unit", "trial_decision", "trial_reason", "review_decision_", "sim_P",
                 "flow_view", "flow_pid", "review_pid", "compare_plan", "sim_plan", "rethink_pid", "work_")
KEEP_EXCLUDE = re.compile(r"(_save|_submit|_decide|_run|_start|_demo|_scenarios)$|_ack_|_restore_")


def keep_inputs() -> None:
    """タブを閉じても、入力欄の値を消さない。

    Streamlit は、表示しなかった入力欄の値を消す。表示の前に値を入れ直すと、残る（入力欄の値ではなく、
    プログラムが入れた値として扱われるため）。
    """
    s = st.session_state
    for key in list(s.keys()):
        name = str(key)
        if name.startswith(KEEP_PREFIXES) and not KEEP_EXCLUDE.search(name):
            s[key] = s[key]


def render_tabs() -> dict:
    """4つのタブ。開いているタブの中身だけを描く（.open）。"""
    labels = list(TABS.values())
    if st.session_state.get(TAB_KEY) not in labels:
        st.session_state[TAB_KEY] = labels[0]
    containers = st.tabs(labels, key=TAB_KEY, on_change="rerun")
    return dict(zip(TABS, containers))
COMPARE_KEY = "compare_plan"  # 比較で選んでいる案（P1 など）


def html(text: str) -> None:
    st.markdown(text, unsafe_allow_html=True)


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
    s[TAB_KEY] = TABS[TAB_OF[section]]  # 対応するタブを開く
    if section in ("flows", "sim", "review"):
        s[WORK_KEY] = section  # 作業領域を切り替える（その場に出す）
    # タブの先頭（compare・gaps・evidence・trial）なら、タブの並びが上に来るように。それ以外はその見出しへ
    request_scroll(TAB_BAR if section in ("compare", "gaps", "evidence", "trial") else "#" + SECTIONS[section])


def request_scroll(selector: str) -> None:
    """次の表示で、selector（CSSの書き方）の要素まで画面を動かすよう頼む。"""
    s = st.session_state
    s["scroll_to"] = selector
    s["scroll_count"] = s.get("scroll_count", 0) + 1


# 画面を動かす処理（ブラウザで動く）。見出しの上端を、固定のヘッダーがあればその直下14px、なければ上端から20pxに合わせる。
# 見出しの上の余白（margin）は位置に含めない。Streamlit が描き直して位置がずれることがあるため、少し待って何回か合わせる。
# 注意：Streamlit の安全チェックで消されないよう、この中に「<英字」「</」を書かない
SCROLL_JS = """
(function () {
  var target = TARGET;
  function offset() {
    var h = document.querySelector('header[data-testid="stHeader"]');
    if (h) {
      var cs = getComputedStyle(h), r = h.getBoundingClientRect();
      var fixed = cs.position === 'fixed' || cs.position === 'sticky';
      if (fixed && r.height > 0 && r.bottom > 0 && cs.visibility !== 'hidden' && cs.display !== 'none') return r.bottom + 14;
    }
    return 20;
  }
  function scroller(el) {
    var p = el.parentElement;
    while (p && p !== document.body && p !== document.documentElement) {
      var o = getComputedStyle(p).overflowY;
      if ((o === 'auto' || o === 'scroll') && p.scrollHeight > p.clientHeight) return p;
      p = p.parentElement;
    }
    return null;
  }
  function align() {
    var el = document.querySelector(target);
    if (!el) return;
    var delta = el.getBoundingClientRect().top - offset();
    if (Math.abs(delta) > 1) {
      var sc = scroller(el);
      if (sc) sc.scrollTop += delta; else window.scrollBy(0, delta);
    }
  }
  // 図などを描き終えると位置がずれるため、少し間を空けて何回か合わせる（合っていれば動かさない）
  [120, 450, 900, 1600].forEach(function (ms) { setTimeout(align, ms); });
})();
"""


def render_scroll() -> None:
    """request_scroll で頼まれた要素まで画面を動かす（ページの最後に置く）。"""
    target = st.session_state.pop("scroll_to", None)
    if not target:
        return
    count = st.session_state.get("scroll_count", 0)
    # 回数を入れて毎回違う中身にする（同じ中身だと、2回目に同じ移動先を押したとき動かないため）
    script = SCROLL_JS.replace("TARGET", json.dumps(target))
    st.html(f'<span data-scroll="{count}"></span><script>{script}</script>', unsafe_allow_javascript=True)


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
    html(ui.label("Check", "不足情報の確認", anchor="gaps", first=True))
    if not ov.gaps:
        html(ui.note("次の判断に必要で、まだ確認できていない情報はありません。"))
        return
    st.caption("次の判断に必要なのに、まだ確認できていない情報です（重要なものから）。その場で答えると、関係する結果だけを更新します。"
               "あとで確認する項目は、答えずにそのままにしてください（「要確認」のまま残ります）。")
    s = st.session_state
    for kind, text in s.pop("gap_messages", []):
        (st.success if kind == "ok" else st.warning if kind == "warn" else st.error)(text)
    if project.diagnosis.analysis.quantities:
        html(quantity_panel(project, compact=True))
    for g in ov.gaps:
        html(ui.gap_card(g))
        if g.key == overview.NUMBERS_KEY:
            _numbers_form(project)  # 数値の一覧がない古い案件
        elif g.numeric:
            _value_form(project, g)
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
        with st.container(border=True):  # フォームにしない（タブを切り替えても書きかけの値が残るように。AI-BIC 3.2）
            st.text_area("回答", key=f"gap_answer_{fk}", max_chars=config.ANSWER_MAX_CHARS)
            st.radio("出どころ", SOURCES, key=f"gap_source_{fk}", horizontal=True,
                     help="ご本人が知っていること（本人の申告）か、資料で確かめたこと（添付資料）かを区別します")
            st.text_input("資料の名前（添付資料で確かめた場合）", key=f"gap_note_{fk}", max_chars=100)
            st.button("回答を保存して、関係する結果を更新する", key=f"gap_submit_{fk}",
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


def quantity_panel(project: Project, compact: bool = False) -> str:
    """分かっている数値と、計算する値（式つき）。未確定の値は、何が分かれば決まるかを示す（AI-BIC 3.1）。

    compact：不足情報欄の先頭に出す短い版（計算する値の結論だけ）。
    """
    rows = []
    if not compact:
        for q in project.diagnosis.analysis.quantities:
            k = facts.known_values(project)[q.qid]
            if k.value is not None:
                rows.append(("✓", q.name, f"{sim.fmt(k.value)}{q.unit}（{k.source}）"))
            else:
                state = "申告と資料が食い違い（" + k.source + "）" if k.status == "食い違い" else k.status
                rows.append(("？", q.name, state))
    for d in facts.derived_values(project):
        if d.calc.value is not None:
            rows.append(("✓", d.name, d.calc.text))
        elif d.calc.error:
            rows.append(("？", d.name, d.calc.error))
        else:
            rows.append(("？", d.name, f"未確定（{'・'.join(d.missing_names)}が分かるまで）"))
    if not rows:
        return ""
    body = "".join(f"<dt>{ui.esc(m)} {ui.esc(n)}</dt><dd>{ui.esc(v)}</dd>" for m, n, v in rows)
    title = "計算できる値と、まだ決まらない値" if compact else "分かっている数値（入力と照合済み）と、計算する値"
    return f'<div class="bic-quantities"><div class="k">{title}</div><dl class="bic-dl">{body}</dl></div>'


def render_fix_form(project: Project) -> None:
    """分かっている数値を直す（例：資料で確かめたら値が違った）。関係する計算・試算だけを更新する。"""
    quantities = project.diagnosis.analysis.quantities
    names = {q.qid: f"{q.name}（{q.unit}）" for q in quantities}
    with st.expander("分かっている数値を直す・確かめた値を入れる"):
        with st.container(border=True):  # フォームにしない（タブを切り替えても書きかけの値が残るように。AI-BIC 3.2）
            st.selectbox("数値", list(names), key="qfix_qid", format_func=names.get)
            c = st.columns([2, 2])
            st.session_state.setdefault("qfix_value", None)  # 空欄で始める（value= を渡すと、保った値と重なり警告が出る）
            c[0].number_input("値", key="qfix_value", min_value=0.0, placeholder="未入力")
            c[1].radio("出どころ", SOURCES, key="qfix_source", horizontal=True)
            st.text_input("資料の名前（添付資料で確かめた場合）", key="qfix_note", max_chars=100)
            st.button("値を保存して、関係する結果を更新する", key="qfix_submit", on_click=_save_fix, args=(project,))
    for kind, text in st.session_state.pop("qfix_messages", []):
        (st.success if kind == "ok" else st.warning if kind == "warn" else st.error)(text)


def _save_fix(project: Project) -> None:
    s = st.session_state
    value = s.get("qfix_value")
    if value is None:
        s["qfix_messages"] = [("error", "値を入力してください。")]
        return
    try:
        changed = facts.record_fact(project, s["qfix_qid"], value, s["qfix_source"], s["qfix_note"])
        changes = facts.apply_to_simulations(project)
    except ValueError as e:
        s["qfix_messages"] = [("error", str(e))]
        return
    simulator.sync_widgets(project, [(c.pid, c.field) for c in changes if c.applied])
    if changed:
        facts.mark_premise(project, pj.proposal_ids(project))
    messages = [("ok", f"値を保存しました（{s['qfix_source']}）。関係する計算と試算を更新しました。")]
    messages += [("warn", f"案{c.pid[1:]}の{sim.label(c.field)}は手入力の値を残しました（確認した値：{sim.fmt(c.value)}）。")
                 for c in changes if not c.applied]
    if facts.state_of(project, s["qfix_qid"]).conflict:
        messages.append(("warn", "申告と資料の値が食い違っています。食い違いが解消するまで、その値は使いません。"))
    s["qfix_messages"] = messages


def _value_form(project: Project, g) -> None:
    """数値1つの確認。入力した値は関係する試算だけに反映し、手入力の値は残す。"""
    fk = _form_key(g.key)
    with st.expander("この場で答える"):
        with st.container(border=True):  # フォームにしない（タブを切り替えても書きかけの値が残るように。AI-BIC 3.2）
            c = st.columns([2, 2])
            st.session_state.setdefault(f"gap_value_{fk}", None)
            c[0].number_input("値", key=f"gap_value_{fk}", min_value=0.0, placeholder="未入力")
            c[1].radio("出どころ", SOURCES, key=f"gap_source_{fk}", horizontal=True)
            st.text_input("資料の名前（添付資料で確かめた場合）", key=f"gap_note_{fk}", max_chars=100)
            st.button("値を保存して、関係する結果を更新する", key=f"gap_submit_{fk}",
                                  on_click=_save_value, args=(project, g))


def _save_value(project: Project, g) -> None:
    s = st.session_state
    fk = _form_key(g.key)
    value = s.get(f"gap_value_{fk}")
    if value is None:
        s["gap_messages"] = [("error", "値を入力してください。分からない場合は、答えずにそのままにしてください。")]
        return
    name = g.key.split(":", 1)[1]
    try:
        changed = facts.record_fact(project, name, value, s[f"gap_source_{fk}"], s[f"gap_note_{fk}"])
        changes = facts.apply_to_simulations(project)
    except ValueError as e:
        s["gap_messages"] = [("error", str(e))]
        return
    simulator.sync_widgets(project, [(c.pid, c.field) for c in changes if c.applied])
    pids = facts.affected_pids(project, g.affects)
    if changed:
        facts.mark_premise(project, pids)
    messages = [("ok", f"値を保存しました（{s[f'gap_source_{fk}']}）。関係する計算と試算を更新しました。")]
    messages += [("warn", f"案{c.pid[1:]}の{sim.label(c.field)}は手入力の値を残しました（確認した値：{sim.fmt(c.value)}）。")
                 for c in changes if not c.applied]
    if facts.state_of(project, name).conflict:
        messages.append(("warn", "申告と資料の値が食い違っています。食い違いが解消するまで、その値は使いません。"))
    s["gap_messages"] = messages


def _numbers_form(project: Project) -> None:
    """試算の元になる数値の確認。計算はプログラムが行い、関係する試算だけを更新する。"""
    with st.expander("数値を確認して入力する", expanded=False):
        with st.container(border=True):  # フォームにしない（タブを切り替えても書きかけの値が残るように。AI-BIC 3.2）
            cols = st.columns(2)
            for i, (name, (label, unit, limit)) in enumerate(facts.FACTS.items()):
                st.session_state.setdefault(f"fact_{name}", None)
                cols[i % 2].number_input(f"{label}（{unit}）", min_value=0.0, max_value=limit,
                                         key=f"fact_{name}", placeholder="未確認（空欄のまま）")
            st.radio("出どころ", SOURCES, key="fact_source", horizontal=True)
            st.text_input("資料の名前（添付資料で確かめた場合）", key="fact_note", max_chars=100)
            st.button("数値を保存して、関係する結果を更新する", key="gap_submit_numbers",
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
    for name in entered:
        s[f"fact_{name}"] = None  # 保存したら入力欄を空にする
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
    html(ui.label("Compare", "改善案の比較", anchor="compare", first=True))
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
    # 選んだ案の要点（目的・変更内容・期待する効果・着手前の条件・主なリスク）。詳しい説明は開いて読む（AI-BIC 3.2）
    issues = {i.issue_id: i for i in result.analysis.issues}
    html(ui.proposal_brief(numbers[pid], by_pid[pid], [issues[i].title for i in by_pid[pid].target_issue_ids if i in issues]))
    with st.expander("詳しい説明を読む（導入前後・最初の一歩・推奨理由など）"):
        html(ui.proposal_card(numbers[pid], by_pid[pid]))
    # 作業領域：業務フロー・試算・レビューは、選んだ1つだけをその場に出す（全部を縦に並べない）
    st.markdown(f'<div id="{SECTIONS["work"]}" class="bic-work-title">案{numbers[pid]}で次にすること</div>',
                unsafe_allow_html=True)
    area = st.session_state.get(WORK_KEY, "brief")
    c1, c2, c3 = st.columns(3)
    for col, (key, label, section) in zip((c1, c2, c3), WORK_BUTTONS):
        col.button(label, key=key, on_click=go, args=(section, pid), width="stretch",
                   type="primary" if area == section else "secondary")


WORK_KEY = "work_area"  # 改善案を選ぶタブの作業領域（brief：要点だけ・flows・sim・review）
WORK_BUTTONS = [("go_flows", "業務フローを見る", "flows"), ("go_sim", "効果を試算する", "sim"),
                ("go_review", "この案の弱点を確認する", "review")]
WORK_NAMES = {"flows": "業務フロー", "sim": "効果の試算", "review": "弱点の確認と確定"}


def work_area() -> str:
    """いま開いている作業領域（brief・flows・sim・review）。"""
    return st.session_state.get(WORK_KEY, "brief")


def render_back() -> None:
    """作業領域から比較表へ戻る。作業領域は閉じるが、入力した値や選んだ案はそのまま残る。"""
    st.button("比較表へ戻る", key="back_to_compare", on_click=_back)


def _back() -> None:
    st.session_state[WORK_KEY] = "brief"
    request_scroll(TAB_BAR)
