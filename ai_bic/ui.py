"""画面デザイン（配色・書体・カード）の部品。

参考にした考え方（デザイン会社の実績ページ）：
- モノクロを基本に、差し色は深い紺の1色だけ（トップの見出しと特長は、紺からスレートへの濃淡のグラデーション）
- 細めの書体と広めの字間、英字の小さな見出し＋日本語の見出し
- 大きな余白と、同じ形のカードの繰り返し、控えめな角丸

各関数はHTMLの文字列を返し、app.py が st.markdown(..., unsafe_allow_html=True) で表示する。
AIの回答や利用者の入力をHTMLに埋め込むため、すべて esc() を通して表示する
（<script> などを入力されても、ただの文字として表示される。XSS対策）。
"""

import html

from ai_bic.models import DiagnosisSummary, HearingTurn, Issue, Proposal

ACCENT = "#1B2B4B"  # 深い紺（差し色）

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Noto+Sans+JP:wght@300;400;500;700&display=swap');

:root {{
  --ink: #14161A;
  --sub: #5F6368;
  --mute: #9AA0A6;
  --line: #E4E5E8;
  --surface: #F6F6F7;
  --accent: {ACCENT};
  --accent-soft: #EEF1F6;
  --gold: #C9B37E;  /* 紺の面の上だけで使う、控えめな強調色 */
}}

/* 書体は画面全体に設定し、各部品には引き継がせる。
   Streamlit のアイコン（目のマークや▶など）は、専用のアイコン用書体で文字を絵に変えて表示している。
   部品ごとに書体を上書きすると、アイコンが「visibility」のような文字のまま表示されてしまうため、
   アイコンには書体を上書きしない（下の2つ目の指定で、アイコン用書体を明示して守る）。 */
