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

from ai_bic import config
from ai_bic.models import Evidence, HearingItem, HearingTurn, Issue, ItemStatus, Proposal

ACCENT = "#1B2B4B"  # 深い紺（差し色）

CSS = f"""
<style class="bic-css">
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Noto+Sans+JP:wght@300;400;500;700&display=swap');

:root {{
  --ink: #14161A;
  --sub: #5F6368;
  --mute: #9AA0A6;
  --line: #E4E5E8;
  --surface: #F6F6F7;
  --accent: {ACCENT};
  --accent-soft: #EEF1F6;
  --gold: #C9B37E;  /* 金色：短い線や小さな装飾に使う */
  --gold-deep: #A88B4E;  /* 白い背景の上で、文字や細い線として使う少し濃い金色 */
  --gold-soft: #F5EFE2;  /* 金色の面：デモの入口・補足案内・注目ポイント。上の文字は濃紺 */
  --gold-soft-strong: #EBDFC3;  /* 金色の面を一段濃くした色（ホバー） */
  --page: #F3F5F8;  /* ページの背景（薄い青灰色）。白い作業面との区切りをはっきりさせる */
  --card: #FFFFFF;  /* 入力・結果の作業面 */
  --line-blue: #C8D2E0;  /* 青灰色の枠 */
  --on-navy-sub: #D9E2F0;  /* 濃紺の面の上の補足の文字 */
  --notice-bg: #E7EDF5;  /* ご利用上の注意の面（淡いブルーグレー）。デモの淡い金色と区別する */
  --notice-line: #647C9B;  /* ご利用上の注意の左端の線（落ち着いた青） */
}}
/* 色の役割（開発者の指示）：
   濃紺＝ページの見出し・現在のステップ・主操作のボタン。濃紺の広い面は、画面の起点（TOPのAI診断・診断サマリー）だけ
   金色＝短い線・小さな装飾。面で使うときは淡い金色（--gold-soft）
   作業エリア＝薄い青灰色の背景（--page）の上に、白い入力・結果の面（--card）。エラー・警告・成功は Streamlit の既定の色 */

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
/* 背景：薄い青灰色。白い作業面（フォーム・カード・表）との区切りをはっきりさせる */
.stApp {{ background: var(--page); color: var(--ink); }}
.block-container {{ max-width: 880px; padding-top: .75rem; padding-bottom: 4rem; }}
/* 最上部の余白を詰める（開発者の指摘：今の3分の1でよい）。
   Streamlit の版によって中身の入れ物の名前が違うため、両方に指定し、既定の大きな余白を上書きする */
[data-testid="stMainBlockContainer"], .block-container {{ padding-top: .4rem !important; }}
/* この見た目の設定（style）だけを入れた部品は画面に何も出さないが、後ろに要素間のすき間ができるため隠す
   （隠しても、中の style は効く） */
[data-testid="stElementContainer"]:has(style.bic-css) {{ display: none; }}
/* 上部に固定された Streamlit のバー（メニューは非表示にしている）。上の余白を詰めると
   内容に重なるため、バーごと表示しない */
header[data-testid="stHeader"] {{ display: none; }}
#MainMenu, footer, [data-testid="stToolbar"] {{ visibility: hidden; }}

/* ボタン・入力欄：控えめな角丸と、広めの字間 */
.stButton > button, .stFormSubmitButton > button {{
  border-radius: 4px; letter-spacing: .06em; font-weight: 500; padding: .55rem 1.4rem;
}}
.stTextInput input, .stTextArea textarea {{ border-radius: 4px; }}
[data-testid="stForm"] {{ border: 1px solid var(--line-blue); border-radius: 6px; padding: 1.75rem; background: var(--card); }}
[data-testid="stExpander"] details {{ background: var(--card); border-color: var(--line-blue); border-radius: 6px; }}
[data-testid="stDataFrame"], [data-testid="stTable"] {{ background: var(--card); border-radius: 4px; }}
/* 入力欄は白い面と青灰色の枠に。Streamlit 1.66 は入力欄の背景に補助の背景色（#F6F6F7）を使い、
   ページ背景（#F3F5F8）とほぼ同じ色で見分けにくかった（画面を撮影して確認）。部品の名前は実際の画面で調べた */
[data-testid="stTextInputRootElement"], [data-testid="stTextAreaRootElement"], [data-testid="stNumberInputContainer"],
.react-aria-ComboBox > div, [data-testid="stFileUploaderDropzone"] {{
  background: var(--card) !important; border: 1px solid var(--line-blue) !important;
}}
/* キーボードで入力欄を選んだときは、金色の枠で示す（濃紺のカードの上でも見える） */
[data-testid="stTextInputRootElement"]:focus-within, [data-testid="stTextAreaRootElement"]:focus-within,
[data-testid="stNumberInputContainer"]:focus-within, .react-aria-ComboBox > div:focus-within {{
  border-color: var(--accent) !important; box-shadow: 0 0 0 2px var(--gold) !important;
}}
/* 進み具合の棒の土台：ページ背景と区別できる青灰色 */
[data-testid="stProgressBarTrack"] {{ background: var(--line-blue) !important; }}
/* キーボードで操作したとき、いま選んでいる部品が分かるようにする */
.stButton > button:focus-visible, .stFormSubmitButton > button:focus-visible, .stDownloadButton > button:focus-visible {{
  outline: 2px solid var(--gold-deep); outline-offset: 2px;
}}

/* 見出し：英字の小さなラベル＋日本語の見出し */
.bic-label {{
  font-family: "Inter", sans-serif; font-size: 11px; font-weight: 600;
  letter-spacing: .2em; color: var(--accent); text-transform: uppercase; margin: 2.75rem 0 .35rem;
  display: flex; align-items: center; gap: .6rem;
}}
.bic-label::before {{ content: ""; width: 18px; height: 2px; background: var(--gold); }}
.bic-h2 {{ font-size: 1.45rem; font-weight: 500; letter-spacing: .06em; margin: 0 0 1.25rem; }}
/* トップページ用の詰めた見出し（1画面に収めるため） */
.bic-label.tight {{ margin-top: .7rem; }}
.bic-label.first {{ margin-top: .9rem; }}

/* 結果画面の4つのタブ（AI-BIC 3.2）：選択中のタブは白い面・濃紺の文字・金色の下線。スマホでは2段に折り返し、4つとも見える */
.st-key-result_tab [role="tablist"] {{ gap: 4px; border-bottom: 1px solid var(--line-blue); flex-wrap: wrap; }}
.st-key-result_tab [data-testid="stTab"] {{ padding: .55rem 1.1rem; border: 1px solid transparent; border-bottom: none;
  border-radius: 6px 6px 0 0; }}
.st-key-result_tab [data-testid="stTab"] p {{ font-size: .98rem; font-weight: 600; color: var(--sub); }}
.st-key-result_tab [data-testid="stTab"][aria-selected="true"] {{ background: var(--card); border-color: var(--line-blue);
  box-shadow: inset 0 -3px 0 var(--gold); }}
.st-key-result_tab [data-testid="stTab"][aria-selected="true"] p {{ color: var(--accent); }}
@media (max-width: 720px) {{
  .st-key-result_tab [role="tablist"] {{ overflow: visible; }}
  .st-key-result_tab [data-testid="stTab"] {{ flex: 1 1 45%; justify-content: center; padding: .5rem .4rem; }}
}}
.bic-h2.tight {{ font-size: 1.15rem; margin-bottom: .5rem; }}

/* トップの見出し：紺からスレートへのグラデーションの面に、白い文字 */
.bic-hero {{
  position: relative; overflow: hidden; border-radius: 6px; padding: 1.3rem 2.4rem 1.2rem; margin-bottom: .6rem;
  color: #fff; background: linear-gradient(120deg, #14213D 0%, #1B2B4B 45%, #3A5778 100%);
}}
/* 右上に、ぼかした光を重ねて奥行きを出す（装飾） */
.bic-hero::after {{
  content: ""; position: absolute; right: -120px; top: -140px; width: 380px; height: 380px; border-radius: 50%;
  background: radial-gradient(circle, rgba(140,170,210,.35) 0%, rgba(140,170,210,0) 70%);
}}
.bic-hero .en {{
  position: relative; font-family: "Inter", sans-serif; font-size: 12px; letter-spacing: .28em; color: var(--gold);
}}
.bic-hero h1 {{
  position: relative; font-size: 2.2rem; font-weight: 300; letter-spacing: .08em; line-height: 1.35;
  margin: .3rem 0 .35rem; color: #fff; white-space: nowrap;
}}
.bic-hero p {{ position: relative; color: rgba(255,255,255,.8); line-height: 1.65; letter-spacing: .04em; margin: 0; font-size: .92rem; }}
@media (max-width: 720px) {{
  .bic-hero {{ padding: 1.1rem 1.2rem; }}
  .bic-hero h1 {{ font-size: 1.6rem; white-space: normal; }}
}}

/* 3つの特長：紺の濃淡で少しずつ色を変えたカード。番号を大きく見せる */
.bic-features {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin: 0 0 .4rem; }}
.bic-features > div {{ border-radius: 6px; padding: .8rem 1.15rem .85rem; color: #fff; }}
.bic-features > div:nth-child(1) {{ background: linear-gradient(160deg, #1B2B4B 0%, #2D4369 100%); }}
.bic-features > div:nth-child(2) {{ background: linear-gradient(160deg, #22385A 0%, #3B5A7E 100%); }}
.bic-features > div:nth-child(3) {{ background: linear-gradient(160deg, #2B4A63 0%, #4E7488 100%); }}
.bic-features .no {{
  font-family: "Inter", sans-serif; font-size: 1.6rem; font-weight: 300; line-height: 1;
  letter-spacing: .04em; color: var(--gold);
}}
.bic-features h3 {{ font-size: .98rem; font-weight: 500; letter-spacing: .05em; margin: .4rem 0 .25rem; color: #fff; }}
.bic-features p {{ font-size: .82rem; color: rgba(255,255,255,.78); line-height: 1.6; margin: 0; }}
@media (max-width: 720px) {{
  .bic-features {{ grid-template-columns: 1fr; gap: 8px; }}
  .bic-features > div {{ display: grid; grid-template-columns: 2.4rem 1fr; column-gap: .6rem; padding: .7rem 1rem; }}
  .bic-features .no {{ grid-row: span 2; font-size: 1.4rem; padding-top: .15rem; }}
  .bic-features h3 {{ margin: 0 0 .2rem; }}
}}

/* TOPの入口のカード（AI診断＝濃紺、デモで試す＝淡い金色）。
   Streamlit の入れ物に key を付けると「st-key-（key）」というクラス名になるため、それを目印にカードの中だけ上書きする */
.st-key-entry_ai, .st-key-entry_demo {{
  border-radius: 8px; padding: 22px 24px; height: 100%; gap: .55rem;
}}
.st-key-entry_ai {{ background: var(--accent); color: #FFFFFF; }}
.st-key-entry_demo {{ background: var(--gold-soft); color: var(--accent); border: 1px solid #E6DAC0; }}
/* 2枚のカードの高さをそろえ、開始ボタンを下端にそろえる */
[data-testid="stHorizontalBlock"]:has(.st-key-entry_ai) {{ align-items: stretch; }}
[data-testid="stColumn"]:has(.st-key-entry_ai) > div, [data-testid="stColumn"]:has(.st-key-entry_demo) > div {{ height: 100%; }}
.st-key-entry_ai [data-testid="stElementContainer"]:has(.stButton),
.st-key-entry_demo [data-testid="stElementContainer"]:has(.stButton) {{ margin-top: auto; }}
.st-key-entry_ai .bic-label, .st-key-entry_ai .bic-h2 {{ color: #FFFFFF; margin-top: 0; }}
.st-key-entry_demo .bic-label, .st-key-entry_demo .bic-h2 {{ color: var(--accent); margin-top: 0; }}
.st-key-entry_ai [data-testid="stWidgetLabel"] p {{ color: var(--on-navy-sub); }}
.st-key-entry_demo [data-testid="stWidgetLabel"] p {{ color: var(--accent); }}
/* 入力欄・選択欄：白い面に濃紺の文字とアイコン（パスワードの表示切り替えのアイコンも濃紺） */
.st-key-entry_ai input, .st-key-entry_demo [data-baseweb="select"] > div {{ color: var(--accent) !important; }}
.st-key-entry_ai [data-testid="stTextInputRootElement"], .st-key-entry_ai [data-baseweb="input"] {{ background: var(--card) !important; }}
.st-key-entry_ai [data-testid="stTextInputRootElement"] button, .st-key-entry_ai [data-testid="stTextInputRootElement"] svg,
.st-key-entry_demo [data-baseweb="select"] svg {{ color: var(--accent) !important; fill: var(--accent) !important; }}
/* 開始ボタン：AI診断は白い面に濃紺の文字、デモは濃紺の枠と文字（ホバーで淡い金色を一段濃く） */
.st-key-entry_ai .stButton > button {{ background: var(--card); color: var(--accent); border: 1px solid var(--card); }}
.st-key-entry_ai .stButton > button:hover {{ background: var(--gold-soft); color: var(--accent); border-color: var(--gold-soft); }}
.st-key-entry_ai .stButton > button:focus-visible {{ outline: 2px solid var(--gold); outline-offset: 2px; }}
.st-key-entry_demo .stButton > button {{ background: transparent; color: var(--accent); border: 1px solid var(--accent); }}
.st-key-entry_demo .stButton > button:hover {{ background: var(--gold-soft-strong); color: var(--accent); border-color: var(--accent); }}
.st-key-entry_ai .bic-note {{ background: rgba(255,255,255,.08); color: var(--on-navy-sub); border-left-color: var(--gold); }}
/* 保存した案件の読み込み：開始カードより控えめな、白い横長の開閉行 */
.st-key-entry_resume [data-testid="stExpander"] details {{ background: var(--card); border: 1px solid var(--line-blue); }}
@media (max-width: 720px) {{ .st-key-entry_ai, .st-key-entry_demo {{ padding: 18px 16px; }} }}

/* 試算の改善案の切り替え（ラジオボタンをタブの形にする）：「案1」と短い名前の2段。
   選択中＝濃紺の面に白い文字、下端に金色の細い線（金色は選択中の印）。未選択＝白い面に濃紺の文字、青灰色の枠 */
[class*="st-key-plans_"] [data-testid="stElementContainer"] {{ width: 100%; }}
[class*="st-key-plans_"] [role="radiogroup"] {{ display: grid; grid-auto-flow: column; grid-auto-columns: minmax(0, 1fr);
  gap: 6px; width: 100%; }}
[class*="st-key-plans_"] [role="radiogroup"] > div {{ position: relative; display: flex; flex-direction: column; justify-content: center;
  margin: 0; padding: .5rem .8rem .55rem; min-width: 0; background: var(--card); border: 1px solid var(--line-blue);
  border-radius: 4px 4px 0 0; cursor: pointer; }}
[class*="st-key-plans_"] [role="radiogroup"] > div:hover {{ border-color: var(--accent); }}
[class*="st-key-plans_"] [data-testid="stRadioOption"] {{ margin: 0; padding: 0; width: 100%; cursor: pointer; }}
/* 短い名前の部分を押しても選べるよう、選択肢の押せる範囲をタブ全体に広げる */
[class*="st-key-plans_"] [data-testid="stRadioOption"]::before {{ content: ""; position: absolute; inset: 0; z-index: 1; }}
[class*="st-key-plans_"] [data-testid="stRadioOption"] > div > div:first-child {{ display: none; }}  /* 丸い選択印は使わない */
[class*="st-key-plans_"] [data-testid="stRadioOption"] p {{ font-size: .75rem; letter-spacing: .08em; color: var(--sub); margin: 0; }}
[class*="st-key-plans_"] [data-testid="stRadioCaption"] {{ padding: 0; margin: .1rem 0 0; }}
[class*="st-key-plans_"] [data-testid="stCaptionContainer"] {{ opacity: 1; }}  /* 補足文として薄くしない（案の名前なので） */
[class*="st-key-plans_"] [data-testid="stRadioCaption"] p {{ font-size: .9rem; font-weight: 600; color: var(--accent); margin: 0;
  line-height: 1.45; white-space: normal; overflow-wrap: anywhere; }}
[class*="st-key-plans_"] [role="radiogroup"] > div[data-selected="true"] {{ background: var(--accent); border-color: var(--accent); }}
[class*="st-key-plans_"] [role="radiogroup"] > div[data-selected="true"]::after {{ content: ""; position: absolute; left: 0; right: 0;
  bottom: 0; height: 3px; background: var(--gold); }}
[class*="st-key-plans_"] [role="radiogroup"] > div[data-selected="true"] p {{ color: #fff; }}
[class*="st-key-plans_"] [role="radiogroup"] > div[data-selected="true"] [data-testid="stRadioOption"] p {{ color: var(--on-navy-sub); }}
[class*="st-key-plans_"] [role="radiogroup"] > div:has(input:focus-visible) {{ outline: 2px solid var(--gold-deep); outline-offset: 2px; }}
.bic-plan-title {{ font-size: 1.05rem; font-weight: 700; color: var(--accent); margin: .6rem 0 .2rem; line-height: 1.5; }}

/* 注記 */
.bic-note {{ font-size: .8rem; color: var(--accent); line-height: 1.75; background: var(--gold-soft);
  border-left: 2px solid var(--gold); padding: .5rem .9rem; margin: .55rem 0; border-radius: 0 4px 4px 0; }}
/* TOPの「ご利用上の注意」だけ：淡いブルーグレーの面と青い線（デモの淡い金色と見分けるため）。ラベルだけ太字 */
.bic-note.bic-notice {{ background: var(--notice-bg); border-left-color: var(--notice-line); color: var(--accent); }}
.bic-notice-label {{ font-weight: 700; }}
.bic-tag {{ display: inline-block; font-size: .72rem; letter-spacing: .08em; padding: .2rem .65rem;
  border: 1px solid var(--line-blue); border-radius: 2px; color: var(--sub); background: var(--card); margin: 0 .4rem .3rem 0; }}

/* 上部のブランドと進み具合 */
.bic-bar {{ display: flex; justify-content: space-between; align-items: baseline;
  border-bottom: 1px solid var(--line-blue); padding: .35rem 0 .3rem; margin-bottom: -.45rem; }}
/* 下の余白：Streamlit が部品の間に入れるすき間（1rem）を、負の余白で一部打ち消して3分の1ほどにする */
.bic-brand {{ font-family: "Inter", sans-serif; font-weight: 600; letter-spacing: .22em; font-size: .95rem; }}
/* ブランド名はTOPへのリンク。見た目は変えず、マウスを乗せたときだけ薄くして押せることを示す */
a.bic-brand, a.bic-brand:visited {{ color: var(--ink) !important; text-decoration: none !important; }}
a.bic-brand:hover {{ opacity: .7; }}
.bic-brand span {{ font-weight: 400; color: var(--mute); letter-spacing: .12em; font-size: .72rem; margin-left: .8rem; }}
.bic-steps {{ display: flex; gap: 1.2rem; font-size: .78rem; letter-spacing: .06em; color: var(--mute); }}
.bic-steps .done {{ color: var(--sub); }}
.bic-steps .now {{ color: var(--accent); font-weight: 700; border-bottom: 2px solid var(--accent); padding-bottom: .2rem; }}
@media (max-width: 720px) {{ .bic-bar {{ flex-direction: column; gap: .6rem; }} .bic-steps {{ gap: .7rem; flex-wrap: wrap; }} }}

.bic-demo {{ font-size: .78rem; letter-spacing: .06em; color: var(--accent); background: var(--gold-soft);
  border-left: 2px solid var(--gold); padding: .55rem .9rem; border-radius: 0 4px 4px 0; margin-bottom: 1.2rem; }}

/* ヒアリングの履歴 */
.bic-qa {{ border-top: 1px solid var(--line-blue); padding: 1.1rem 0; }}
.bic-qa .q {{ display: flex; gap: .9rem; line-height: 1.8; }}
.bic-qa .q b {{ font-family: "Inter", sans-serif; color: var(--accent); font-weight: 600; min-width: 2rem; }}
.bic-qa .a {{ margin: .6rem 0 0 2.9rem; background: var(--card); border: 1px solid var(--line-blue); padding: .7rem 1rem; border-radius: 4px;
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

/* 結果画面の冒頭（AI-BIC 3.0）：最大の課題・最初にやること・次の判断に必要な情報の3つ。
   最初の画面に、この3つと主ボタンが収まるよう、診断サマリーより詰める */
.bic-decision {{ padding: 1.1rem 1.4rem 1.2rem; margin-bottom: 12px; }}
.bic-decision .tiles {{ grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin-top: .5rem; }}
.bic-decision .tile {{ padding: .7rem .9rem .8rem; }}
.bic-decision .t-main {{ font-size: .95rem; line-height: 1.6; margin: .25rem 0; }}
.bic-decision .gaps {{ list-style: none; margin: .3rem 0; padding: 0; font-size: .88rem; line-height: 1.55; }}
.bic-decision .gaps li {{ display: flex; gap: .35rem; margin: .15rem 0; }}
.bic-decision .gaps .m {{ color: var(--gold); font-weight: 700; flex: none; }}
@media (max-width: 720px) {{
  .bic-decision {{ padding: .85rem .95rem .9rem; }}
  .bic-decision .tiles {{ grid-template-columns: 1fr; gap: 8px; }}
  .bic-decision .tile {{ padding: .55rem .75rem .6rem; }}
  .bic-decision .t-main {{ font-size: .9rem; line-height: 1.55; }}
}}

/* 改善案の比較表。狭い画面では、行を同じ項目順のカードにする（項目名は data-label から出す） */
.bic-compare {{ width: 100%; border-collapse: separate; border-spacing: 0; background: var(--card);
  border: 1px solid var(--line-blue); border-radius: 6px; font-size: .9rem; line-height: 1.6; margin: .3rem 0 .4rem; }}
.bic-compare th {{ text-align: left; font-size: .76rem; letter-spacing: .08em; color: var(--sub); font-weight: 600;
  padding: .55rem .8rem; border-bottom: 1px solid var(--line-blue); background: var(--page); }}
.bic-compare td {{ vertical-align: top; padding: .65rem .8rem; border-bottom: 1px solid var(--line); color: var(--ink); }}
.bic-compare tr:last-child td {{ border-bottom: none; }}
.bic-compare td:first-child {{ width: 34%; }}
.bic-compare .no {{ font-size: .75rem; letter-spacing: .08em; color: var(--sub); }}
.bic-compare .nm {{ font-weight: 700; color: var(--accent); }}
.bic-compare .cat {{ font-size: .76rem; color: var(--sub); margin-bottom: .2rem; }}
.bic-compare small {{ color: var(--sub); }}
.bic-badge.rel {{ border-color: var(--notice-line); color: var(--accent); background: var(--notice-bg); margin: .15rem .3rem 0 0; }}
@media (max-width: 720px) {{
  .bic-compare, .bic-compare tbody, .bic-compare tr, .bic-compare td {{ display: block; width: 100% !important; }}
  .bic-compare {{ border: none; background: none; }}
  .bic-compare thead {{ display: none; }}
  .bic-compare tr {{ background: var(--card); border: 1px solid var(--line-blue); border-radius: 6px; margin-bottom: 8px; }}
  .bic-compare td {{ border-bottom: 1px solid var(--line); padding: .5rem .8rem; }}
  .bic-compare td:not(:first-child)::before {{ content: attr(data-label); display: block; font-size: .72rem;
    letter-spacing: .06em; color: var(--sub); }}
}}
.bic-relations {{ margin: .2rem 0 .6rem; padding-left: 1.2rem; font-size: .86rem; line-height: 1.7; color: var(--ink); }}
.bic-relations b {{ color: var(--accent); }}

/* 選んだ案の要点と作業領域（AI-BIC 3.2） */
.bic-brief {{ background: var(--card); border: 1px solid var(--line-blue); border-left: 4px solid var(--accent); border-radius: 6px;
  padding: .8rem 1rem; margin: .5rem 0; }}
.bic-brief .head {{ display: flex; flex-wrap: wrap; align-items: center; gap: .4rem .6rem; margin-bottom: .3rem; color: var(--accent); }}
.bic-brief .head .no {{ font-size: .78rem; color: var(--sub); }}
.bic-brief small {{ color: var(--sub); }}
.bic-work-title {{ font-weight: 700; color: var(--accent); margin: .9rem 0 .2rem; }}

/* 分かっている数値と計算する値（AI-BIC 3.1） */
.bic-quantities {{ background: var(--card); border: 1px solid var(--line-blue); border-radius: 6px; padding: .6rem .9rem; margin: .4rem 0 .6rem; }}
.bic-quantities .k {{ font-weight: 700; color: var(--accent); font-size: .9rem; }}

/* 不足情報のカード（重要なものから） */
.bic-gap {{ background: var(--card); border: 1px solid var(--line-blue); border-radius: 6px; padding: .7rem .9rem; margin: .4rem 0; }}
.bic-gap .q {{ font-weight: 700; color: var(--accent); margin: .3rem 0 .2rem; line-height: 1.6; }}

/* 試行の測定結果の表（試算値・目標値・実測値を別の列に）。狭い画面では表の中だけ横に動かす */
.bic-trial-wrap {{ overflow-x: auto; margin: .4rem 0; }}
.bic-trial {{ width: 100%; min-width: 640px; border-collapse: collapse; background: var(--card); font-size: .86rem; line-height: 1.55;
  border: 1px solid var(--line-blue); }}
.bic-trial th {{ text-align: left; font-size: .74rem; color: var(--sub); font-weight: 600; background: var(--page);
  padding: .45rem .6rem; border-bottom: 1px solid var(--line-blue); }}
.bic-trial td {{ padding: .45rem .6rem; border-bottom: 1px solid var(--line); vertical-align: top; color: var(--ink); }}
@media (max-width: 720px) {{
  .bic-trial {{ min-width: 0; border: none; background: none; }}
  .bic-trial thead {{ display: none; }}
  .bic-trial, .bic-trial tbody, .bic-trial tr {{ display: block; width: 100%; }}
  .bic-trial tr {{ background: var(--card); border: 1px solid var(--line-blue); border-radius: 6px; margin-bottom: 8px; padding: .3rem 0; }}
  .bic-trial td {{ display: flex; gap: .6rem; border-bottom: none; padding: .2rem .7rem; }}
  .bic-trial td:not(:first-child)::before {{ content: attr(data-label); flex: 0 0 8.5em; font-size: .74rem; color: var(--sub); }}
}}

/* ヒアリングの現在の質問カード（AI-BIC 3.1）：質問番号・進み具合 → 質問文 → 回答欄 → 回答例 → ボタン */
.st-key-hearing_card {{ background: #EFF6FF; border: 1px solid #CBDCF3; border-radius: 12px; padding: 1.1rem 1.3rem 1.2rem;
  gap: .55rem; }}
.st-key-hearing_card .bic-qhead {{ font-size: .8rem; letter-spacing: .04em; color: #44546F; }}
.st-key-hearing_card .bic-question {{ color: #172B4D; font-weight: 700; font-size: 22px; line-height: 1.6; margin: .2rem 0 .6rem; }}
.st-key-hearing_card .bic-preparing {{ color: #172B4D; font-weight: 600; padding: .6rem 0; }}
/* 回答のフォームは、共通の白い枠を付けない（カードの中に白い箱が二重にできるため。白いのは回答欄だけ） */
.st-key-hearing_card [data-testid="stForm"] {{ border: none; background: transparent; padding: 0; }}
.st-key-hearing_card [data-testid="stWidgetLabel"] p {{ color: #172B4D; font-weight: 600; }}
.st-key-hearing_card [data-testid="stTextAreaRootElement"] {{ background: #FFFFFF !important; border: 1px solid #9FB6D8 !important; }}
.st-key-hearing_card [data-testid="stTextAreaRootElement"]:focus-within {{ border-color: #172B4D !important;
  box-shadow: 0 0 0 3px rgba(23,43,77,.18) !important; }}
.st-key-hearing_card [data-testid="stCaptionContainer"] {{ color: #44546F; }}
@media (max-width: 720px) {{
  .st-key-hearing_card {{ padding: .9rem .9rem 1rem; }}
  .st-key-hearing_card .bic-question {{ font-size: 19px; }}
}}


/* 課題・改善案のカード
   読む順番：太字（タイトル・要点）→ 根拠や改善後の姿 → 小さく薄い補足（表形式） */
.bic-card {{ background: var(--card); border: 1px solid var(--line-blue); border-left: 3px solid var(--line-blue); border-radius: 4px;
  padding: 1.6rem 1.9rem 1.5rem; margin-bottom: 1.1rem; background: #fff; }}
.bic-card.sev-high {{ border-left-color: var(--gold-deep); }}  /* 重要度「高」は左端を金色に */
.bic-card.sev-mid {{ border-left-color: var(--accent); }}
.bic-card .head {{ display: flex; gap: 1.2rem; align-items: flex-start; }}
.bic-card .num {{ font-family: "Inter", sans-serif; font-size: 2.1rem; font-weight: 300; color: #C9CCD2;
  line-height: 1; min-width: 2.7rem; }}
.bic-card.sev-high .num, .bic-card.recommended .num {{ color: var(--gold-deep); }}
.bic-card h3 {{ font-size: 1.15rem; font-weight: 700; letter-spacing: .03em; line-height: 1.6; margin: .2rem 0 .55rem; }}
.bic-card .gist {{ font-size: .98rem; font-weight: 500; line-height: 1.9; color: var(--ink); margin: .7rem 0 .2rem; }}
.bic-card p {{ line-height: 1.9; margin: .5rem 0; font-size: .92rem; }}
.bic-card .k {{ font-size: .72rem; font-weight: 700; letter-spacing: .12em; color: var(--accent); margin: 1.2rem 0 .35rem; }}
.bic-badge {{ display: inline-block; font-size: .72rem; letter-spacing: .06em; padding: .18rem .6rem;
  border-radius: 2px; margin: 0 .35rem .3rem 0; border: 1px solid var(--accent); color: var(--accent); }}
.bic-badge.fill {{ background: var(--accent); color: var(--gold); border-color: var(--accent); font-weight: 700; }}
.bic-badge.gray {{ border-color: var(--line); color: var(--sub); }}
.bic-badge.gold {{ border-color: var(--gold-deep); color: var(--gold-deep); font-weight: 700; }}
.bic-quote {{ border-left: 2px solid var(--accent); padding: .2rem 0 .2rem .9rem; margin: .45rem 0;
  font-size: .92rem; font-weight: 500; line-height: 1.8; }}
.bic-quote small {{ color: var(--mute); margin-left: .6rem; font-weight: 400; }}
.bic-quote .supports {{ display: block; font-size: .8rem; color: var(--sub); font-weight: 400; line-height: 1.6; }}
.bic-warn {{ font-size: .8rem; color: var(--accent); background: var(--accent-soft); padding: .5rem .8rem;
  border-radius: 4px; margin: .6rem 0; }}

/* 補足情報（原因・影響・リスクなど）：小さく薄い文字の表にまとめ、要点より目立たせない */
.bic-dl {{ display: grid; grid-template-columns: 8.5rem 1fr; margin: 1.2rem 0 0; border-top: 1px solid var(--line); }}
.bic-dl dt, .bic-dl dd {{ border-bottom: 1px solid var(--line); padding: .6rem 0; margin: 0;
  font-size: .84rem; line-height: 1.8; }}
.bic-dl dt {{ color: var(--mute); letter-spacing: .06em; padding-right: 1rem; }}
.bic-dl dd {{ color: var(--sub); }}
.bic-dl dd ul {{ margin: 0; padding-left: 1.1rem; }}
@media (max-width: 720px) {{ .bic-dl {{ grid-template-columns: 1fr; }} .bic-dl dt {{ border-bottom: none; padding-bottom: 0; }} }}

/* 改善案：1つ目（最も推奨）に印、BEFORE/AFTER、推奨理由を強調 */
.bic-card.recommended {{ border-left-color: var(--gold-deep); }}
.bic-reco {{ display: inline-block; font-size: .7rem; font-weight: 700; letter-spacing: .14em; color: #fff;
  background: linear-gradient(120deg, var(--gold-deep), var(--gold)); padding: .2rem .7rem; border-radius: 2px; margin-bottom: .5rem; }}
.bic-ba {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1px; background: var(--line);
  border: 1px solid var(--line); border-radius: 4px; overflow: hidden; margin: 1rem 0; }}
.bic-ba div {{ background: #fff; padding: 1rem 1.1rem; font-size: .88rem; line-height: 1.85; color: var(--sub); }}
.bic-ba div.after {{ background: var(--accent-soft); color: var(--ink); font-weight: 500; }}
.bic-ba b {{ display: block; font-family: "Inter", sans-serif; font-size: .68rem; letter-spacing: .2em;
  color: var(--mute); margin-bottom: .35rem; font-weight: 600; }}
.bic-ba div.after b {{ color: var(--accent); }}
@media (max-width: 720px) {{ .bic-ba {{ grid-template-columns: 1fr; }} }}
.bic-reason {{ border-left: 3px solid var(--gold-deep); background: #FBF8F1; padding: .8rem 1.1rem;
  margin: .9rem 0; border-radius: 0 4px 4px 0; font-size: .92rem; line-height: 1.85; }}
.bic-reason b {{ display: block; font-size: .72rem; letter-spacing: .12em; color: var(--gold-deep); margin-bottom: .2rem; }}

.bic-steps-list {{ list-style: none; padding: 0; margin: .3rem 0 .4rem; }}
.bic-steps-list li {{ position: relative; padding: .35rem 0 .35rem 1.7rem; line-height: 1.75;
  font-size: .93rem; font-weight: 500; }}
.bic-steps-list li::before {{ content: ""; position: absolute; left: 0; top: .72rem; width: .8rem; height: .8rem;
  border: 1.5px solid var(--gold-deep); border-radius: 2px; }}
.bic-list {{ margin: .2rem 0; padding-left: 1.2rem; font-size: .88rem; line-height: 1.85; color: var(--sub); }}
.bic-footnote {{ font-size: .78rem; color: var(--mute); line-height: 1.9; margin-top: 2rem; }}
</style>
"""

