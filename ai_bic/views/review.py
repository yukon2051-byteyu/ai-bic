"""改善案チャレンジレビューの画面（AI-BIC 2.0 機能D）。

画面の並び（指示書 5.4・7.1）：
1. 結論：総合判定（推奨／条件付き推奨／再検討）と、プログラムが判定を変えた理由
2. 要点：評価の要約、重大な懸念、指摘ごとの重要度・根拠（なければ「未確認」）・改善提案と「採用・保留・却下」
3. 根拠：プログラムの検証結果、評価できる点・不足している情報・代替案・次に確認すべき事項（タブ）
そのあと、採用した指摘だけを反映した改善案の作り直しと、作り直す前後の違いを表示する。

AIを呼ぶのは利用者がボタンを押したときだけ（自動で繰り返さない）。回数にも上限がある（review.py）。
"""

import streamlit as st

from ai_bic import project as pj
from ai_bic import review as rv
from ai_bic import ui
from ai_bic.errors import AIError, is_retryable, user_message
from ai_bic.project import Project

VERDICT_MARK = {"推奨": "✓ 推奨", "条件付き推奨": "△ 条件付き推奨", "再検討": "✕ 再検討"}
VERDICT_TEXT = {
    "推奨": "重大な懸念は確認されず、次の検討に進めます。",
    "条件付き推奨": "追加の確認や修正をしてから進めてください。",
    "再検討": "重大な問題があり、改善案の見直しが必要です。",
}
SEVERITY_ORDER = {"高": 0, "中": 1, "低": 2}
DECISIONS = ["保留", "採用", "却下"]


def html(text: str) -> None:
    st.markdown(text, unsafe_allow_html=True)


def render(project: Project, get_client) -> None:
    html(ui.label("Review", "改善案チャレンジレビュー"))
    st.caption("改善案を作ったAIとは別の指示文で、改善案の弱点や不足情報を批判的に評価します。"
               "試算・業務フロー・情報の充足度はプログラムでも確かめ、AIの判定が甘い場合は上書きします。")
    pids = pj.proposal_ids(project)
    proposals = project.diagnosis.proposals.proposals
    titles = {pid: f"案{no}：{p.title}" for no, (pid, p) in enumerate(zip(pids, proposals), start=1)}
    pid = st.selectbox("レビューする改善案", pids, format_func=titles.get, key="review_pid")

    if error := st.session_state.get("review_error"):
        st.error(user_message(error))
    record = project.reviews.get(pid)
    if record is None:
        _render_start(project, pid, get_client)
    else:
        reasons = pj.outdated_reasons(project, "review", pid)
        changed = [r for r in reasons if r.endswith("変更されました")]
        if changed:
            st.warning(f"このレビューは、{'・'.join(changed)}。今の内容でレビューし直してください。")
            _review_button(project, pid, get_client, "改善案をレビューし直す")
        elif reasons:
            # 元にした改善後フロー・試算が古いだけなら、レビューし直しても解決しない。先にそちらの更新を案内する
            st.info(f"{'・'.join(reasons)}。改善後フロー（業務フローの「確認した」または編集）と効果試算の値を見直してから、"
                    "レビューし直してください。")
        _render_record(project, pid, record)
        _render_revision(project, pid, get_client)
    _render_history(project, pid)
    _render_decide(project, pid, titles[pid])


def _review_button(project: Project, pid: str, get_client, label: str) -> None:
    if project.mode == "demo":
        if st.button(label.replace("（AI）", ""), key=f"review_run_{pid}", type="primary"):
            _run(lambda: rv.generate_review(get_client(), project, pid))
        st.caption("デモ：事前に作った架空のレビューです（AIは使いません）。プログラムの検証と判定の上書きは本物と同じく行います。")
        return
    if st.session_state.get("loaded"):
        html(ui.note("読み込んだ案件では、AIでレビューできません（費用の管理のため）。"))
        return
    left = rv.calls_left(project, "review")
    error = st.session_state.get("review_error")
    if left > 0 and (not error or is_retryable(error)):
        if st.button(f"{label}（AI）", key=f"review_run_{pid}", type="primary"):
            _run(lambda: rv.generate_review(get_client(), project, pid))
        st.caption(f"AIを1回呼びます（この案件で残り{left}回。失敗した回は数えません）。1分ほどかかることがあります。")
    elif left <= 0:
        html(ui.note("この案件では、AIでレビューできる回数の上限に達しました。"))


