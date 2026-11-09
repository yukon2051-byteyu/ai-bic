"""業務フローの画面（AI-BIC 2.0 機能B）。

画面の並び（指示書 7.1 の3階層）：
1. 結論：1件あたりの作業時間（現状 → 改善後）と、変化した工程の数
2. 要点：フロー図（現状／改善後／左右に並べて比較）と凡例
3. 根拠：工程の詳細、AIからの確認事項、照合でプログラムが直したこと

編集は表（st.data_editor）と上下移動のボタンで行う（ドラッグ操作は Streamlit では複雑になるため使わない）。
図は保存せず、表示のたびに工程データ（flow.Flow）から作る。
"""

from datetime import datetime

import pandas as pd
import streamlit as st
from pydantic import ValidationError

from ai_bic import config, report, ui
from ai_bic import flow as fl
from ai_bic import flow_ai
from ai_bic import project as pj
from ai_bic.errors import AIError, is_retryable, user_message
from ai_bic.views import flow_canvas
from ai_bic.project import Project

VIEWS = ["現状（As-Is）", "改善後（To-Be）", "左右に並べて比較"]
KINDS = ["作業", "判断", "承認"]
CHANGES = ["変更なし", "自動化", "変更", "短縮", "追加", "廃止"]
MANUAL = {"はい": True, "いいえ": False, "不明": None}
# 表の列名 → 工程の項目名
COLUMNS = {
    "工程名": "name", "種類": "kind", "担当": "actor", "作業時間（分）": "minutes_per_case",
    "実施割合（%）": "occurrence_rate", "使用システム": "system", "手作業": "manual", "作業内容": "description",
    "待ち・手戻り": "wait_or_rework", "課題・ボトルネック": "problem", "要確認": "needs_confirmation",
    "変化": "change", "改善内容": "change_note",
}
TO_BE_ONLY = ("変化", "改善内容")


def html(text: str) -> None:
    st.markdown(text, unsafe_allow_html=True)


# ---------------------------------------------------------------
# 対象のフローの読み書き（target は "as_is" か改善案ID）
# ---------------------------------------------------------------
def _get(project: Project, target: str) -> fl.Flow | None:
    return project.as_is if target == "as_is" else project.to_be.get(target)


def _set(project: Project, target: str, flow: fl.Flow) -> None:
    if target == "as_is":
        project.as_is = flow
        pj.mark_updated(project, "as_is")
    else:
        project.to_be[target] = flow
        pj.mark_updated(project, "to_be", target)


def _key(target: str, project: Project, name: str) -> str:
    """表の部品の名前。フローを変えるたびに版番号が上がり、新しい表として作り直す（古い編集が残らない）。"""
    kind = "as_is" if target == "as_is" else "to_be"
    return f"flow_{name}_{target}_{pj.version(project, kind, None if target == 'as_is' else target)}"


