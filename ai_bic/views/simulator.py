"""改善効果シミュレーターの画面（AI-BIC 2.0 機能A）。

画面の並び（指示書 7.1 の3階層）：
1. 結論：年間削減時間・年間削減効果額・投資回収期間を大きく表示
2. 要点：現状と改善後の作業時間のグラフ、3シナリオの比較、改善案どうしの比較
3. 根拠：計算式と入力値の出どころ（開いたときだけ表示）

計算は simulation.py が行い、この画面は入力の受け取りと表示だけを行う。
入力を変えるたびに案件の版を更新し、あとのレビューが「再レビューが必要」と分かるようにする。
"""

from datetime import datetime

import altair as alt
import pandas as pd
import streamlit as st

from ai_bic import config, report, ui
from ai_bic import project as pj
from ai_bic import simulation as sim
from ai_bic.demo_client import load_scenarios
from ai_bic.project import Project

STEP = {"minutes_per_case": 1.0, "cases_per_month": 1.0, "hourly_cost": 100.0, "reduction_rate": 5.0,
        "initial_cost": 10_000.0, "monthly_running_cost": 1_000.0, "other_initial_cost": 10_000.0}
SOURCE_MARK = {"ユーザー入力": "✎ ユーザー入力", "ヒアリング": "◎ ヒアリング", "AI推定": "◇ AI推定", "デモ値": "□ デモ値"}
GRAY, NAVY = "#9AA0A6", ui.ACCENT  # 現状（比べる基準）は灰色、改善後は差し色


def html(text: str) -> None:
    st.markdown(text, unsafe_allow_html=True)


def _key(pid: str, name: str) -> str:
    return f"sim_{pid}_{name}"


def demo_values(project: Project) -> dict | None:
    """デモの案件なら、シナリオに用意した試算のデモ値（架空のサンプル）。なければ None。"""
    if project.mode != "demo":
        return None
    scenario = next((sc for sc in load_scenarios() if sc["id"] == project.demo_scenario_id), None)
    return scenario.get("simulation") if scenario else None


def ensure_inputs(project: Project) -> None:
    """改善案ごとの試算の入力値を用意する（初めて表示したときだけ初期値を入れる）。

    AI診断：診断結果の計算式から読み取れる値だけ（出どころ「ヒアリング」）。
    デモ：シナリオに用意したデモ値をすべて入れる（出どころ「デモ値」。架空のサンプルと明示する）。
    """
    demo = demo_values(project)
    for no, pid in enumerate(pj.proposal_ids(project)):
        if pid in project.simulations:
            continue
        if demo:
            values = {**demo["base"], **demo["proposals"][no]}
            project.simulations[pid] = sim.SimulationInputs(
                task_name=project.business_info.task_name, **values, sources={k: "デモ値" for k in values},
            )
        else:
            project.simulations[pid] = sim.prefill(project.business_info.task_name, project.diagnosis.analysis).inputs
        project.scenarios[pid] = sim.blank_scenarios()
        pj.mark_updated(project, "simulation", pid)


def input_notes(project: Project) -> list[str]:
    """入力欄の上に出す、初期値の出どころの説明。"""
    demo = demo_values(project)
    if demo:
        return ["□ デモ値：" + demo["note"] + "値を変えると、結果がすぐに変わる様子を試せます。"]
    return sim.prefill(project.business_info.task_name, project.diagnosis.analysis).notes


# ---------------------------------------------------------------
# 入力の受け取り（入力欄を変えたときに呼ばれる）
# ---------------------------------------------------------------
def _update(project: Project, pid: str, name: str, value) -> None:
    """入力値を1つ変える。変えた値の出どころは「ユーザー入力」になる。"""
    old = project.simulations[pid]
    sources = {k: v for k, v in old.sources.items() if k != name}
    if value is not None and name in sim.NUMBER_FIELDS:
        sources[name] = "ユーザー入力"
    new, errors = sim.make_inputs({**old.model_dump(), name: value, "sources": sources})
    if errors:  # 入力欄で範囲を制限しているため通常は起きないが、念のため値を変えずに知らせる
        st.session_state[f"sim_error_{pid}"] = "、".join(errors.values())
        return
    st.session_state.pop(f"sim_error_{pid}", None)
    project.simulations[pid] = new
    pj.mark_updated(project, "simulation", pid)