def _run(action) -> None:
    st.session_state.pop("review_error", None)
    try:
        with st.spinner("AIが処理しています…（1分ほどかかることがあります）"):
            action()
    except AIError as e:
        st.session_state["review_error"] = e.kind
    st.rerun()


def _render_start(project: Project, pid: str, get_client) -> None:
    st.markdown("6つの観点（課題との整合性・技術的実現可能性・費用対効果・業務運用リスク・セキュリティと統制・代替案の妥当性）"
                "で評価し、総合判定と、指摘ごとの重要度・根拠・改善提案を示します。")
    rules = rv.rule_findings(project, pid)
    if rules:
        with st.expander(f"レビューの前に、プログラムの検証で見つかった問題（{len(rules)}件）"):
            st.markdown("\n".join(f"- [{r.severity}・{r.viewpoint}] {ui.esc(r.message)}" for r in rules))
    _review_button(project, pid, get_client, "改善案をレビューする")


def _render_record(project: Project, pid: str, record) -> None:
    result = record.result
    # 1. 結論
    color = {"推奨": "#137333", "条件付き推奨": "#B06000", "再検討": "#C5221F"}[record.verdict]
    html(
        f'<div class="bic-card" style="border-left:4px solid {color}"><div style="font-size:1.35rem;font-weight:700;color:{color}">'
        f"{ui.esc(VERDICT_MARK[record.verdict])}</div>"
        f'<div style="margin:.25rem 0">{ui.esc(VERDICT_TEXT[record.verdict])}</div>'
        f'<div class="bic-footnote">{ui.esc(rv.DISCLAIMER)}</div></div>'
    )
    if record.verdict != record.ai_verdict:
        st.info(f"AIの判定は「{record.ai_verdict}」でしたが、プログラムの検証により「{record.verdict}」としました。\n\n"
                + "\n\n".join(record.override_reasons))
    # 2. 要点
    st.markdown(f"**評価の要約**　{ui.esc(result.summary)}")
    high = [f.title for f in result.findings if f.severity == "高"] + [r.message for r in record.rule_findings if r.severity == "高"]
    if high:
        st.error("重大な懸念事項\n\n" + "\n\n".join(f"- {ui.esc(h)}" for h in high))

    st.markdown(f"**指摘事項（{len(result.findings)}件）**　採用した指摘だけを反映して、改善案を作り直せます。")
    version = pj.version(project, "review", pid)
    order = sorted(range(len(result.findings)), key=lambda i: SEVERITY_ORDER[result.findings[i].severity])
    for no in order:
        f = result.findings[no]
        evidence = "未確認（根拠となる発言や資料を確認できませんでした）" if no in record.unverified else f"「{f.evidence}」"
        html(
            f'<div class="bic-card"><div><span class="bic-tag">重要度 {ui.esc(f.severity)}</span>'
            f'<span class="bic-tag">{ui.esc(f.viewpoint)}</span></div>'
            f"<div style=\"font-weight:700;margin:.2rem 0\">{ui.esc(f.title)}</div>"
            f"<div>{ui.esc(f.detail)}</div>"
            f'<div class="bic-footnote">根拠：{ui.esc(evidence)}</div>'
            f'<div style="margin-top:.3rem"><b>改善提案</b>　{ui.esc(f.suggestion)}</div></div>'
        )
        key = f"review_decision_{pid}_{version}_{no}"
        st.session_state.setdefault(key, record.decisions.get(no, "保留"))
        st.radio("この指摘を", DECISIONS, key=key, horizontal=True, label_visibility="collapsed",
                 on_change=_decide, args=(project, pid, no, key))

    # 3. 根拠
    if record.rule_findings:
        with st.expander(f"プログラムの検証結果（{len(record.rule_findings)}件。AIの判断ではありません）"):
            st.markdown("\n".join(f"- [{r.severity}・{r.viewpoint}] {ui.esc(r.message)}" for r in record.rule_findings))
    tabs = st.tabs(["評価できる点", "不足している情報", "代替案", "次に確認すべき事項"])
    for tab, items in zip(tabs, [result.strengths, result.missing_info, result.alternatives, result.next_checks]):
        with tab:
            st.markdown("\n".join(f"- {ui.esc(i)}" for i in items) if items else "（なし）")