SEVERITY_CLASS = {"高": "fill", "中": "", "低": "gray"}  # 重要度のバッジ
SEVERITY_CARD = {"高": "sev-high", "中": "sev-mid", "低": "sev-low"}  # カードの左端の線の色
# 根拠の区分の表示（B04：「事実」は、発言にそう書かれていることを「本人の申告」と呼ぶよう改めた）
EVIDENCE_TEXT = {
    "本人の申告": "本人の申告：発言で確認済み",
    "計算結果": "計算結果：発言の数値から計算",
    "推測": "推測（仮説）：発言からの推測",
    "要確認": "要確認：判断材料が不足",
}
DISCLAIMER = "診断結果はAIによる参考情報です。効果は想定であり、実施前に関係者と事実を確認してください。"


def esc(text) -> str:
    """HTMLに埋め込む文字を、ただの文字として表示されるように変換する（XSS対策）。

    「$」は Streamlit で数式として解釈されるため、これも文字として扱う。
    """
    return html.escape(str(text or "")).replace("$", "&#36;")


def md_text(text: str) -> str:
    """Markdown として表示される場所（選択肢の説明文など）に、記号をそのまま文字として出す。"""
    return "".join("\\" + c if c in "\\`*_{}[]()#+-.!|~<>$" else c for c in str(text))


