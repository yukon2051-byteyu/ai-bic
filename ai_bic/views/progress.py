"""結果画面の進み具合の表示（AI-BIC 2.0 指示書 7.3）。状態は progress.py が判定する。"""

import streamlit as st

from ai_bic import progress, ui
from ai_bic.project import Project

# 状態 → (記号, 色)。色だけで区別しないよう、記号と文字も付ける
MARK = {
    "完了": ("✓", "#137333"), "作業中": ("…", "#1967D2"), "未着手": ("○", "#9AA0A6"), "要確認": ("？", "#B06000"),
    "再作成が必要": ("↻", "#C5221F"), "再計算が必要": ("↻", "#C5221F"), "再レビューが必要": ("↻", "#C5221F"),
}


def render(project: Project) -> None:
    states = progress.steps(project)
    cells = []
    for s in states:
        mark, color = MARK[s.status]
        title = f' title="{ui.esc(s.detail)}"' if s.detail else ""
        cells.append(
            f'<div{title} style="flex:1;min-width:120px;background:var(--card);border:1px solid var(--line-blue);'
            f'border-top:3px solid {color};border-radius:4px;padding:.35rem .55rem;font-size:.78rem">'
            f'<div style="color:var(--sub)">STEP {s.no}</div><div style="font-weight:600;color:var(--accent)">{ui.esc(s.name)}</div>'
            f'<div style="color:{color}">{mark} {ui.esc(s.status)}</div></div>'
        )
    st.markdown(f'<div style="display:flex;flex-wrap:wrap;gap:6px;margin:.2rem 0 .4rem">{"".join(cells)}</div>',
                unsafe_allow_html=True)
    action = progress.next_action(states)
    # 次にすることは、読み手に拾ってほしい案内なので淡い金色の面にする（配色の役割）
    st.markdown(ui.note(f"次にすること　{action}" if action else "すべての STEP が完了しています。"), unsafe_allow_html=True)
    details = [f"STEP {s.no}：{s.detail}" for s in states if s.detail]
    if details:
        st.caption("　／　".join(details))