def _on_input(project: Project, pid: str, name: str, widget_key: str) -> None:
    value = st.session_state[widget_key]
    _update(project, pid, name, value)
    if name == "reduction_rate":  # 削減率は入力欄とスライダーの両方で変えられるため、もう一方にも反映する
        other = _key(pid, "rate_slider") if widget_key == _key(pid, name) else _key(pid, name)
        st.session_state[other] = float(value) if value is not None else (0.0 if other.endswith("slider") else None)


def _on_scenarios(project: Project, pid: str) -> None:
    """シナリオの表を編集したとき。空欄は「自動」に戻す。"""
    edits = st.session_state[_key(pid, "scenarios")]["edited_rows"]
    rows = [sc for sc in project.scenarios[pid] if sc.name != "標準"]
    updated = {sc.name: sc.model_dump() for sc in project.scenarios[pid]}
    fields = {sim.label(f): f for f in sim.SCENARIO_FIELDS}  # 表の列名（画面の名前）→ 項目名
    for row_no, changes in edits.items():
        name = rows[int(row_no)].name
        for column, value in changes.items():
            if column in fields:
                empty = value is None or (isinstance(value, float) and pd.isna(value))
                updated[name][fields[column]] = None if empty else value
    try:
        project.scenarios[pid] = [sim.Scenario(**updated[n]) for n in sim.SCENARIO_NAMES]
    except ValueError:
        st.session_state[f"sim_error_{pid}"] = "シナリオの値は0以上（削減率は100以下）で入力してください"
        return
    st.session_state.pop(f"sim_error_{pid}", None)
    pj.mark_updated(project, "simulation", pid)


# ---------------------------------------------------------------
# 表示
# ---------------------------------------------------------------
def render(project: Project) -> None:
    ensure_inputs(project)
    proposals = project.diagnosis.proposals.proposals
    pids = pj.proposal_ids(project)

    html(ui.label("Estimate", "改善効果の試算"))
    st.markdown(
        "改善案ごとに、作業時間と費用の前提を入れると、削減効果と投資回収期間を**プログラムで計算**します"
        "（AIは計算していません）。入力を変えると、結果はすぐに更新されます。"
    )
    labels = [f"案{no}" for no in range(1, len(pids) + 1)]
    tabs = st.tabs(labels + (["改善案の比較"] if len(pids) > 1 else []))
    for tab, pid, proposal, short in zip(tabs, pids, proposals, labels):
        with tab:
            html(f'<div class="bic-footnote">{ui.esc(short)}：{ui.esc(proposal.title)}</div>')
            _render_one(project, pid)
    if len(pids) > 1:
        with tabs[-1]:
            _render_comparison(project, pids, proposals)

    created_at = datetime.now(config.JST)
    st.download_button(
        "試算結果をダウンロード（Excel）",
        data=report.build_simulation_report(project, created_at),
        file_name=report.simulation_file_name(created_at),
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="download_simulation",
    )
    html(ui.footnote("試算は入力した値による想定です。実測の結果ではありません。" + sim.NOTE_TIME_VALUE))