# 結果画面の中の移動先（見出しそのものに付ける id。空の要素を置くと、見出しの上の余白が画面の上に残ったため）
SECTIONS = {"gaps": "sec-gaps", "compare": "sec-compare", "flows": "sec-flows", "sim": "sec-sim", "review": "sec-review",
            "trial": "sec-trial", "evidence": "sec-evidence", "work": "sec-work"}


def label(en: str, ja: str, tight: bool = False, anchor: str | None = None, first: bool = False) -> str:
    """英字の小さなラベル＋日本語の見出し。tight=True は余白を詰めた版（トップページ用）。

    anchor：SECTIONS のキー。見出しに移動先の id を付ける（画面を動かすと、この見出しが上に来る）。
    first：タブの中の最初の見出し。上の大きな余白を付けない（タブのすぐ下から内容が始まるように）。
    """
    extra = (" tight" if tight else "") + (" first" if first else "")
    id_attr = f' id="{SECTIONS[anchor]}"' if anchor else ""
    return f'<div class="bic-label{extra}"{id_attr}>{esc(en)}</div><div class="bic-h2{extra}">{esc(ja)}</div>'


def hero() -> str:
    return (
        '<div class="bic-hero"><div class="en">AI BUSINESS IMPROVEMENT CONSULTANT</div>'
        "<h1>業務改善を、根拠から始める。</h1>"
        "<p>AIが担当者にヒアリングし、業務の課題と改善案を、ご本人の発言と資料にもとづいて整理します。</p></div>"
    )


