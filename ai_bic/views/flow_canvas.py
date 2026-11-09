"""業務フロー図の表示部品（AI-BIC 2.0 機能B の表示の改修）。

Streamlit 標準の図の表示（st.graphviz_chart）には、拡大縮小・移動や、工程を選んで詳細を見る機能がない。
そこで、ブラウザの中で Graphviz（viz.js）を動かして図を描き、次の操作をできるようにする。
- ズーム（ボタン・Ctrl＋ホイール）、パン（ドラッグ・スクロール）、「全体を表示」「100%」
- 工程をクリックすると、図の下に詳細（工程名の全文・作業内容・担当・システム・課題・改善内容・確認事項）を表示する
- 左右比較では、2つの図を同じ倍率で表示し、選んだ工程と同じIDの工程を両方で強調する
- 狭い画面（スマートフォン）では、比較を左右ではなくタブで切り替える
最初は全体が見える倍率にするが、文字が読めないほど小さくはしない（MIN_FIT_SCALE）。

図は表示のたびに工程データ（flow.Flow）から作る。データは変えない。
画面に出す文字はすべて textContent で入れ、HTML として解釈させない（工程名などによる画面の乗っ取りを防ぐ）。
"""

import json

import streamlit.components.v1 as components

from ai_bic import flow as fl

VIZ_URL = "https://cdn.jsdelivr.net/npm/@viz-js/viz@3.29.0/dist/viz-global.js"  # 版を固定（2026-08 公開）
MIN_FIT_SCALE = 0.75  # 「全体を表示」で縮める下限（工程名14px × 0.75 ≒ 10.5px。これより小さいと読めない）


def step_details(step: fl.FlowStep, to_be: bool) -> list[list[str]]:
    """工程を選んだときに表示する詳細（項目名, 内容）。"""
    rows = [
        ["工程", f"{step.id} {step.name}"],
        ["担当", step.actor or "未確認"],
        ["作業内容", step.description or "—"],
        ["使用システム", step.system or "未確認"],
        ["手作業", {True: "はい", False: "いいえ", None: "不明"}[step.manual]],
        ["作業時間", f"{step.minutes_per_case:g}分（{step.minutes_source}）" if step.minutes_per_case is not None else "不明"],
        ["実施割合", f"{step.occurrence_rate:g}%"],
        ["待ち・手戻り", step.wait_or_rework or "—"],
        ["課題", step.problem or "—"],
    ]
    if to_be:
        rows += [["変化", step.change], ["改善内容", step.change_note or "—"]]
    if step.needs_confirmation:
        rows.append(["確認事項", "ヒアリングで確かめられていない工程です。実際の業務と合っているか確認してください"])
    return rows


def pane(title: str, flow: fl.Flow, to_be: bool) -> dict:
    return {
        "title": title,
        "dot": fl.to_dot(flow, to_be=to_be),
        "steps": {s.id: step_details(s, to_be) for s in flow.steps},
    }


def height_for(*flows: fl.Flow) -> int:
    """部品の高さ（px）。工程が多いほど高くするが、画面に収まる上限で止め、残りは図の中でスクロールする。"""
    steps = max((len(f.steps) for f in flows), default=1)
    return int(min(860, max(460, 200 + 62 * steps)))


def build_html(panes: list[dict], height: int) -> str:
    """図の表示部品の HTML。データは JSON として埋め込み、スクリプトの中で読む。"""
    data = json.dumps({"panes": panes, "minFit": MIN_FIT_SCALE}, ensure_ascii=False).replace("</", "<\\/")
    return TEMPLATE.replace("__HEIGHT__", str(height)).replace("__VIZ__", VIZ_URL).replace("__DATA__", data)


def render(panes: list[dict], height: int) -> None:
    components.html(build_html(panes, height), height=height, scrolling=False)