def _render_one(project: Project, pid: str) -> None:
    base = project.simulations[pid]
    for note in input_notes(project):
        html(ui.footnote(note))
    _render_inputs(project, pid, base)
    if error := st.session_state.get(f"sim_error_{pid}"):
        st.error(error)
    base = project.simulations[pid]  # 入力で変わった値を使う
    result = sim.simulate(base)

    # 1. 結論
    c1, c2, c3 = st.columns(3)
    c1.metric("年間削減時間", _value(result.annual_saved_hours, "時間"))
    c2.metric("年間削減効果額", _value(result.annual_saved_yen, "円"))
    c3.metric("投資回収期間", sim.payback_text(result) if result.payback_status != "未入力" else "—")
    if result.missing:
        html(ui.note("未入力の項目：" + "、".join(result.missing) + "。入力すると計算します。"))
    for note in result.notes:
        st.warning(note)

    # 2. 要点
    scenarios = project.scenarios.get(pid) or sim.blank_scenarios()
    by_scenario = {sc.name: sim.simulate(sim.scenario_inputs(base, sc)) for sc in scenarios}
    _render_chart(result, by_scenario)
    _render_scenarios(project, pid, base, scenarios, by_scenario)

    # 3. 根拠
    with st.expander("計算式と前提条件（入力値の出どころ）"):
        if result.formulas:
            st.markdown("\n".join(f"- {ui.esc(f)}" for f in result.formulas))
        rows = [
            {"項目": sim.label(name), "値": _value(getattr(base, name), ""), "出どころ": SOURCE_MARK.get(base.source_of(name), "未入力")}
            for name in sim.NUMBER_FIELDS
        ]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption("出どころ：◎ ヒアリング＝お聞きした内容の数値／✎ ユーザー入力／◇ AI推定＝AIの推定（参考値）／□ デモ値＝架空のサンプル")


def _render_inputs(project: Project, pid: str, base: sim.SimulationInputs) -> None:
    s = st.session_state
    task_key = _key(pid, "task_name")
    s.setdefault(task_key, base.task_name)
    st.text_input("業務名", key=task_key, max_chars=config.FIELD_LIMITS["task_name"],
                  on_change=_on_input, args=(project, pid, "task_name", task_key))
    names = list(sim.NUMBER_FIELDS)
    for row_start in range(0, len(names), 3):  # 1行に3つまで（狭い画面で入力欄の名前が切れないように）
        columns = st.columns(3)
        for column, name in zip(columns, names[row_start:row_start + 3]):
            widget_key = _key(pid, name)
            s.setdefault(widget_key, getattr(base, name))
            _, _, max_value = sim.NUMBER_FIELDS[name]
            with column:
                st.number_input(
                    sim.label(name), min_value=0.0, max_value=max_value, step=STEP[name],
                    format="%.0f" if STEP[name] >= 1 and name != "minutes_per_case" else "%.1f",
                    key=widget_key, placeholder="未入力",
                    on_change=_on_input, args=(project, pid, name, widget_key),
                )
                source = base.source_of(name)
                st.caption(SOURCE_MARK[source] if source else "未入力")
    slider_key = _key(pid, "rate_slider")
    s.setdefault(slider_key, float(base.reduction_rate or 0.0))
    st.slider("想定削減率（%）をスライダーで変える", 0.0, 100.0, step=1.0, key=slider_key,
              on_change=_on_input, args=(project, pid, "reduction_rate", slider_key))


def _render_chart(result: sim.SimulationResult, by_scenario: dict[str, sim.SimulationResult]) -> None:
    """現状と改善後の月間作業時間（棒グラフ）。1つの量（時間）だけを、同じ軸で比べる。"""
    if result.monthly_hours is None:
        return
    rows = [{"区分": "現状", "月間作業時間": result.monthly_hours, "色": GRAY}]
    for name in sim.SCENARIO_NAMES:
        after = by_scenario[name].after_monthly_hours
        if after is not None:
            rows.append({"区分": f"改善後（{name}）", "月間作業時間": after, "色": NAVY})
    if len(rows) == 1:
        return
    data = pd.DataFrame(rows)
    data["表示"] = data["月間作業時間"].map(lambda v: f"{sim.fmt(v)}時間")
    base = alt.Chart(data).encode(
        y=alt.Y("区分:N", sort=None, title=None, axis=alt.Axis(labelLimit=200)),
        x=alt.X("月間作業時間:Q", title="月間作業時間（時間）", axis=alt.Axis(grid=True, gridOpacity=0.4)),
        tooltip=[alt.Tooltip("区分:N"), alt.Tooltip("表示:N", title="月間作業時間")],
    )
    bars = base.mark_bar(cornerRadiusEnd=4, height=18).encode(color=alt.Color("色:N", scale=None, legend=None))
    labels = base.mark_text(align="left", dx=6, color="#5F6368").encode(text="表示:N")
    st.altair_chart((bars + labels).properties(height=40 * len(rows) + 30), width="stretch")