def features() -> str:
    # 質問数は設定値から作る（設定を変えたときに、説明だけ古いまま残らないように）。
    # 実装：基本の質問のあと、3機能に必要な情報が足りず、利用者が選んだ場合だけ追加確認をする（readiness.py）
    base, extra = config.MAX_HEARING_TURNS, config.MAX_EXTRA_QUESTIONS
    items = [
        # 情報がそろえば早く終わるため、「最大○問」を問題数の約束に見せない（AI-BIC 3.1）
        ("01", "必要な情報だけを確認", f"基本は最大{base}問、追加は最大{extra}問。分かっていることは聞かず、情報がそろえば早く終わります。"),
        ("02", "事実と推測を区別", "AIが挙げた根拠を、入力や添付資料と照合。確認できない内容は「要確認」にします。"),
        ("03", "費用のかからない順に提案", "ルールの見直しから検討し、新しいシステムの導入を安易に勧めません。"),
    ]
    cells = "".join(
        f'<div><div class="no">{no}</div><h3>{esc(t)}</h3><p>{esc(d)}</p></div>' for no, t, d in items
    )
    return f'<div class="bic-features">{cells}</div>'


def note(text: str) -> str:
    return f'<div class="bic-note">{esc(text)}</div>'


def notice(label: str, text: str) -> str:
    """TOPの「ご利用上の注意」（ラベルだけ太字）。配色は .bic-notice に限る（ほかの注記は変えない）。"""
    return f'<div class="bic-note bic-notice"><span class="bic-notice-label">{esc(label)}</span>　{esc(text)}</div>'


