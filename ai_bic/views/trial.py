"""確定した案を小さく試す画面（AI-BIC 3.0 第4段階）。計算と判定の目安はプログラム（trial.py）が行う。AIは使わない。

画面の並び：計画（試行範囲・担当・期限・想定リスク・試算の前提）→ 測定項目と結果（改善前・試算値・目標値・実測値を分けて表示）
→ 判定の目安 → 利用者の判断と理由。
"""

import streamlit as st

from ai_bic import facts, ui
from ai_bic import project as pj
from ai_bic import simulation as sim
from ai_bic import trial as tr
from ai_bic.demo_client import load_scenarios
from ai_bic.project import Project, TrialMetric
from ai_bic.quantities import basis_text

CHECK_RESULTS = ["未確認", "成功", "失敗"]
HEADERS = ["測定項目", "改善前", "試算値（参考）", "目標値（合格条件）", "実測値", "差", "状態"]
DIFF_SKIPPED = "計算しない（単位を確認）"


def html(text: str) -> None:
    st.markdown(text, unsafe_allow_html=True)


def demo_sample(project: Project) -> dict | None:
    """デモの案件で、確定した案にシナリオの試行のサンプルがあれば返す。"""
    if project.mode != "demo":
        return None
    scenario = next((sc for sc in load_scenarios() if sc["id"] == project.demo_scenario_id), None)
    sample = scenario.get("trial_sample") if scenario else None
    return sample if sample and sample["pid"] == project.selected_proposal else None


def _clear_widgets() -> None:
    """計画を作り直したとき、前の計画の入力欄の値を消す。"""
    for key in [k for k in st.session_state if str(k).startswith("trial_")]:
        del st.session_state[key]


def start(project: Project) -> None:
    """「この案を小さく試す」：確定した案から計画を作る（まだなければ）。"""
    if project.trial is None or project.trial.pid != project.selected_proposal:
        _clear_widgets()
        project.trial = tr.new_plan(project)


def _start_demo(project: Project) -> None:
    _clear_widgets()
    project.trial = tr.demo_plan(project, demo_sample(project))


# ---------------------------------------------------------------
# 表示
# ---------------------------------------------------------------
def render(project: Project) -> None:
    """試行の画面。計画を作るまでは表示しない（作る操作は、確定の欄の「この案を小さく試す」1つだけ。AI-BIC 3.1）。"""
    pid = project.selected_proposal
    plan = project.trial
    if pid is None or project.diagnosis.proposals is None or plan is None or plan.pid != pid:
        html(ui.label("Trial", "この案を小さく試す", anchor="trial", first=True))
        if pid is None:
            html(ui.note("改善案を確定すると、ここで小さく試す計画を立てられます。「改善案を選ぶ」タブで、レビューを確かめてから確定してください。"))
        else:
            html(ui.note("確定した案の試行計画はまだありません。「改善案を選ぶ」タブの「この案を小さく試す」から作ってください。"))
        return
    no = pj.proposal_ids(project).index(pid) + 1
    html(ui.label("Trial", "この案を小さく試す", anchor="trial", first=True))
    title = project.decision.proposal.title if project.decision else project.diagnosis.proposals.proposals[no - 1].title
    st.caption(f"確定した案：案{no}「{title}」。小さく試して測った結果から、展開するか・直して試すか・見送るかを決めます。"
               "計算と判定の目安はプログラムが行います（AIは使いません）。")
    if changed := facts.decision_changes(project):
        st.warning(f"確定したあとに{'・'.join(changed)}が変わっています。試行は確定した時点の内容をもとにしています。")

    if plan.sample:
        html(ui.note("デモ用の数値です（架空のサンプル）。1台分のCSVで試した場合の例を入れています。"))
    _render_plan(project)
    _render_metrics(project)
    _render_decision(project)
    if demo_sample(project) and not plan.sample:
        st.button("デモ：1台分のCSVで試したサンプルを入れる", key="trial_demo", on_click=_start_demo, args=(project,))


def _render_plan(project: Project) -> None:
    plan = project.trial
    s = st.session_state
    s.setdefault("trial_scope", plan.scope)
    s.setdefault("trial_owner", plan.owner)
    s.setdefault("trial_due", plan.due)
    with st.container(border=True):  # フォームにしない（タブを切り替えても書きかけの値が残るように。AI-BIC 3.2）
        st.markdown("**計画**")
        st.text_area("試行範囲（どこまで小さく試すか）", key="trial_scope", max_chars=300,
                     placeholder="例：1台分・1か月分のデータだけで試す")
        c1, c2 = st.columns(2)
        c1.text_input("担当", key="trial_owner", max_chars=60)
        c2.date_input("期限", key="trial_due", format="YYYY/MM/DD")
        st.button("計画を保存する", key="trial_plan_save", on_click=_save_plan, args=(project,))
    cols = st.columns(2)
    cols[0].markdown("**想定リスク（確定した案から）**\n\n" + ("\n".join(f"- {ui.esc(r)}" for r in plan.risks) or "（なし）"))
    cols[1].markdown("**試算の前提（確定した時点）**\n\n" + ("\n".join(f"- {ui.esc(a)}" for a in plan.assumptions) or "（試算の値がありません）"))