# ---------------------------------------------------------------
# 表示
# ---------------------------------------------------------------
def render(project: Project, get_client) -> None:
    html(ui.label("Flow", "業務フロー（現状と改善後）"))
    if project.diagnosis.proposals is None:
        html(ui.note("改善案がないため、業務フローは作成していません。"))
        return
    if project.as_is is None:
        _render_start(project, get_client)
        return

    pids = pj.proposal_ids(project)
    titles = {pid: f"案{no}：{p.title}" for no, (pid, p) in enumerate(zip(pids, project.diagnosis.proposals.proposals), 1)}
    c1, c2 = st.columns([3, 2])
    view = c1.radio("表示", VIEWS, horizontal=True, key="flow_view")
    pid = c2.selectbox("改善案", pids, format_func=titles.get, key="flow_pid")
    to_be = project.to_be.get(pid) or fl.Flow()

    if reasons := pj.outdated_reasons(project, "to_be", pid):
        st.warning(f"{titles[pid]}の改善後フローは、{'・'.join(reasons)}。内容を確認し、必要なら下の表で修正してください。")
        if st.button("改善後フローを確認した（このままでよい）", key=f"flow_ack_{pid}"):
            pj.mark_updated(project, "to_be", pid)
            st.rerun()

    # 1. 結論
    _render_summary(project.as_is, to_be)
    if project.case_unit:
        cases = (f"、月{project.cases_per_month:g}件（{project.cases_source}）"
                 if project.cases_per_month is not None else "、月の件数は不明")
        html(ui.footnote(f"工程の時間は「1件＝{project.case_unit}」あたりです{cases}。"))
    # 2. 要点：図（ズーム・パン・工程のクリックで詳細。比較は同じ縮尺、スマートフォンではタブ切替）
    shown = []
    if view != VIEWS[1]:
        shown.append(("現状（As-Is）", project.as_is, False))
    if view != VIEWS[0]:
        shown.append((f"改善後（To-Be）　{titles[pid]}", to_be, True))
    _render_chart(shown)
    _render_legend()
    if kept := project.kept_controls.get(pid):
        html(ui.footnote("品質管理・内部統制のために残した工程：" + "／".join(kept)))
    if setup := project.setup_tasks.get(pid):
        html(ui.footnote("初回だけの準備作業（毎回の時間には含めていません。効果試算の「その他の初期費用」の参考）："
                         + "／".join(setup)))

    # 3. 根拠
    _render_detail(project, pid, view, to_be)
    with st.expander("工程を編集する（現状フロー）"):
        _render_editor(project, "as_is")
    with st.expander(f"工程を編集する（改善後フロー　{titles[pid]}）"):
        if not to_be.steps and project.as_is.steps:
            st.caption("改善後フローがまだありません。現状フローを写して、変化を書き込むところから始められます。")
            st.button("現状フローを写して始める", key=f"flow_copy_{pid}", on_click=_copy_as_is, args=(project, pid))
        _render_editor(project, pid)
    _render_simulation_link(project, pid, titles[pid])
    _render_downloads(project, pid, to_be)


def _render_start(project: Project, get_client) -> None:
    st.markdown(
        "ヒアリングの内容と改善案から、**現状の業務フロー（As-Is）**と、改善案ごとの**改善後の業務フロー（To-Be）**を作ります。"
        "根拠となる発言を確認できない工程は「要確認」として示し、画面で修正できます。"
    )
    if error := st.session_state.get("flow_error"):
        st.error(user_message(error))
    c1, c2 = st.columns(2)
    with c1:
        if project.mode == "demo":
            if st.button("サンプルの業務フローを表示する", type="primary", key="flow_generate"):
                _generate(project, get_client)
            st.caption("デモ：事前に作った架空のサンプルです（AIは使いません）。")
        elif flow_ai.flow_calls_left(project) > 0:
            label = "業務フローを作成する（AI）" if not st.session_state.get("flow_error") else "もう一度作成する（AI）"
            if not st.session_state.get("flow_error") or is_retryable(st.session_state["flow_error"]):
                if st.button(label, type="primary", key="flow_generate"):
                    _generate(project, get_client)
            st.caption(f"AIを1回呼びます（この案件で{config.MAX_FLOW_CALLS}回まで。失敗した回は数えません）。1分ほどかかることがあります。")
        else:
            html(ui.note("この案件では、AIで業務フローを作れる回数の上限に達しました。"))
    with c2:
        if st.button("自分で作る（空のフローから）", key="flow_blank"):
            flow_ai.start_blank(project)
            st.rerun()


def _generate(project: Project, get_client) -> None:
    st.session_state.pop("flow_error", None)
    try:
        with st.spinner("業務フローを作っています…"):
            flow_ai.generate_flows(get_client(), project)
    except AIError as e:
        st.session_state["flow_error"] = e.kind
    st.rerun()