def topbar(steps: list[tuple[str, str]], current: str | None) -> str:
    """上部のブランド名と、診断の進み具合（N04）。"""
    keys = [k for k, _ in steps]
    now = keys.index(current) if current in keys else -1
    items = "".join(
        f'<span class="{"now" if i == now else ("done" if i < now else "")}">{esc(name)}</span>'
        for i, (_, name) in enumerate(steps)
    )
    steps_html = f'<div class="bic-steps">{items}</div>' if now >= 0 else ""
    # どの画面でも、ブランド名を押すとTOPの最上部に戻る（開発者の要望）。ページを読み込み直すため、
    # 入力中の内容は消える（「トップに戻る」と同じ扱い）。そのことをマウスを乗せたときに示す
    brand = ('<a class="bic-brand" href="./" target="_self" title="TOPに戻ります（入力中の内容は消えます）">'
             "AI-BIC<span>業務改善診断</span></a>")
    return f'<div class="bic-bar">{brand}{steps_html}</div>'


def demo_banner() -> str:
    return '<div class="bic-demo">デモモード：AIは使用していません。架空の業務と、あらかじめ用意した固定の結果を表示しています。</div>'


def qa(turn: HearingTurn) -> str:
    """ヒアリングの質問と回答の1往復。"""
    answer = f'<div class="a">{esc(turn.answer)}</div>' if turn.answer is not None else ""
    return f'<div class="bic-qa"><div class="q"><b>Q{turn.turn_no}</b><span>{esc(turn.question)}</span></div>{answer}</div>'