html, body, .stApp, .stMarkdown, label, input, textarea, button {{
  font-family: "Noto Sans JP", "Hiragino Sans", "Yu Gothic", sans-serif;
}}
[data-testid="stIconMaterial"], .material-symbols-rounded {{
  font-family: "Material Symbols Rounded" !important;
}}
/* 背景：真っ白にせず、上部にごく薄い青みを入れて奥行きを出す */
.stApp {{ background: linear-gradient(180deg, #EEF1F6 0px, #F7F8FA 360px, #FFFFFF 720px); color: var(--ink); }}
.block-container {{ max-width: 880px; padding-top: 1rem; padding-bottom: 4rem; }}
/* 上部に固定された Streamlit のバー（メニューは非表示にしている）。上の余白を詰めると
   内容に重なるため、バーごと表示しない */
header[data-testid="stHeader"] {{ display: none; }}
#MainMenu, footer, [data-testid="stToolbar"] {{ visibility: hidden; }}

/* ボタン・入力欄：控えめな角丸と、広めの字間 */
.stButton > button, .stFormSubmitButton > button {{
  border-radius: 4px; letter-spacing: .06em; font-weight: 500; padding: .55rem 1.4rem;
}}
.stTextInput input, .stTextArea textarea {{ border-radius: 4px; }}
[data-testid="stForm"] {{ border: 1px solid var(--line); border-radius: 4px; padding: 1.75rem; }}

/* 見出し：英字の小さなラベル＋日本語の見出し */
.bic-label {{
  font-family: "Inter", sans-serif; font-size: 11px; font-weight: 600;
  letter-spacing: .2em; color: var(--accent); text-transform: uppercase; margin: 2.75rem 0 .35rem;
}}
.bic-h2 {{ font-size: 1.45rem; font-weight: 500; letter-spacing: .06em; margin: 0 0 1.25rem; }}
/* トップページ用の詰めた見出し（1画面に収めるため） */
.bic-label.tight {{ margin-top: .9rem; }}
.bic-h2.tight {{ font-size: 1.15rem; margin-bottom: .5rem; }}

/* トップの見出し：紺からスレートへのグラデーションの面に、白い文字 */
.bic-hero {{
  position: relative; overflow: hidden; border-radius: 6px; padding: 1.8rem 2.4rem 1.7rem; margin-bottom: .75rem;
  color: #fff; background: linear-gradient(120deg, #14213D 0%, #1B2B4B 45%, #3A5778 100%);
}}
/* 右上に、ぼかした光を重ねて奥行きを出す（装飾） */
.bic-hero::after {{
  content: ""; position: absolute; right: -120px; top: -140px; width: 380px; height: 380px; border-radius: 50%;
  background: radial-gradient(circle, rgba(140,170,210,.35) 0%, rgba(140,170,210,0) 70%);
}}
.bic-hero .en {{
  position: relative; font-family: "Inter", sans-serif; font-size: 12px; letter-spacing: .28em; color: rgba(255,255,255,.65);
}}
.bic-hero h1 {{
  position: relative; font-size: 2.2rem; font-weight: 300; letter-spacing: .08em; line-height: 1.35;
  margin: .5rem 0 .6rem; color: #fff; white-space: nowrap;
}}
.bic-hero p {{ position: relative; color: rgba(255,255,255,.8); line-height: 1.8; letter-spacing: .04em; margin: 0; font-size: .92rem; }}
@media (max-width: 720px) {{
  .bic-hero {{ padding: 1.5rem 1.3rem; }}
  .bic-hero h1 {{ font-size: 1.6rem; white-space: normal; }}
}}

/* 3つの特長：紺の濃淡で少しずつ色を変えたカード。番号を大きく見せる */
.bic-features {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin: 0 0 .5rem; }}
.bic-features > div {{ border-radius: 6px; padding: 1.1rem 1.3rem 1.2rem; color: #fff; }}
.bic-features > div:nth-child(1) {{ background: linear-gradient(160deg, #1B2B4B 0%, #2D4369 100%); }}
.bic-features > div:nth-child(2) {{ background: linear-gradient(160deg, #22385A 0%, #3B5A7E 100%); }}
.bic-features > div:nth-child(3) {{ background: linear-gradient(160deg, #2B4A63 0%, #4E7488 100%); }}
.bic-features .no {{
  font-family: "Inter", sans-serif; font-size: 2rem; font-weight: 300; line-height: 1;
  letter-spacing: .04em; color: rgba(255,255,255,.5);
}}
.bic-features h3 {{ font-size: .98rem; font-weight: 500; letter-spacing: .05em; margin: .6rem 0 .35rem; color: #fff; }}
.bic-features p {{ font-size: .82rem; color: rgba(255,255,255,.78); line-height: 1.7; margin: 0; }}
@media (max-width: 720px) {{ .bic-features {{ grid-template-columns: 1fr; }} }}

/* 注記 */
.bic-note {{ font-size: .8rem; color: var(--sub); line-height: 1.75; background: var(--surface);
  border-left: 2px solid var(--accent); padding: .65rem 1rem; margin: .75rem 0; border-radius: 0 4px 4px 0; }}
.bic-tag {{ display: inline-block; font-size: .72rem; letter-spacing: .08em; padding: .2rem .65rem;
  border: 1px solid var(--line); border-radius: 2px; color: var(--sub); margin: 0 .4rem .3rem 0; }}

/* 上部のブランドと進み具合 */
.bic-bar {{ display: flex; justify-content: space-between; align-items: baseline;
  border-bottom: 1px solid var(--line); padding-bottom: .6rem; margin-bottom: 1rem; }}
.bic-brand {{ font-family: "Inter", sans-serif; font-weight: 600; letter-spacing: .22em; font-size: .95rem; }}
.bic-brand span {{ font-weight: 400; color: var(--mute); letter-spacing: .12em; font-size: .72rem; margin-left: .8rem; }}
.bic-steps {{ display: flex; gap: 1.2rem; font-size: .78rem; letter-spacing: .06em; color: var(--mute); }}
.bic-steps .done {{ color: var(--sub); }}
.bic-steps .now {{ color: var(--accent); font-weight: 700; border-bottom: 2px solid var(--accent); padding-bottom: .2rem; }}
@media (max-width: 720px) {{ .bic-bar {{ flex-direction: column; gap: .6rem; }} .bic-steps {{ gap: .7rem; flex-wrap: wrap; }} }}

.bic-demo {{ font-size: .78rem; letter-spacing: .06em; color: var(--accent); background: var(--accent-soft);
  padding: .55rem .9rem; border-radius: 4px; margin-bottom: 1.2rem; }}

/* ヒアリングの履歴 */
.bic-qa {{ border-top: 1px solid var(--line); padding: 1.1rem 0; }}
.bic-qa .q {{ display: flex; gap: .9rem; line-height: 1.8; }}
.bic-qa .q b {{ font-family: "Inter", sans-serif; color: var(--accent); font-weight: 600; min-width: 2rem; }}
.bic-qa .a {{ margin: .6rem 0 0 2.9rem; background: var(--surface); padding: .7rem 1rem; border-radius: 4px;
  color: var(--sub); line-height: 1.8; font-size: .92rem; }}

/* 診断サマリー：3段（要点 → 最重要課題と最初の一手 → 数字）で強弱をつける。
   紺の面の上で大事な場所だけに、くすんだ金色（--gold）を細い線とラベルで使う */
.bic-summary {{
  position: relative; overflow: hidden; color: #fff; border-radius: 6px; padding: 1.9rem 2.2rem 1.6rem;
  background: linear-gradient(125deg, #14213D 0%, #1B2B4B 55%, #2F4A6E 100%);
}}
.bic-summary::after {{
  content: ""; position: absolute; right: -100px; bottom: -160px; width: 360px; height: 360px; border-radius: 50%;
  background: radial-gradient(circle, rgba(140,170,210,.25) 0%, rgba(140,170,210,0) 70%);
}}
.bic-summary > * {{ position: relative; z-index: 1; }}
.bic-summary .eyebrow {{ font-family: "Inter", sans-serif; font-size: 11px; font-weight: 600;
  letter-spacing: .24em; color: var(--gold); }}
.bic-summary .lead {{ font-size: 1.3rem; font-weight: 500; line-height: 1.75; letter-spacing: .03em; margin: .55rem 0 1.3rem; }}
.bic-summary .tiles {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
.bic-summary .tile {{ background: rgba(255,255,255,.07); border-top: 2px solid var(--gold);
  border-radius: 0 0 4px 4px; padding: .95rem 1.15rem 1.05rem; }}
.bic-summary .t-label {{ font-size: .72rem; letter-spacing: .16em; color: rgba(255,255,255,.62); }}
.bic-summary .t-main {{ font-size: 1.02rem; font-weight: 700; line-height: 1.7; margin: .35rem 0 .3rem; }}
.bic-summary .t-sub {{ font-size: .76rem; color: rgba(255,255,255,.65); letter-spacing: .04em; }}
.bic-summary .stats {{ display: grid; grid-template-columns: repeat(4, 1fr); margin-top: 1.3rem;
  border-top: 1px solid rgba(255,255,255,.16); padding-top: 1rem; }}
.bic-summary .stats div {{ border-left: 1px solid rgba(255,255,255,.16); padding-left: 1rem; }}
.bic-summary .stats div:first-child {{ border-left: none; padding-left: 0; }}
.bic-summary .n {{ display: block; font-family: "Inter", sans-serif; font-size: 1.7rem; font-weight: 300; line-height: 1.2; }}
.bic-summary .n.hot {{ color: var(--gold); font-weight: 500; }}
.bic-summary .stats span:last-child {{ font-size: .74rem; color: rgba(255,255,255,.65); letter-spacing: .06em; }}
@media (max-width: 720px) {{
  .bic-summary {{ padding: 1.5rem 1.3rem; }}
  .bic-summary .tiles {{ grid-template-columns: 1fr; }}
  .bic-summary .stats {{ grid-template-columns: 1fr 1fr; gap: .8rem 0; }}
}}

/* 課題・改善案のカード */
.bic-card {{ border: 1px solid var(--line); border-radius: 4px; padding: 1.7rem 1.9rem; margin-bottom: 1.1rem; background: #fff; }}
.bic-card .head {{ display: flex; gap: 1.2rem; align-items: flex-start; }}
.bic-card .num {{ font-family: "Inter", sans-serif; font-size: 2rem; font-weight: 400; color: var(--line);
  line-height: 1; min-width: 2.6rem; }}
.bic-card h3 {{ font-size: 1.12rem; font-weight: 500; letter-spacing: .04em; line-height: 1.6; margin: 0 0 .55rem; }}
.bic-card p {{ line-height: 1.95; margin: .6rem 0; font-size: .93rem; }}
.bic-card .k {{ font-size: .72rem; letter-spacing: .12em; color: var(--mute); margin: 1.1rem 0 .3rem; }}
.bic-badge {{ display: inline-block; font-size: .72rem; letter-spacing: .06em; padding: .18rem .6rem;
  border-radius: 2px; margin: 0 .35rem .3rem 0; border: 1px solid var(--accent); color: var(--accent); }}
.bic-badge.fill {{ background: var(--accent); color: #fff; }}
.bic-badge.gray {{ border-color: var(--line); color: var(--sub); }}
.bic-quote {{ border-left: 2px solid var(--line); padding: .15rem 0 .15rem .9rem; margin: .45rem 0;
  font-size: .9rem; line-height: 1.8; }}
.bic-quote small {{ color: var(--mute); margin-left: .5rem; }}
.bic-warn {{ font-size: .8rem; color: var(--accent); background: var(--accent-soft); padding: .5rem .8rem;
  border-radius: 4px; margin: .6rem 0; }}

.bic-ba {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1px; background: var(--line);
  border: 1px solid var(--line); border-radius: 4px; overflow: hidden; margin: 1rem 0; }}
.bic-ba div {{ background: #fff; padding: 1rem 1.1rem; font-size: .88rem; line-height: 1.85; }}
.bic-ba div.after {{ background: var(--accent-soft); }}
.bic-ba b {{ display: block; font-family: "Inter", sans-serif; font-size: .68rem; letter-spacing: .2em;
  color: var(--mute); margin-bottom: .35rem; font-weight: 600; }}
.bic-ba div.after b {{ color: var(--accent); }}
@media (max-width: 720px) {{ .bic-ba {{ grid-template-columns: 1fr; }} }}

.bic-steps-list {{ list-style: none; padding: 0; margin: .3rem 0 .4rem; }}
.bic-steps-list li {{ position: relative; padding: .35rem 0 .35rem 1.7rem; line-height: 1.75; font-size: .92rem; }}
.bic-steps-list li::before {{ content: ""; position: absolute; left: 0; top: .72rem; width: .8rem; height: .8rem;
  border: 1.5px solid var(--accent); border-radius: 2px; }}
.bic-list {{ margin: .2rem 0; padding-left: 1.2rem; font-size: .88rem; line-height: 1.85; color: var(--sub); }}
.bic-footnote {{ font-size: .78rem; color: var(--mute); line-height: 1.9; margin-top: 2rem; }}
</style>
"""

SEVERITY_CLASS = {"高": "fill", "中": "", "低": "gray"}
EVIDENCE_TEXT = {"事実": "事実：発言で確認済み", "推測": "推測：発言からの推測", "要確認": "要確認：判断材料が不足"}


def esc(text) -> str:
    """HTMLに埋め込む文字を、ただの文字として表示されるように変換する（XSS対策）。

    「$」は Streamlit で数式として解釈されるため、これも文字として扱う。
    """
    return html.escape(str(text or "")).replace("$", "&#36;")


def label(en: str, ja: str, tight: bool = False) -> str:
    """英字の小さなラベル＋日本語の見出し。tight=True は余白を詰めた版（トップページ用）。"""
    extra = " tight" if tight else ""
    return f'<div class="bic-label{extra}">{esc(en)}</div><div class="bic-h2{extra}">{esc(ja)}</div>'


def hero() -> str:
    return (
        '<div class="bic-hero"><div class="en">AI BUSINESS IMPROVEMENT CONSULTANT</div>'
        "<h1>業務改善を、根拠から始める。</h1>"
        "<p>AIが業務改善コンサルタントとして担当者にヒアリングし、"
        "業務の課題と改善案を、ご本人の発言と資料にもとづいて整理します。</p></div>"
    )


def features() -> str:
    items = [
        ("01", "1問ずつのヒアリング", "診断項目に沿って最大5問。分かっていることは聞かず、あいまいな回答は確かめます。"),
        ("02", "事実と推測を区別", "AIが挙げた根拠を、入力や添付資料と照合。確認できない内容は「要確認」にします。"),
        ("03", "費用のかからない順に提案", "ルールの見直しから検討し、新しいシステムの導入を安易に勧めません。"),
    ]
    cells = "".join(
        f'<div><div class="no">{no}</div><h3>{esc(t)}</h3><p>{esc(d)}</p></div>' for no, t, d in items
    )
    return f'<div class="bic-features">{cells}</div>'


def note(text: str) -> str:
    return f'<div class="bic-note">{esc(text)}</div>'


def topbar(steps: list[tuple[str, str]], current: str | None) -> str:
    """上部のブランド名と、診断の進み具合（N04）。"""
    keys = [k for k, _ in steps]
    now = keys.index(current) if current in keys else -1
    items = "".join(
        f'<span class="{"now" if i == now else ("done" if i < now else "")}">{esc(name)}</span>'
        for i, (_, name) in enumerate(steps)
    )
    steps_html = f'<div class="bic-steps">{items}</div>' if now >= 0 else ""
    return f'<div class="bic-bar"><div class="bic-brand">AI-BIC<span>業務改善診断</span></div>{steps_html}</div>'


def demo_banner() -> str:
    return '<div class="bic-demo">デモモード：AIは使用していません。架空の業務と、あらかじめ用意した固定の結果を表示しています。</div>'


def qa(turn: HearingTurn) -> str:
    """ヒアリングの質問と回答の1往復。"""
    answer = f'<div class="a">{esc(turn.answer)}</div>' if turn.answer is not None else ""
    return f'<div class="bic-qa"><div class="q"><b>Q{turn.turn_no}</b><span>{esc(turn.question)}</span></div>{answer}</div>'


def summary_card(
    summary: DiagnosisSummary, top_issue: Issue | None, issues: list[Issue], proposal_count: int
) -> str:
    """診断サマリー。要点 → 最重要課題と最初の一手 → 件数 の3段で、重要な点から目に入るようにする。"""
    if top_issue:
        top_main = esc(top_issue.title)
        top_sub = f"重要度 {esc(top_issue.severity)}　／　{esc(top_issue.evidence_type)}"
    else:
        top_main, top_sub = "（課題を特定できませんでした）", ""
    high = sum(1 for i in issues if i.severity == "高")
    check = sum(1 for i in issues if i.evidence_type == "要確認")
    stats = [
        (len(issues), "抽出した課題", False),
        (high, "重要度「高」", high > 0),  # 重要度「高」があれば金色で目立たせる
        (proposal_count, "改善案", False),
        (check, "要確認", False),
    ]
    stats_html = "".join(
        f'<div><span class="n{" hot" if hot else ""}">{n}</span><span>{esc(name)}</span></div>'
        for n, name, hot in stats
    )
    return (
        '<div class="bic-summary"><div class="eyebrow">DIAGNOSIS SUMMARY</div>'
        f'<p class="lead">{esc(summary.headline)}</p><div class="tiles">'
        f'<div class="tile"><div class="t-label">最重要課題</div><div class="t-main">{top_main}</div>'
        f'<div class="t-sub">{top_sub}</div></div>'
        f'<div class="tile"><div class="t-label">最初の一手</div><div class="t-main">{esc(summary.first_action)}</div>'
        '<div class="t-sub">今週から始められること</div></div></div>'
        f'<div class="stats">{stats_html}</div></div>'
    )


def issue_card(no: int, issue: Issue, viewpoints: str, sources: list[str], downgraded: bool) -> str:
    """課題のカード。sources は各根拠の出どころの表示（evidences と同じ順）。"""
    badges = (
        f'<span class="bic-badge {SEVERITY_CLASS[issue.severity]}">重要度 {esc(issue.severity)}</span>'
        f'<span class="bic-badge gray">{esc(EVIDENCE_TEXT[issue.evidence_type])}</span>'
        f'<span class="bic-badge gray">{esc(viewpoints)}</span>'
    )
    warn = (
        '<div class="bic-warn">AIは「事実」と判断しましたが、根拠となる発言を入力内容の中に'
        "確認できなかったため「要確認」としています。</div>"
        if downgraded
        else ""
    )
    quotes = "".join(
        f'<div class="bic-quote">{esc(ev.quote)}<small>{esc(src)}</small></div>'
        for ev, src in zip(issue.evidences, sources)
    ) or '<p class="bic-list">根拠となる発言はありません</p>'
    followups = (
        '<div class="k">追加で確認したいこと</div><ul class="bic-list">'
        + "".join(f"<li>{esc(q)}</li>" for q in issue.followup_questions)
        + "</ul>"
        if issue.followup_questions
        else ""
    )
    return (
        f'<div class="bic-card"><div class="head"><div class="num">{no:02d}</div><div style="flex:1">'
        f"<h3>{esc(issue.title)}</h3>{badges}{warn}<p>{esc(issue.summary)}</p>"
        f'<div class="k">判断根拠（ご本人の発言・添付資料）</div>{quotes}'
        f'<div class="k">想定原因（推測）</div><p>{esc(issue.assumed_cause)}</p>'
        f'<div class="k">業務への影響</div><p>{esc(issue.impact)}</p>'
        f'<div class="k">重要度の理由</div><p>{esc(issue.severity_reason)}</p>'
        f"{followups}</div></div></div>"
    )


def proposal_card(no: int, p: Proposal) -> str:
    def items(values: list[str]) -> str:
        return "".join(f"<li>{esc(v)}</li>" for v in values)

    why = (
        f'<div class="k">より簡単な方法では不十分な理由</div><p>{esc(p.why_not_simpler)}</p>'
        if p.why_not_simpler
        else ""
    )
    risks = f'<div class="k">想定リスク</div><ul class="bic-list">{items(p.risks)}</ul>' if p.risks else ""
    pre = (
        f'<div class="k">実施前に確認すべき事項</div><ul class="bic-list">{items(p.prerequisites)}</ul>'
        if p.prerequisites
        else ""
    )
    return (
        f'<div class="bic-card"><div class="head"><div class="num">{no:02d}</div><div style="flex:1">'
        f'<span class="bic-badge">{esc(p.category)}</span>'
        f'<span class="bic-badge gray">導入難易度 {esc(p.difficulty)}</span>'
        f'<span class="bic-badge gray">対応する課題 {esc("・".join(p.target_issue_ids))}</span>'
        f"<h3>{esc(p.title)}</h3><p>{esc(p.description)}</p>"
        f'<div class="bic-ba"><div><b>BEFORE　今</b>{esc(p.before)}</div>'
        f'<div class="after"><b>AFTER　改善後（想定）</b>{esc(p.after)}</div></div>'
        f'<div class="k">最初の一歩（今週から始められること）</div><ul class="bic-steps-list">{items(p.first_steps)}</ul>'
        f'<div class="k">推奨理由</div><p>{esc(p.reason)}</p>{why}'
        f'<div class="k">期待できる効果（想定）</div><p>{esc(p.expected_effect)}</p>'
        f"{risks}{pre}</div></div></div>"
    )


def footnote(text: str) -> str:
    return f'<div class="bic-footnote">{esc(text)}</div>'