TEMPLATE = r"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<style>
  :root { --ink:#14161A; --sub:#5F6368; --line:#E4E5E8; --accent:#1B2B4B; --bg:#FFFFFF; --soft:#F6F6F7; }
  * { box-sizing: border-box; }
  body { margin:0; font-family:"Noto Sans JP","Hiragino Sans","Yu Gothic","Meiryo",sans-serif; color:var(--ink); background:var(--bg); }
  #app { height:__HEIGHT__px; display:flex; flex-direction:column; gap:6px; }
  .bar { display:flex; flex-wrap:wrap; align-items:center; gap:6px; font-size:12px; }
  .bar button { font:inherit; padding:3px 10px; border:1px solid var(--line); border-radius:4px; background:var(--bg); cursor:pointer; }
  .bar button:hover { border-color:var(--accent); }
  .bar .tabs button.on { background:var(--accent); color:#fff; border-color:var(--accent); }
  .bar .hint { color:var(--sub); }
  .bar .zoom { min-width:44px; text-align:center; color:var(--sub); }
  #panes { flex:1; min-height:0; display:grid; gap:8px; grid-template-columns:repeat(var(--cols),minmax(0,1fr)); }
  .pane { display:flex; flex-direction:column; min-height:0; border:1px solid var(--line); border-radius:4px; }
  .pane h4 { margin:0; padding:4px 8px; font-size:12px; font-weight:600; border-bottom:1px solid var(--line); background:var(--soft); }
  .scroll { flex:1; min-height:0; overflow:auto; cursor:grab; }
  .scroll.drag { cursor:grabbing; }
  .scroll svg { display:block; margin:0 auto; }
  .scroll g.node { cursor:pointer; }
  .scroll g.node.sel > path, .scroll g.node.sel > polygon { stroke-width:3.5px; stroke:#1B2B4B; }
  #detail { height:118px; overflow:auto; border:1px solid var(--line); border-radius:4px; padding:6px 10px; font-size:12px; background:var(--soft); }
  #detail dl { display:grid; grid-template-columns:7em 1fr; gap:2px 10px; margin:0; }
  #detail dt { color:var(--sub); } #detail dd { margin:0; }
  .msg { padding:12px; color:var(--sub); font-size:13px; }
  @media (max-width: 760px) { #panes { --cols:1 !important; } .pane.off { display:none; } }
</style></head>
<body><div id="app">
  <div class="bar">
    <span class="tabs" id="tabs"></span>
    <button id="out" title="縮小">－</button><span class="zoom" id="zoom"></span><button id="in" title="拡大">＋</button>
    <button id="fit">全体を表示</button><button id="one">100%</button>
    <span class="hint">工程をクリックすると詳細を表示／ドラッグで移動／Ctrl＋ホイールで拡大縮小</span>
  </div>
  <div id="panes"></div>
  <div id="detail"><div class="msg" id="detailmsg">工程をクリックすると、ここに詳細（工程名の全文・作業内容・課題など）を表示します。</div></div>
</div>
<script type="application/json" id="data">__DATA__</script>
<script src="__VIZ__"></script>
<script>
(function () {
  const data = JSON.parse(document.getElementById("data").textContent);
  const panesEl = document.getElementById("panes");
  const views = [];
  let scale = 1;
  panesEl.style.setProperty("--cols", String(data.panes.length));

  function message(text) {
    panesEl.textContent = "";
    const m = document.createElement("div"); m.className = "msg"; m.textContent = text; panesEl.appendChild(m);
  }
  if (typeof Viz === "undefined") { message("図を表示する部品を読み込めませんでした（ネットワークを確認してください）。工程は下の表と Excel で確認できます。"); return; }

  function applyScale() {
    for (const v of views) { v.svg.style.width = (v.w * scale) + "px"; v.svg.style.height = (v.h * scale) + "px"; }
    document.getElementById("zoom").textContent = Math.round(scale * 100) + "%";
  }
  function fit() {
    // 全体が見える倍率。比較では、同じ縮尺で比べられるよう、両方が収まる小さいほうにそろえる
    let s = 1;
    for (const v of views) {
      if (v.pane.classList.contains("off")) continue;
      const box = v.scroll.getBoundingClientRect();
      s = Math.min(s, (box.width - 16) / v.w, (box.height - 16) / v.h);
    }
    scale = Math.max(data.minFit, Math.min(1, s));
    applyScale();
  }
  function select(id, from) {
    for (const v of views) {
      v.svg.querySelectorAll("g.node").forEach((n) => n.classList.toggle("sel", n.querySelector("title").textContent === id));
    }
    const detail = document.getElementById("detail");
    detail.textContent = "";
    const rows = from.steps[id];
    if (!rows) return;
    const head = document.createElement("div"); head.style.fontWeight = "600"; head.style.marginBottom = "4px";
    head.textContent = from.title; detail.appendChild(head);
    const dl = document.createElement("dl");
    for (const [k, val] of rows) {
      const dt = document.createElement("dt"); dt.textContent = k;
      const dd = document.createElement("dd"); dd.textContent = val;
      dl.appendChild(dt); dl.appendChild(dd);
    }
    detail.appendChild(dl);
  }
  function enableDrag(scroll) {
    let start = null;
    scroll.addEventListener("pointerdown", (e) => {
      if (e.target.closest("g.node")) return;
      start = { x: e.clientX, y: e.clientY, l: scroll.scrollLeft, t: scroll.scrollTop };
      scroll.classList.add("drag"); scroll.setPointerCapture(e.pointerId);
    });
    scroll.addEventListener("pointermove", (e) => {
      if (!start) return;
      scroll.scrollLeft = start.l - (e.clientX - start.x); scroll.scrollTop = start.t - (e.clientY - start.y);
    });
    const end = () => { start = null; scroll.classList.remove("drag"); };
    scroll.addEventListener("pointerup", end); scroll.addEventListener("pointercancel", end);
    scroll.addEventListener("wheel", (e) => {
      if (!e.ctrlKey) return;
      e.preventDefault();
      scale = Math.min(2.5, Math.max(0.3, scale * (e.deltaY < 0 ? 1.1 : 1 / 1.1))); applyScale();
    }, { passive: false });
  }

  Viz.instance().then((viz) => {
    data.panes.forEach((p, i) => {
      const pane = document.createElement("div"); pane.className = "pane";
      const h = document.createElement("h4"); h.textContent = p.title; pane.appendChild(h);
      const scroll = document.createElement("div"); scroll.className = "scroll"; pane.appendChild(scroll);
      panesEl.appendChild(pane);
      let svg;
      try { svg = viz.renderSVGElement(p.dot); } catch (err) { scroll.textContent = "図を描けませんでした。"; return; }
      const vb = svg.viewBox.baseVal;
      svg.removeAttribute("width"); svg.removeAttribute("height");
      scroll.appendChild(svg);
      svg.querySelectorAll("g.node").forEach((n) => {
        n.addEventListener("click", () => select(n.querySelector("title").textContent, p));
      });
      enableDrag(scroll);
      views.push({ pane, scroll, svg, w: vb.width, h: vb.height });
    });
    // 狭い画面では、比較をタブで切り替える
    const tabs = document.getElementById("tabs");
    if (views.length > 1) {
      views.forEach((v, i) => {
        const b = document.createElement("button"); b.textContent = data.panes[i].title.split("　")[0];
        b.addEventListener("click", () => { views.forEach((w, j) => w.pane.classList.toggle("off", i !== j));
          tabs.querySelectorAll("button").forEach((x, j) => x.classList.toggle("on", i === j)); fit(); });
        tabs.appendChild(b);
      });
      const narrow = window.matchMedia("(max-width: 760px)");
      const layout = () => {
        tabs.style.display = narrow.matches ? "" : "none";
        if (narrow.matches) { tabs.querySelector("button").click(); }
        else { views.forEach((w) => w.pane.classList.remove("off")); fit(); }
      };
      narrow.addEventListener("change", layout); layout();
    } else { tabs.style.display = "none"; fit(); }
  }).catch(() => message("図を表示できませんでした。工程は下の表と Excel で確認できます。"));

  document.getElementById("in").onclick = () => { scale = Math.min(2.5, scale * 1.2); applyScale(); };
  document.getElementById("out").onclick = () => { scale = Math.max(0.3, scale / 1.2); applyScale(); };
  document.getElementById("fit").onclick = fit;
  document.getElementById("one").onclick = () => { scale = 1; applyScale(); };
})();
</script></body></html>
"""