CONFIRM_MARK = {"確認済み": "✓", "推測": "△", "要確認": "？"}  # 色だけで区別しないよう記号も付ける


def decision_card(ov, shown_gaps: int) -> str:
    """結果画面の冒頭：「最大の課題」「最初にやること」「次の判断に必要な情報」をこの順で出す（AI-BIC 3.0）。

    ov は overview.Overview。確認済みの情報と、推測・要確認・未確認の情報を、記号と文字で見分けられるようにする。
    """
    if ov.top_issue:
        mark = CONFIRM_MARK[ov.top_confirmation.split("（")[0]]
        top = (f'<div class="t-main">{esc(ov.top_issue.title)}</div>'
               f'<div class="t-sub">{mark} {esc(ov.top_confirmation)}　／　重要度 {esc(ov.top_issue.severity)}</div>')
    else:
        top = '<div class="t-main">（課題を特定できませんでした）</div>'
    open_gaps = ov.open_gaps  # 回答済みのものは出さない
    gaps = open_gaps[:shown_gaps]
    if gaps:
        items = "".join(
            f'<li><span class="m">{"！" if g.important else "・"}</span>{esc(g.question)}</li>' for g in gaps)
        rest = len(open_gaps) - len(gaps)
        sub = ("！は重要な未確認の情報です" if any(g.important for g in gaps) else "分かると判断の精度が上がる情報です")
        need = f'<ul class="gaps">{items}</ul><div class="t-sub">？ 未確認　{sub}{f"（ほか{rest}件）" if rest else ""}</div>'
    else:
        need = ('<div class="t-main">大きな不足はありません</div>'
                '<div class="t-sub">改善案を比較して、取り組む案を選んでください</div>')
    return (
        '<div class="bic-summary bic-decision"><div class="eyebrow">DIAGNOSIS</div><div class="tiles">'
        f'<div class="tile"><div class="t-label">最大の課題</div>{top}</div>'
        f'<div class="tile"><div class="t-label">最初にやること</div><div class="t-main">{esc(ov.first_action)}</div>'
        '<div class="t-sub">△ AIの提案（今週から始められること）</div></div>'
        f'<div class="tile"><div class="t-label">次の判断に必要な情報</div>{need}</div>'
        "</div></div>"
    )