def _render_summary(as_is: fl.Flow, to_be: fl.Flow) -> None:
    d = fl.compare_flows(as_is, to_be)
    before, after = d.as_is_minutes.minutes_per_case, d.to_be_minutes.minutes_per_case
    c1, c2, c3 = st.columns(3)
    c1.metric("1件あたりの時間（現状）", f"{before:,.1f}分")
    c2.metric("1件あたりの時間（改善後）", f"{after:,.1f}分" if to_be.steps else "—",
              delta=f"{after - before:+,.1f}分" if to_be.steps else None, delta_color="inverse")
    c3.metric("変化した工程", f"{sum(d.counts.values())}個" if to_be.steps else "—")
    if d.counts:
        st.caption("内訳：" + "、".join(f"{k} {v}個" for k, v in d.counts.items()))
    unknown = len(d.as_is_minutes.unknown_step_ids) + len(d.to_be_minutes.unknown_step_ids)
    note = "工程ごとの「時間×実施割合」を1回ずつ足した値です（分岐や差し戻しで二重に数えません）。"
    if unknown:
        note += f"時間が不明な工程（{unknown}個）は含みません。"
    html(ui.footnote(note))


@st.cache_data(show_spinner=False, max_entries=64)
def _image(dot: str) -> bytes | None:
    return fl.render_image(dot)


def _render_chart(shown: list[tuple[str, fl.Flow, bool]]) -> None:
    """図を表示する（flow_canvas.py）。工程のない図は表示せず、案内を出す。"""
    panes = [flow_canvas.pane(title, flow, to_be) for title, flow, to_be in shown if flow.steps]
    for title, flow, _ in shown:
        if not flow.steps:
            html(ui.note(f"{title}：工程がありません。下の「工程を編集する」から追加できます。"))
    if panes:
        flow_canvas.render(panes, flow_canvas.height_for(*(flow for _, flow, _ in shown)))
    for title, flow, _ in shown:
        for warning in fl.check_flow(flow) if flow.steps else []:
            st.caption(f"注意（{title.split('　')[0]}）：{warning}")


def _render_legend() -> None:
    chips = []
    for name, color_name in fl.LEGEND:
        tag, fill, line = fl.STYLE[name]
        border = "dashed" if name == "廃止" else "solid"
        text = tag or "変更なし"
        chips.append(
            f'<span style="display:inline-block;margin:.15rem .35rem .15rem 0;padding:.1rem .5rem;font-size:.75rem;'
            f'background:{fill};border:1.5px {border} {line};border-radius:4px">{ui.esc(text)}（{color_name}）</span>'
        )
    chips.append('<span style="font-size:.75rem;color:#5F6368">バッジ：要確認＝ヒアリングで確かめられていない工程／'
                 '判断・承認＝工程の種類／点線の矢印＝差し戻し</span>')
    html("".join(chips))


def _render_detail(project: Project, pid: str, view: str, to_be: fl.Flow) -> None:
    """業務フローについてAIが確認したいことと、照合でプログラムが直したこと。

    工程ごとの詳細（担当・作業時間・使用システム・改善内容など）は、図の工程をクリックすると図の下に表示する。
    """
    if project.flow_questions:
        st.markdown("**業務フローについて、確認したいこと**")
        st.markdown("\n".join(f"- {ui.esc(q)}" for q in project.flow_questions))
        st.caption("答えが分かったら、下の表で工程を直してください（AIをもう一度呼ぶ必要はありません）。")
    if project.flow_notes:
        with st.expander(f"プログラムが照合して直したこと（{len(project.flow_notes)}件）"):
            st.markdown("\n".join(f"- {ui.esc(n)}" for n in project.flow_notes))


