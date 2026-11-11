"""改善効果シミュレーターの画面（AI-BIC 2.0 機能A）。

画面の並び（指示書 7.1 の3階層）。PC（1920×1280）で、どの案がどれだけ減り何か月で回収できるかを
スクロールせずに見られるよう、縦の長さを抑える（開発者の指摘で見直した）：
1. 結論：全改善案の比較表（最上部）と、案ごとの年間削減時間・年間削減効果額・投資回収期間
2. 要点：入力欄（1行4つ）、現状と改善後のグラフと3シナリオの比較（左右に並べる）
3. 根拠：業務名・計算式・入力値の出どころ、悲観と楽観の値の編集（開いたときだけ表示）

計算は simulation.py が行い、この画面は入力の受け取りと表示だけを行う。
入力を変えるたびに案件の版を更新し、あとのレビューが「再レビューが必要」と分かるようにする。
"""

from datetime import datetime

import altair as alt
import pandas as pd
import streamlit as st

from ai_bic import config, facts, report, ui
from ai_bic import project as pj
from ai_bic import simulation as sim
from ai_bic.demo_client import load_scenarios
from ai_bic.overview import tab_name
from ai_bic.project import Project

STEP = {"minutes_per_case": 1.0, "cases_per_month": 1.0, "hourly_cost": 100.0, "reduction_rate": 5.0,
        "initial_cost": 10_000.0, "monthly_running_cost": 1_000.0, "other_initial_cost": 10_000.0}
SOURCE_MARK = {"ユーザー入力": "✎ ユーザー入力", "ヒアリング": "◎ ヒアリング", "AI推定": "◇ AI推定", "デモ値": "□ デモ値",
               "本人の申告": "◎ 本人の申告（確認済み）", "添付資料": "◎ 添付資料（確認済み）"}
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
    created = False
    for no, pid in enumerate(pj.proposal_ids(project)):
        if pid in project.simulations:
            continue
        created = True
        if demo:
            values = {**demo["base"], **demo["proposals"][no]}
            project.simulations[pid] = sim.SimulationInputs(
                task_name=project.business_info.task_name, **values, sources={k: "デモ値" for k in values},
            )
        else:
            project.simulations[pid] = sim.prefill(project.business_info.task_name, project.diagnosis.analysis).inputs
        project.scenarios[pid] = sim.blank_scenarios()
        pj.mark_updated(project, "simulation", pid)
    # 本文と同じ数値（入力と照合した数値の一覧）を、試算の元の値にも使う（AI-BIC 3.1。デモの値より優先）
    if created and project.diagnosis.analysis.quantities:
        facts.apply_to_simulations(project)


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


def _restore(project: Project, pid: str, field: str) -> None:
    """手入力の値をやめて、不足情報の確認で確かめた値に戻す。"""
    facts.restore_basis(project, pid, field)
    sync_widgets(project, [(pid, field)])


def sync_widgets(project: Project, changed: list[tuple[str, str]]) -> None:
    """試算の値をプログラムで変えたあと、入力欄の表示も合わせる（コールバックの中で呼ぶ）。"""
    for pid, field in changed:
        key = _key(pid, field)
        if key in st.session_state:
            st.session_state[key] = getattr(project.simulations[pid], field)


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

    html(ui.label("Estimate", "改善効果の試算", anchor="sim"))
    st.caption("計算はプログラムが行います（AIは計算していません）。入力を変えると、すぐに計算し直します。")
    _render_overview(project, pids, proposals)

    # 案の切り替え：「案1」と、何をする案か分かる短い名前の2段（省略記号は使わない）。正式な名前は選んだあとの見出しに出す
    numbers = {pid: no for no, pid in enumerate(pids, start=1)}
    by_pid = dict(zip(pids, proposals))
    if st.session_state.get(PLAN_KEY) not in pids:
        st.session_state[PLAN_KEY] = pids[0]
    with st.container(key="plans_sim"):
        pid = st.radio("改善案", pids, key=PLAN_KEY, horizontal=True, label_visibility="collapsed",
                       format_func=lambda x: f"案{numbers[x]}",
                       captions=[ui.md_text(tab_name(by_pid[x])) for x in pids])
    html(f'<div class="bic-plan-title">案{numbers[pid]}：{ui.esc(by_pid[pid].title)}</div>')
    if by_pid[pid].genai is not None:  # 生成AIを使う案（AI-BIC 3.4）：下書きの時間だけでなく、確認・修正の時間と利用料を含める
        html(ui.note(GENAI_NOTE))
    _render_one(project, pid)

    created_at = datetime.now(config.JST)
    st.download_button(
        "試算結果をダウンロード（Excel）",
        data=report.build_simulation_report(project, created_at),
        file_name=report.simulation_file_name(created_at),
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="download_simulation",
    )
    html(ui.footnote("試算は入力した値による想定です。実測の結果ではありません。" + sim.NOTE_TIME_VALUE))