def sample_banner(answered: int, total: int) -> str:
    """デモを途中で終えたときに、結果の冒頭に出す説明（AI-BIC 3.0）。判定の基準が混ざらないよう、何と照合したかを示す。"""
    return (
        '<div class="bic-note bic-notice"><span class="bic-notice-label">全問回答した場合のサンプル結果</span>　'
        f"ヒアリングを途中（{answered}問回答）で終えたため、サンプル回答（全{total}問）で答えた場合の結果です。"
        "根拠もサンプル回答と照合し、今回の回答は「今回の入力」に分けて表示しています。</div>"
    )


def gap_card(g) -> str:
    """不足情報のカード。質問・確認が必要な理由・影響する課題や改善案と、確認の状態（記号と文字）を示す。"""
    badge = ('<span class="bic-badge gold">！ 重要</span>' if g.important
             else '<span class="bic-badge gray">参考</span>')
    state = (f'<span class="bic-badge rel">✓ 回答済み（{esc(g.answered.split("：")[0])}）</span>' if g.answered
             else '<span class="bic-badge gray">？ 要確認</span>')
    answered = f'<dt>回答</dt><dd>{esc(g.answered)}</dd>' if g.answered else ""
    return (
        f'<div class="bic-gap">{badge}{state}'
        f'<div class="q">{esc(g.question)}</div>'
        f'<dl class="bic-dl"><dt>確認が必要な理由</dt><dd>{esc(g.reason)}</dd>'
        f'<dt>影響するもの</dt><dd>{esc("、".join(g.affects))}</dd>{answered}</dl></div>'
    )


def gap_list(gaps) -> str:
    """不足情報の一覧（重要なものから）。"""
    return "".join(gap_card(g) for g in gaps)


def compare_table(rows) -> str:
    """改善案の比較表（rows は overview.CompareRow）。狭い画面では、同じ項目順のカードとして表示する（CSS）。"""
    body = []
    for r in rows:
        chips = "".join(f'<span class="bic-badge rel" title="{esc(t.reason)}">{esc(t.text)}</span>' for t in r.relations)
        reduces = esc(r.reduces) + ('<small>（対象の課題から）</small>' if r.reduces_from_issues else "")
        checks = esc(r.check_before) + (f"<small>（ほか{r.more_checks}件）</small>" if r.more_checks else "")
        body.append(
            f'<tr><td data-label="改善案"><div class="no">案{r.no}</div><div class="nm">{esc(r.name)}</div>'
            f'<div class="cat">{esc(r.category)}</div>{chips}</td>'
            f'<td data-label="主に減らすもの">{reduces}</td>'
            f'<td data-label="着手前に確認すること">{checks}</td></tr>'
        )
    return ('<table class="bic-compare"><thead><tr><th>改善案</th><th>主に減らすもの</th><th>着手前に確認すること</th>'
            f'</tr></thead><tbody>{"".join(body)}</tbody></table>')


def relation_list(relations) -> str:
    """案どうしの関係と理由（relations は overview.valid_relations の結果）。"""
    items = []
    for r in relations:
        if r.kind == "順序":
            head = f"案{r.first}を先にやる → 案{r.second}を次に試す"
        else:
            head = f"案{r.first}と案{r.second}は併用できる"
        items.append(f"<li><b>{esc(head)}</b>　{esc(r.reason)}</li>")
    return f'<ul class="bic-relations">{"".join(items)}</ul>' if items else ""