# ---------------------------------------------------------------
# 編集
# ---------------------------------------------------------------
def _render_editor(project: Project, target: str) -> None:
    flow = _get(project, target) or fl.Flow()
    is_to_be = target != "as_is"
    if error := st.session_state.pop(f"flow_err_{target}", None):
        st.error(error)
    rows = []
    for s in flow.steps:
        row = {"工程ID": s.id}
        for column, field in COLUMNS.items():
            if column in TO_BE_ONLY and not is_to_be:
                continue
            value = getattr(s, field)
            row[column] = {True: "はい", False: "いいえ", None: "不明"}[value] if field == "manual" else value
        rows.append(row)
    columns = ["工程ID"] + [c for c in COLUMNS if is_to_be or c not in TO_BE_ONLY]
    table = pd.DataFrame(rows, columns=columns).astype({"作業時間（分）": "float64", "実施割合（%）": "float64"})
    st.caption("表のセルを直接書き換えられます。行を追加すると最後の工程の次につながり、行を削除すると前後の工程をつなぎ直します。")
    st.data_editor(
        table, num_rows="dynamic", hide_index=True, width="stretch", disabled=["工程ID"],
        key=_key(target, project, "steps"), on_change=_on_steps, args=(project, target),
        column_config={
            "種類": st.column_config.SelectboxColumn("種類", options=KINDS, required=True),
            "変化": st.column_config.SelectboxColumn("変化", options=CHANGES, required=True),
            "手作業": st.column_config.SelectboxColumn("手作業", options=list(MANUAL), required=True),
            "作業時間（分）": st.column_config.NumberColumn("作業時間（分）", min_value=0.0),
            "実施割合（%）": st.column_config.NumberColumn("実施割合（%）", min_value=0.0, max_value=100.0),
            "要確認": st.column_config.CheckboxColumn("要確認"),
            "工程名": st.column_config.TextColumn("工程名", max_chars=config.MAX_STEP_TEXT, required=True),
        },
    )
    if flow.steps:
        c1, c2, c3 = st.columns([3, 1, 1])
        chosen = c1.selectbox("順番を動かす工程", [s.id for s in flow.steps],
                              format_func=lambda i: f"{i} {flow.step(i).name}", key=f"flow_move_{target}")
        c2.button("↑ 上へ", key=f"flow_up_{target}", on_click=_move, args=(project, target, chosen, -1))
        c3.button("↓ 下へ", key=f"flow_down_{target}", on_click=_move, args=(project, target, chosen, 1))

    st.markdown("**工程のつながり（接続）**")
    ids = [s.id for s in flow.steps]
    edges = pd.DataFrame([{"元の工程": e.source, "次の工程": e.target, "条件": e.label} for e in flow.edges],
                         columns=["元の工程", "次の工程", "条件"])
    st.data_editor(
        edges, num_rows="dynamic", hide_index=True, width="stretch",
        key=_key(target, project, "edges"), on_change=_on_edges, args=(project, target),
        column_config={
            "元の工程": st.column_config.SelectboxColumn("元の工程", options=ids, required=True),
            "次の工程": st.column_config.SelectboxColumn("次の工程", options=ids, required=True),
            "条件": st.column_config.TextColumn("条件（分岐のときだけ）", max_chars=config.MAX_STEP_TEXT),
        },
    )


def _fields(changes: dict) -> dict:
    """表の1行の変更を、工程の項目に直す。空のセルは「なし」として扱う。"""
    fields = {}
    for column, value in changes.items():
        field = COLUMNS.get(column)
        if field is None:
            continue
        empty = value is None or (isinstance(value, float) and pd.isna(value))
        if field == "manual":
            fields[field] = MANUAL.get(value)
        elif field == "minutes_per_case":
            fields[field] = None if empty else value
            fields["minutes_source"] = None if empty else "ユーザー入力"
        elif field in ("occurrence_rate",):
            fields[field] = 100.0 if empty else value
        elif field == "needs_confirmation":
            fields[field] = bool(value)
        else:
            fields[field] = "" if empty else value
    return fields