def _save_plan(project: Project) -> None:
    s = st.session_state
    plan = project.trial
    plan.scope, plan.owner, plan.due = s["trial_scope"].strip(), s["trial_owner"].strip(), s["trial_due"]
    project.updated_at = pj.now()


def _render_metrics(project: Project) -> None:
    plan = project.trial
    s = st.session_state
    st.markdown("**測定項目と結果**")
    st.caption("改善前の値・試算値（参考）・目標値（合格条件）・実測値は、別々に記録します。測っていない項目は空欄のまま（未測定）にしてください。"
               "数値は、すべて項目名の横の「単位／何あたり」（例：分／稼働日）で入れてください。")
    with st.container(border=True):  # フォームにしない（タブを切り替えても書きかけの値が残るように。AI-BIC 3.2）
        for i, m in enumerate(plan.metrics):
            k = f"trial_m{i}_"
            st.markdown(f"**{i + 1}. {ui.esc(tr.metric_label(m))}**（{m.kind}）")
            s.setdefault(k + "required", m.required)
            s.setdefault(k + "note", m.note)
            if m.kind == "数値":
                for name in ("baseline", "target", "actual"):
                    s.setdefault(k + name, getattr(m, name))
                s.setdefault(k + "direction", m.direction)
                s.setdefault(k + "unit", m.unit)
                s.setdefault(k + "per", m.per)
                if m.source:
                    st.caption(f"改善前の値の出どころ：{m.source}")
                c = st.columns([1, 1, 2])
                c[0].text_input("単位", key=k + "unit", max_chars=10, placeholder="例：分")
                c[1].text_input("何あたり", key=k + "per", max_chars=20, placeholder="例：稼働日・月",
                                help="値が何あたりか（稼働日・発生日・月・試行期間など）。合計なら空欄")
                if issue := tr.basis_issue(m):
                    c[2].warning(issue)
                    st.checkbox(f"値は「{basis_text(s[k + 'unit'], s[k + 'per']) or '単位なし'}」で正しいと確かめた",
                                key=k + "basis_ok", help="確かめるまで、この項目の差と判定は計算しません")
                c = st.columns([1, 1, 1, 1])
                c[0].number_input("改善前の値", key=k + "baseline", min_value=0.0, placeholder="未入力")
                c[1].number_input("目標値（合格条件）", key=k + "target", min_value=0.0, placeholder="未入力")
                c[2].selectbox("合格になる向き", ["以下", "以上"], key=k + "direction")
                c[3].number_input("実測値", key=k + "actual", min_value=0.0, placeholder="未測定")
                if m.estimate is not None:
                    st.caption(f"試算値（参考）：{sim.fmt(m.estimate)}{m.unit}　※試算値は目標値・実測値とは別に記録します")
            else:
                s.setdefault(k + "check_target", m.check_target)
                s.setdefault(k + "check_result", m.check_result or "未確認")
                c = st.columns([2, 1])
                c[0].text_input("合格条件", key=k + "check_target", max_chars=120)
                c[1].radio("確認の結果", CHECK_RESULTS, key=k + "check_result", horizontal=True)
            c = st.columns([3, 1])
            c[0].text_input("メモ", key=k + "note", max_chars=200)
            c[1].checkbox("必須の測定", key=k + "required", help="必須の測定が未入力のときは「判定保留」になります")
        st.button("測定結果を保存する", key="trial_metrics_save", type="primary",
                              on_click=_save_metrics, args=(project,))
    with st.expander("測定項目を追加する"):
        with st.container(border=True):  # フォームにしない（タブを切り替えても書きかけの値が残るように。AI-BIC 3.2）
            c = st.columns([3, 1, 1, 1])
            c[0].text_input("項目の名前", key="trial_add_name", max_chars=60)
            c[1].radio("種類", ["数値", "確認"], key="trial_add_kind")
            c[2].text_input("単位（数値の場合）", key="trial_add_unit", max_chars=10, placeholder="例：日")
            c[3].text_input("何あたり（数値の場合）", key="trial_add_per", max_chars=20, placeholder="例：月")
            st.button("追加する", key="trial_add_submit", on_click=_add_metric, args=(project,))
    if error := s.pop("trial_error", None):
        st.error(error)
    html(results_table(plan.metrics, plan.sample))