PLAN_KEY = "sim_plan"  # 試算で選んでいる改善案（P1 など）
GENAI_NOTE = ("生成AIを使う案です。想定削減率には、AIの出力を確認・修正する時間を差し引いた正味の値を入れてください"
              "（下書きの時間だけが短くなっても、確認を含めた総作業時間が減るとは限りません）。"
              "AIの利用料は月額の運用費用に入れてください。分からない場合は、小さく試して確認込みの総作業時間を測ってから入れます。")



def _render_overview(project: Project, pids: list[str], proposals) -> None:
    """全改善案の比較（指示書 3.5）。最上部に置き、どの案がどれだけ減り何か月で回収できるかを一目で見せる。"""
    items = [(f"案{no}", project.simulations[pid]) for no, pid in enumerate(pids, start=1)]
    comparison = sim.compare(items)
    rows = []
    for (title, result), proposal in zip(comparison.rows, proposals):
        rows.append({
            "改善案": f"{title}：{proposal.title}",
            "年間削減時間": _value(result.annual_saved_hours, "時間"),
            "年間削減効果額": _value(result.annual_saved_yen, "円"),
            "年間純効果額": _value(result.annual_net_yen, "円"),
            "投資回収期間": _payback(result),
            "初年度純効果額": _value(result.first_year_net_yen, "円"),
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    if comparison.differences:
        st.warning("前提条件が改善案によって異なるため、そのままでは比べられません。\n\n" + "\n\n".join(comparison.differences))
    sources = _source_summary([project.simulations[pid] for pid in pids])
    html(ui.footnote(f"標準シナリオの値です。入力値の出どころ：{sources}"))


def _source_summary(inputs: list[sim.SimulationInputs]) -> str:
    """入力値の出どころを1行にまとめる（欄ごとに表示すると縦に長くなるため。開発者の指摘）。"""
    groups: dict[str, list[str]] = {}
    for values in inputs:
        for name in sim.NUMBER_FIELDS:
            mark = SOURCE_MARK.get(values.source_of(name), "未入力")
            short = sim.NUMBER_FIELDS[name][0]
            if short not in groups.setdefault(mark, []):
                groups[mark].append(short)
    if len(groups) == 1:
        return f"{next(iter(groups))}（すべての項目）"
    return "／".join(f"{mark}：{'・'.join(names)}" for mark, names in groups.items())


def _render_one(project: Project, pid: str) -> None:
    base = project.simulations[pid]
    # 元の改善案や業務フローが変わったら、古い試算を有効に見せない（指示書 6章）
    if reasons := pj.outdated_reasons(project, "simulation", pid):
        st.warning(f"再計算が必要：{'・'.join(reasons)}。下の値が今の改善案・業務フローに合っているか見直してください。")
        if st.button("試算の値を確認した（このままでよい）", key=f"sim_ack_{pid}"):
            pj.mark_updated(project, "simulation", pid)
            st.rerun()
    # 不足情報の確認で元の値が変わったが、利用者が手入力した値を残している項目（AI-BIC 3.0）
    for field, value in project.basis_changes.get(pid, {}).items():
        st.info(f"元の値が変わりました：{sim.label(field)}は、不足情報の確認で {sim.fmt(value)} になりました。"
                f"この試算では、手入力の値（{_value(getattr(base, field), '')}）を残しています。")
        st.button("確認した値に戻す", key=f"sim_restore_{pid}_{field}", on_click=_restore, args=(project, pid, field))
    # 1. 結論。入力欄の変更はコールバックで案件に反映済みなので、入力欄より先に計算・表示してよい
    result = sim.simulate(base)
    c1, c2, c3 = st.columns(3)
    c1.metric("年間削減時間", _value(result.annual_saved_hours, "時間"))
    c2.metric("年間削減効果額", _value(result.annual_saved_yen, "円"))
    c3.metric("投資回収期間", _payback(result))
    for note in result.notes:
        st.warning(note)

    # 2. 要点：入力欄（1行4つ）と、グラフ・3シナリオの比較（左右に並べる）
    _render_inputs(project, pid, base)
    if error := st.session_state.get(f"sim_error_{pid}"):
        st.error(error)
    if result.missing:
        html(ui.note("未入力の項目：" + "、".join(result.missing) + "。入力すると計算します。"))
    scenarios = project.scenarios.get(pid) or sim.blank_scenarios()
    by_scenario = {sc.name: sim.simulate(sim.scenario_inputs(base, sc)) for sc in scenarios}
    left, right = st.columns([2, 3])  # スマートフォンでは自動で縦に並ぶ
    with left:
        _render_chart(result, by_scenario)
    with right:
        _render_scenario_table(base, scenarios, by_scenario)

    # 3. 根拠（開いたときだけ）
    with st.expander("悲観・楽観の値を変える"):
        _render_scenario_editor(project, pid, scenarios)
    with st.expander("業務名・計算式・入力値の出どころ"):
        task_key = _key(pid, "task_name")
        st.session_state.setdefault(task_key, base.task_name)
        st.text_input("業務名", key=task_key, max_chars=config.FIELD_LIMITS["task_name"],
                      on_change=_on_input, args=(project, pid, "task_name", task_key))
        for note in input_notes(project):
            html(ui.footnote(note))
        if result.formulas:
            st.markdown("\n".join(f"- {ui.esc(f)}" for f in result.formulas))
        rows = [
            {"項目": sim.label(name), "値": _value(getattr(base, name), ""),
             "出どころ": SOURCE_MARK.get(base.source_of(name), "未入力")}
            for name in sim.NUMBER_FIELDS
        ]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption("◎ ヒアリング＝お聞きした内容の数値／✎ ユーザー入力／◇ AI推定＝AIの推定（参考値）／□ デモ値＝架空のサンプル／◎ 本人の申告・添付資料＝不足情報の確認で確かめた値")


# 入力欄の並び（1行4つ × 2行。8つ目は削減率のスライダー）
INPUT_ROWS = [
    ["minutes_per_case", "cases_per_month", "hourly_cost", "reduction_rate"],
    ["initial_cost", "monthly_running_cost", "other_initial_cost", "rate_slider"],
]


def _render_inputs(project: Project, pid: str, base: sim.SimulationInputs) -> None:
    s = st.session_state
    for names in INPUT_ROWS:
        for column, name in zip(st.columns(4), names):
            with column:
                if name == "rate_slider":
                    slider_key = _key(pid, "rate_slider")
                    s.setdefault(slider_key, float(base.reduction_rate or 0.0))
                    st.slider("想定削減率（%）のスライダー", 0.0, 100.0, step=1.0, key=slider_key,
                              on_change=_on_input, args=(project, pid, "reduction_rate", slider_key))
                    continue
                widget_key = _key(pid, name)
                s.setdefault(widget_key, getattr(base, name))
                st.number_input(
                    sim.label(name), min_value=0.0, max_value=sim.NUMBER_FIELDS[name][2], step=STEP[name],
                    format="%.0f" if STEP[name] >= 1 and name != "minutes_per_case" else "%.1f",
                    key=widget_key, placeholder="未入力",
                    on_change=_on_input, args=(project, pid, name, widget_key),
                )


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
    bars = base.mark_bar(cornerRadiusEnd=4, height=16).encode(color=alt.Color("色:N", scale=None, legend=None))
    labels = base.mark_text(align="left", dx=6, color="#5F6368").encode(text="表示:N")
    st.altair_chart((bars + labels).properties(height=34 * len(rows) + 30), width="stretch")


def _render_scenario_table(base, scenarios, by_scenario) -> None:
    rows = []
    for metric, getter in [
        ("想定削減率", lambda r, v: _value(v.reduction_rate, "%")),
        ("年間削減時間", lambda r, v: _value(r.annual_saved_hours, "時間")),
        ("年間純効果額", lambda r, v: _value(r.annual_net_yen, "円")),
        ("投資回収期間", lambda r, v: _payback(r)),
        ("初年度純効果額", lambda r, v: _value(r.first_year_net_yen, "円")),
    ]:
        row = {"悲観・標準・楽観": metric}
        for sc in scenarios:
            row[sc.name] = getter(by_scenario[sc.name], sim.scenario_inputs(base, sc))
        rows.append(row)
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _render_scenario_editor(project, pid, scenarios) -> None:
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


def _payback(result: sim.SimulationResult) -> str:
    return sim.payback_text(result) if result.payback_status != "未入力" else "—"


def _value(value: float | None, unit: str) -> str:
    return "—" if value is None else f"{sim.fmt(value)}{unit}"