def _friendly(error: Exception) -> str:
    if isinstance(error, ValidationError):
        first = error.errors()[0]
        message = str(first.get("ctx", {}).get("error", first["msg"]))
        return f"変更を反映できませんでした：{message}（工程名は空にできず、時間は0以上、割合は0〜100です）"
    return f"変更を反映できませんでした：{error}"


def _on_steps(project: Project, target: str) -> None:
    state = st.session_state[_key(target, project, "steps")]
    flow = _get(project, target) or fl.Flow()
    ids = [s.id for s in flow.steps]
    new, pending = flow, False
    try:
        for row, changes in state.get("edited_rows", {}).items():
            fields = _fields(changes)
            if fields:
                new = fl.update_step(new, ids[int(row)], **fields)
        for row in sorted(state.get("deleted_rows", []), reverse=True):
            new = fl.delete_step(new, ids[row])
        for added in state.get("added_rows", []):
            fields = _fields(added)
            name = fields.pop("name", "")
            if not name:
                pending = True  # 工程名が入るまでは追加しない（入力の途中）
                continue
            fields.setdefault("change", "追加" if target != "as_is" else "変更なし")
            last = new.steps[-1].id if new.steps else None
            new, _ = fl.add_step(new, name, after_id=last, **fields)
    except (ValidationError, ValueError, KeyError) as e:
        st.session_state[f"flow_err_{target}"] = _friendly(e)
        return
    if new != flow:  # 工程名の入力途中の行だけなら、まだ反映しない（入力中の行を消さないため）
        _set(project, target, new)


def _on_edges(project: Project, target: str) -> None:
    state = st.session_state[_key(target, project, "edges")]
    flow = _get(project, target) or fl.Flow()
    rows = [{"source": e.source, "target": e.target, "label": e.label} for e in flow.edges]
    names = {"元の工程": "source", "次の工程": "target", "条件": "label"}
    for row, changes in state.get("edited_rows", {}).items():
        rows[int(row)].update({names[c]: (v or "") for c, v in changes.items() if c in names})
    for row in sorted(state.get("deleted_rows", []), reverse=True):
        rows.pop(row)
    for added in state.get("added_rows", []):
        values = {names[c]: v for c, v in added.items() if c in names}
        if values.get("source") and values.get("target"):
            rows.append({"label": "", **{k: v or "" for k, v in values.items()}})
        # 元と先がそろうまでは追加しない（入力の途中）
    try:
        new = fl.set_edges(flow, [fl.FlowEdge(**r) for r in rows])
    except (ValidationError, ValueError) as e:
        st.session_state[f"flow_err_{target}"] = _friendly(e)
        return
    _set(project, target, new)


def _move(project: Project, target: str, step_id: str, offset: int) -> None:
    flow = _get(project, target)
    moved = fl.move_step(flow, step_id, offset)
    if moved != flow:
        _set(project, target, moved)


def _copy_as_is(project: Project, pid: str) -> None:
    _set(project, pid, project.as_is.model_copy(deep=True))


# ---------------------------------------------------------------
# 効果試算との連動・出力
# ---------------------------------------------------------------
WEAKEST = ["AI推定", "ユーザー入力", "デモ値", "ヒアリング"]  # 弱い根拠が1つでもあれば、その出どころにする