def _save_metrics(project: Project) -> None:
    s = st.session_state
    plan = project.trial
    updated = []
    for i, m in enumerate(plan.metrics):
        k = f"trial_m{i}_"
        values = {"required": s[k + "required"], "note": s[k + "note"].strip()}
        if m.kind == "数値":
            values.update({name: s[k + name] for name in ("baseline", "target", "actual", "direction")})
            unit, per = s[k + "unit"].strip(), s[k + "per"].strip()
            values.update(unit=unit, per=per)
            new = m.model_copy(update=values)
            if s.get(k + "basis_ok") and unit:
                values["checked_basis"] = tr.basis_of(new)  # 利用者が今の単位で正しいと確かめた
            elif tr.basis_of(new) != tr.basis_of(m) and unit and (not m.unit or not tr.has_values(m)):
                # 単位を初めて決めた・値がないうちに決めた：利用者が入れた単位をそのまま使う
                # （すでに単位のある値の単位を変えたときは、確かめ直すまで再確認が必要のまま）
                values["checked_basis"] = tr.basis_of(new)
            s[k + "basis_ok"] = False
        else:
            result = s[k + "check_result"]
            values.update(check_target=s[k + "check_target"].strip(), check_result=None if result == "未確認" else result)
        updated.append(m.model_copy(update=values))
    plan.metrics = updated
    project.updated_at = pj.now()


def _add_metric(project: Project) -> None:
    s = st.session_state
    name = s["trial_add_name"].strip()
    if not name:
        s["trial_error"] = "項目の名前を入力してください"
        return
    kind = s["trial_add_kind"]
    unit, per = (s["trial_add_unit"].strip(), s["trial_add_per"].strip()) if kind == "数値" else ("", "")
    # 値がまだない項目なので、利用者が決めた単位をそのまま確かめた単位とする
    project.trial.metrics.append(TrialMetric(name=name, kind=kind, unit=unit, per=per,
                                             checked_basis=basis_text(unit, per) if unit else None))
    project.updated_at = pj.now()
    s["trial_add_name"], s["trial_add_unit"], s["trial_add_per"] = "", "", ""  # 追加したら入力欄を空にする


def results_table(metrics: list[TrialMetric], sample: bool) -> str:
    """測定結果の表。試算値・目標値・実測値を別の列に分け、状態は記号と文字で示す。"""
    rows, issues = [], []
    for m in metrics:
        r = tr.evaluate(m)
        if m.kind == "数値":
            f = (lambda v: "—" if v is None else f"{sim.fmt(v)}{m.unit}")  # noqa: E731
            base = f(m.baseline) + (f"（{m.source}）" if m.source and m.baseline is not None else "")
            diff = DIFF_SKIPPED if r.status == tr.UNCONFIRMED else r.diff_text or "—"
            cells = [base, f(m.estimate), f"{f(m.target)}{m.direction}" if m.target is not None else "未設定",
                     f(m.actual) if m.actual is not None else "未測定", diff]
        else:
            cells = ["—", "—", m.check_target or "未設定", m.check_result or "未測定", "—"]
        if r.status == tr.UNCONFIRMED:
            issues.append(f"<li>{ui.esc(tr.metric_label(m))}：{ui.esc(r.diff_text)}</li>")
        required = "" if m.required else "（参考）"
        # 狭い画面ではカードにするため、各セルに項目名（data-label）を持たせる
        rows.append(f'<tr><td data-label="測定項目"><b>{ui.esc(tr.metric_label(m))}{required}</b></td>'
                    + "".join(f'<td data-label="{h}">{ui.esc(c)}</td>' for h, c in zip(HEADERS[1:6], cells))
                    + f'<td data-label="状態"><b>{r.mark} {ui.esc(r.status)}</b></td></tr>')
    head = "".join(f"<th>{h}</th>" for h in HEADERS)
    note = '<div class="bic-footnote">デモ用の数値です。</div>' if sample else ""
    if issues:  # 差と判定を計算しなかった項目と、その理由
        note = f'<div class="bic-footnote">差と判定を計算していない項目（単位を確認）：<ul>{"".join(issues)}</ul></div>' + note
    return f'<div class="bic-trial-wrap"><table class="bic-trial"><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>{note}'


def _render_decision(project: Project) -> None:
    plan = project.trial
    g = tr.guide(plan)
    st.markdown("**次の判断**")
    if g.hold:
        st.warning(f"判定保留：{g.reason}。")
    else:
        html(ui.note(f"判定の目安　{g.label}（{g.reason}）。決めるのはあなたです。"))
    if plan.decision:
        when = f"（{plan.decided_at:%Y-%m-%d %H:%M}）" if plan.decided_at else ""
        st.success(f"判断：{plan.decision}{when}　理由：{plan.reason}")
    with st.container(border=True):  # フォームにしない（タブを切り替えても書きかけの値が残るように。AI-BIC 3.2）
        st.radio("判断", tr.DECISIONS, key="trial_decision", index=None, horizontal=True)
        st.text_area("理由", key="trial_reason", max_chars=500, placeholder="例：時間と数量は合格したが、日付の書き方の例外が残ったため")
        st.button("判断を記録する", key="trial_decide", disabled=g.hold, on_click=_decide, args=(project,))
    if error := st.session_state.pop("trial_decision_error", None):
        st.error(error)


def _decide(project: Project) -> None:
    s = st.session_state
    try:
        tr.record_decision(project.trial, s.get("trial_decision"), s.get("trial_reason") or "")
        project.updated_at = pj.now()
    except ValueError as e:
        s["trial_decision_error"] = str(e)