def issue_card(no: int, issue: Issue, viewpoints: str, sources: list[str], downgraded: str | None) -> str:
    """課題のカード。sources は各根拠の出どころの表示（evidences と同じ順）。

    downgraded は、根拠を確認できず「要確認」に格下げしたときの説明（格下げしていなければ None）。
    """
    badges = (
        f'<span class="bic-badge {SEVERITY_CLASS[issue.severity]}">重要度 {esc(issue.severity)}</span>'
        f'<span class="bic-badge gray">{esc(EVIDENCE_TEXT[issue.evidence_type])}</span>'
        f'<span class="bic-badge gray">{esc(viewpoints)}</span>'
    )
    warn = f'<div class="bic-warn">{esc(downgraded)}</div>' if downgraded else ""
    # 引用と、その引用から言えることを並べ、対応が正しいかを読む人が確かめられるようにする（B04）
    quotes = "".join(
        f'<div class="bic-quote">{esc(ev.quote)}<small>{esc(src)}</small>'
        f'<span class="supports">→ この引用から言えること：{esc(ev.supports)}</span></div>'
        for ev, src in zip(issue.evidences, sources)
    ) or '<p class="bic-list">根拠となる発言はありません</p>'

    # 補足（原因・影響・重要度の理由・追加確認）は小さく薄い表にまとめる
    rows = [("計算式", esc(issue.calculation))] if issue.calculation else []
    rows += [
        ("想定原因（推測）", esc(issue.assumed_cause)),
        ("業務への影響", esc(issue.impact)),
        ("重要度の理由", esc(issue.severity_reason)),
    ]
    if issue.followup_questions:
        rows.append(
            ("追加で確認したいこと", "<ul>" + "".join(f"<li>{esc(q)}</li>" for q in issue.followup_questions) + "</ul>")
        )
    details = "".join(f"<dt>{name}</dt><dd>{value}</dd>" for name, value in rows)

    return (
        f'<div class="bic-card {SEVERITY_CARD[issue.severity]}"><div class="head">'
        f'<div class="num">{no:02d}</div><div style="flex:1">'
        f"<h3>{esc(issue.title)}</h3>{badges}{warn}"
        f'<div class="gist">{esc(issue.summary)}</div>'
        f'<div class="k">判断根拠（ご本人の発言・添付資料）</div>{quotes}'
        f'<dl class="bic-dl">{details}</dl></div></div></div>'
    )


def proposal_brief(no: int, p: Proposal, issue_titles: list[str]) -> str:
    """選んだ案の要点（AI-BIC 3.2）：目的・変更内容・期待する効果・着手前の条件・主なリスク。詳しい説明は別に開く。"""
    def first(values: list[str], n: int = 2) -> str:
        shown = "／".join(esc(v) for v in values[:n])
        return shown + (f"<small>（ほか{len(values) - n}件）</small>" if len(values) > n else "") if values else "特になし"
    purpose = esc(p.reduces) + "を減らす" if p.reduces else ""
    if issue_titles:
        purpose += ("（" if purpose else "") + "対象の課題：" + "、".join(esc(x) for x in issue_titles) + ("）" if purpose else "")
    rows = [("目的", purpose or "—"), ("変更内容", esc(p.description)), ("期待する効果（想定）", esc(p.expected_effect)),
            ("着手前の条件", first(p.prerequisites)), ("主なリスク", first(p.risks))]
    if p.effect_metric:
        rows.insert(3, ("効果を測る指標", esc(p.effect_metric)))
    body = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in rows)
    return (f'<div class="bic-brief"><div class="head"><span class="no">案{no}</span><b>{esc(p.title)}</b>'
            f'<span class="bic-badge">{esc(p.category)}</span><span class="bic-badge gray">導入難易度 {esc(p.difficulty)}</span></div>'
            f'<dl class="bic-dl">{body}</dl></div>')


def proposal_card(no: int, p: Proposal) -> str:
    """改善案のカード。改善案は推奨順に並ぶため、1つ目に「最初に取り組む案」の印をつける。"""

    def items(values: list[str]) -> str:
        return "".join(f"<li>{esc(v)}</li>" for v in values)

    recommended = no == 1
    reco = '<div class="bic-reco">最初に取り組む案</div><br>' if recommended else ""

    # 補足（効果・より簡単な方法では不十分な理由・リスク・事前確認）は小さく薄い表にまとめる
    rows = [("期待できる効果（想定）", esc(p.expected_effect))]
    if p.why_not_simpler:
        rows.append(("より簡単な方法では不十分な理由", esc(p.why_not_simpler)))
    if p.risks:
        rows.append(("想定リスク", f"<ul>{items(p.risks)}</ul>"))
    if p.prerequisites:
        rows.append(("実施前に確認すべき事項", f"<ul>{items(p.prerequisites)}</ul>"))
    details = "".join(f"<dt>{name}</dt><dd>{value}</dd>" for name, value in rows)

    return (
        f'<div class="bic-card{" recommended" if recommended else ""}"><div class="head">'
        f'<div class="num">{no:02d}</div><div style="flex:1">{reco}'
        f'<span class="bic-badge">{esc(p.category)}</span>'
        f'<span class="bic-badge gray">導入難易度 {esc(p.difficulty)}</span>'
        f'<span class="bic-badge gray">対応する課題 {esc("・".join(p.target_issue_ids))}</span>'
        f"<h3>{esc(p.title)}</h3><p>{esc(p.description)}</p>"
        f'<div class="bic-ba"><div><b>BEFORE　今</b>{esc(p.before)}</div>'
        f'<div class="after"><b>AFTER　改善後（想定）</b>{esc(p.after)}</div></div>'
        f'<div class="k">最初の一歩（今週から始められること）</div><ul class="bic-steps-list">{items(p.first_steps)}</ul>'
        f'<div class="bic-reason"><b>推奨理由</b>{esc(p.reason)}</div>'
        f'<dl class="bic-dl">{details}</dl></div></div></div>'
    )


def footnote(text: str) -> str:
    return f'<div class="bic-footnote">{esc(text)}</div>'


# ---------------------------------------------------------------
# 画面とレポート（report.py）で共通の文言
# ---------------------------------------------------------------
def evidence_source(ev: Evidence, attachment_names: list[str], sample: bool = False) -> str:
    """根拠の出どころの表示（例：「2問目の回答」「添付ファイル『記録.csv』」）。

    sample：デモを途中で終え、全問回答のサンプルと照合した結果（「サンプル回答」と明示する）。
    """
    if ev.source == "回答":
        return f"{ev.turn_no}問目のサンプル回答" if sample else f"{ev.turn_no}問目の回答"
    if ev.source == "添付ファイル":
        valid = ev.file_no is not None and 1 <= ev.file_no <= len(attachment_names)
        return f"添付ファイル『{attachment_names[ev.file_no - 1]}』" if valid else "添付ファイル"
    return "業務情報"


def missing_item_text(item: HearingItem, status: ItemStatus | None) -> str:
    """不足している診断項目と、その理由の表示（B01。例：「業務の目的（不十分）　足りないこと：…」）。

    AIが理由を書かなかったときは、その項目で確認したいこと（description）を代わりに出す。
    """
    label = status.status if status else "未確認"
    lacking = (status.lacking if status else "") or item.description
    text = f"{item.name}（{label}）　足りないこと：{lacking}"
    if status and status.known_facts:
        text += f"　／　分かっていること：{status.known_facts}"
    return text