def _render_simulation_link(project: Project, pid: str, title: str) -> None:
    """フローの時間を、効果試算の「1件あたりの作業時間」と「想定削減率」に反映する（指示書 4.7）。

    足し合わせず上書きする（同じ作業時間を二重に数えない）。件数・人件費・費用は変えない。
    """
    to_be = project.to_be.get(pid)
    if to_be is None or not to_be.steps or not project.as_is.steps:
        return
    d = fl.compare_flows(project.as_is, to_be)
    before, after = d.as_is_minutes.minutes_per_case, d.to_be_minutes.minutes_per_case
    if before <= 0:
        return
    rate = round(max(0.0, min(100.0, (1 - after / before) * 100)), 1)
    st.markdown("**効果試算への反映**")
    cases = (f"「月間処理件数」を{project.cases_per_month:g}件、" if project.cases_per_month is not None else "")
    text = (f"{title}の試算の「1件あたりの作業時間」を{before:,.1f}分、{cases}「想定削減率」を{rate:g}%"
            f"（{before:,.1f}分 → {after:,.1f}分）にします。今の値は上書きされます（足し合わせません）。")
    if project.case_unit:
        text += f"1件＝{project.case_unit}として数えています。"
    if project.cases_per_month is None:
        text += "月の件数は業務フローから分からないため変えません。試算の件数が同じ「1件」の数え方か確かめてください。"
    if d.as_is_minutes.unknown_step_ids or d.to_be_minutes.unknown_step_ids:
        text += "時間が不明な工程は含まれていないため、実際より少なく見積もっている可能性があります。"
    st.caption(text)
    st.button("この業務フローの時間を、効果試算に反映する", key=f"flow_to_sim_{pid}",
              on_click=_apply_to_simulation, args=(project, pid, before, rate))


def _source_of(*flows: fl.Flow) -> str:
    sources = {s.minutes_source for f in flows for s in f.steps if s.minutes_source}
    return next((w for w in WEAKEST if w in sources), "AI推定")


def _apply_to_simulation(project: Project, pid: str, minutes: float, rate: float) -> None:
    current = project.simulations.get(pid)
    if current is None:
        return
    minutes_source = _source_of(project.as_is)
    rate_source = "AI推定" if _source_of(project.as_is, project.to_be[pid]) == "AI推定" else "ユーザー入力"
    sources = {**current.sources, "minutes_per_case": minutes_source, "reduction_rate": rate_source}
    update = {"minutes_per_case": round(minutes, 1), "reduction_rate": rate}
    if project.cases_per_month is not None:  # 時間と件数の「1件」の意味をそろえる（片方だけ変えると効果が膨らむ）
        update["cases_per_month"] = project.cases_per_month
        sources["cases_per_month"] = project.cases_source or "AI推定"
        st.session_state[f"sim_{pid}_cases_per_month"] = project.cases_per_month
    project.simulations[pid] = current.model_copy(update={**update, "sources": sources})
    pj.mark_updated(project, "simulation", pid)
    # 試算の入力欄にも反映する（入力欄は自分の値を覚えているため）
    st.session_state[f"sim_{pid}_minutes_per_case"] = round(minutes, 1)
    st.session_state[f"sim_{pid}_reduction_rate"] = rate
    st.session_state[f"sim_{pid}_rate_slider"] = rate
    st.session_state["flow_to_sim_done"] = pid


def _render_downloads(project: Project, pid: str, to_be: fl.Flow) -> None:
    if st.session_state.pop("flow_to_sim_done", None) == pid:
        st.success("効果試算に反映しました。下の「改善効果の試算」で確認できます。")
    created_at = datetime.now(config.JST)
    c1, c2, c3 = st.columns(3)
    c1.download_button(
        "工程一覧と比較（Excel）", data=report.build_flow_report(project, created_at),
        file_name=report.flow_file_name(created_at),
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="download_flow",
    )
    as_is_png = _image(fl.to_dot(project.as_is)) if project.as_is.steps else None
    to_be_png = _image(fl.to_dot(to_be, to_be=True)) if to_be.steps else None
    if as_is_png:
        c2.download_button("現状フローの画像（PNG）", data=as_is_png, file_name="AI-BIC_現状フロー.png",
                           mime="image/png", key="download_flow_as_is")
    if to_be_png:
        c3.download_button("改善後フローの画像（PNG）", data=to_be_png, file_name=f"AI-BIC_改善後フロー_{pid}.png",
                           mime="image/png", key="download_flow_to_be")
    if not (as_is_png or to_be_png):
        st.caption("画像の出力は、Graphviz を入れた環境（公開環境）で使えます。この環境では表示だけできます。")