def _decide(project: Project, pid: str, no: int, key: str) -> None:
    project.reviews[pid].decisions[no] = st.session_state[key]


def _render_revision(project: Project, pid: str, get_client) -> None:
    adopted = rv.adopted_findings(project, pid)
    st.markdown("**改善案の再検討**")
    if not adopted:
        st.caption("指摘を「採用」すると、その指摘だけを反映して改善案を作り直せます。元の業務課題と、採用していない部分は変えません。")
        return
    st.caption(f"採用した指摘：{len(adopted)}件（" + "、".join(f.title for f in adopted) + "）")
    if project.mode == "ai":
        left = 0 if st.session_state.get("loaded") else rv.calls_left(project, "revise")
        if left <= 0:
            html(ui.note("この案件では、改善案を作り直せる回数の上限に達しました。"))
            return
        label, note = "採用した指摘を反映して改善案を作り直す（AI）", f"AIを1回呼びます（この案件で残り{left}回）。"
    else:
        label, note = "採用した指摘を反映して改善案を作り直す", "デモ：AIを使わず、採用した指摘の改善提案を前提条件に加えた例を作ります。"
    if st.button(label, key=f"review_revise_{pid}"):
        _run(lambda: rv.revise(get_client(), project, pid))
    st.caption(note + "作り直すと、改善後フロー・効果試算・レビューは「要更新」になります。")


def _render_history(project: Project, pid: str) -> None:
    """作り直す前後の違いと、関連する結果の状態（古い結果を有効に見せない。指示書 6章）。"""
    history = project.proposal_history.get(pid)
    if not history:
        return
    no = pj.proposal_ids(project).index(pid)
    current = project.diagnosis.proposals.proposals[no]
    with st.expander(f"改善案の作り直し（{len(history)}回）：変更前と変更後", expanded=True):
        for note in project.revision_notes.get(pid, []):
            st.markdown(f"- {ui.esc(note)}")
        for name, before, after in rv.diff(history[-1], current):
            c1, c2 = st.columns(2)
            c1.markdown(f"**{name}（変更前）**")
            c1.text(before)
            c2.markdown(f"**{name}（変更後）**")
            c2.text(after)
        states = [(name, pj.status(project, kind, pid)) for kind, name in
                  [("to_be", "改善後フロー"), ("simulation", "効果試算"), ("review", "レビュー")]]
        st.caption("関連する結果の状態：" + "／".join(f"{n}：{s}" for n, s in states))


def _render_decide(project: Project, pid: str, title: str) -> None:
    """STEP 6：改善案の確定。レビューが古い・未実施の場合は、その旨を表示する。"""
    st.markdown("**改善案の確定**")
    if project.selected_proposal == pid:
        st.success(f"{title} を確定しました。")
        return
    status = pj.status(project, "review", pid)
    record = project.reviews.get(pid)
    if status != "完了":
        st.caption(f"レビューの状態：{status}。確定する前にレビューすることをおすすめします。")
    elif record and record.verdict == "再検討":
        st.caption("レビューの判定が「再検討」です。見直してから確定することをおすすめします。")
    if st.button("この改善案で確定する", key=f"review_decide_{pid}"):
        project.selected_proposal = pid
        st.rerun()