def _render_scenarios(project, pid, base, scenarios, by_scenario) -> None:
    st.markdown("**悲観・標準・楽観の比較**")
    factors = "、".join(
        f"{n}：削減率×{config.SCENARIO_FACTORS[n][0]:g}・費用×{config.SCENARIO_FACTORS[n][1]:g}" for n in ("悲観", "楽観")
    )
    st.caption(
        f"標準は上の入力値です。悲観・楽観の空欄は「自動」で、標準の値に一定の倍率をかけた仮の幅です（{factors}）。"
        "自社の事情に合わせて値を入れてください。"
    )
    editable = [sc for sc in scenarios if sc.name != "標準"]
    # 空欄（自動）は NaN にして、表に「None」と出さず空のまま表示する
    table = pd.DataFrame(
        [{"シナリオ": sc.name, **{sim.label(f): getattr(sc, f) for f in sim.SCENARIO_FIELDS}} for sc in editable]
    ).astype({sim.label(f): "float64" for f in sim.SCENARIO_FIELDS})
    st.data_editor(
        table, hide_index=True, width="stretch", key=_key(pid, "scenarios"), disabled=["シナリオ"],
        column_config={
            sim.label(f): st.column_config.NumberColumn(
                sim.label(f), min_value=0.0, max_value=sim.NUMBER_FIELDS[f][2], help="空欄は自動（標準×倍率）"
            )
            for f in sim.SCENARIO_FIELDS
        },
        on_change=_on_scenarios, args=(project, pid),
    )

    rows = []
    for metric, getter in [
        ("想定削減率", lambda r, v: _value(v.reduction_rate, "%")),
        ("年間削減時間", lambda r, v: _value(r.annual_saved_hours, "時間")),
        ("年間純効果額", lambda r, v: _value(r.annual_net_yen, "円")),
        ("初期投資額", lambda r, v: _value(r.initial_investment, "円")),
        ("投資回収期間", lambda r, v: sim.payback_text(r) if r.payback_status != "未入力" else "—"),
        ("初年度純効果額", lambda r, v: _value(r.first_year_net_yen, "円")),
    ]:
        row = {"": metric}
        for sc in scenarios:
            row[sc.name] = getter(by_scenario[sc.name], sim.scenario_inputs(base, sc))
        rows.append(row)
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _render_comparison(project: Project, pids: list[str], proposals) -> None:
    """改善案どうしの比較（指示書 3.5）。前提条件が違う場合は、表の前に知らせる。"""
    items = [(f"案{no}", project.simulations[pid]) for no, pid in enumerate(pids, start=1)]
    comparison = sim.compare(items)
    if comparison.differences:
        st.warning("前提条件が改善案によって異なるため、そのままでは比べられません。\n\n" + "\n\n".join(comparison.differences))
    else:
        html(ui.footnote("どの改善案も、現状の業務（業務名・作業時間・件数・人件費）は同じ前提で試算しています。"))
    rows = []
    for (title, result), proposal in zip(comparison.rows, proposals):
        rows.append({
            "改善案": f"{title}：{proposal.title}",
            "年間削減時間": _value(result.annual_saved_hours, "時間"),
            "年間純効果額": _value(result.annual_net_yen, "円"),
            "初期投資額": _value(result.initial_investment, "円"),
            "投資回収期間": sim.payback_text(result) if result.payback_status != "未入力" else "—",
            "初年度純効果額": _value(result.first_year_net_yen, "円"),
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption("標準シナリオの値で比べています。")


def _value(value: float | None, unit: str) -> str:
    return "—" if value is None else f"{sim.fmt(value)}{unit}"
