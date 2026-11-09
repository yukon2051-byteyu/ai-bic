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

表示は st.html（JavaScript を許可）でページに直接書き込む。以前の st.components.v1.html（iframe）は Streamlit で
削除予定のため置き換えた。iframe で守られなくなったため、見た目の指定と要素の探し方は、部品の入れ物の中だけに限る。
"""

import hashlib
import json

import streamlit as st

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
    """図の表示部品の HTML（ページに直接書き込む断片）。データは JSON にして、処理のスクリプトの中に直接書く。

    iframe を使わないため、見た目の指定はすべて部品の入れ物（固有の id）の中だけに限る（アプリ全体に漏らさない）。
    固有の id は中身から作る（同じ内容なら同じ id。Streamlit の再表示で部品を作り直さないため）。
    """
    # 「<」はすべて < と書く（JavaScript では同じ文字）。Streamlit の安全チェック（DOMPurify）は「<英字」「</」を
    # 含むスクリプトを丸ごと取り除くため（図の記述の <TABLE> など）。工程名の「</script>」でスクリプトが終わることも防ぐ
    data = json.dumps({"panes": panes, "minFit": MIN_FIT_SCALE}, ensure_ascii=False).replace("<", "\\u003c")
    uid = "bicflow-" + hashlib.sha1(f"{height}{data}".encode("utf-8")).hexdigest()[:12]
    return (TEMPLATE.replace("__UID__", uid).replace("__HEIGHT__", str(height))
            .replace("__VIZ__", VIZ_URL).replace("__DATA__", data))


def render(panes: list[dict], height: int) -> None:
    # st.components.v1.html（iframe）は Streamlit で削除予定のため、後継の st.html で JavaScript を許可して表示する
    st.html(build_html(panes, height), unsafe_allow_javascript=True)


TEMPLATE = r"""<div id="__UID__" class="bicflow" style="height:__HEIGHT__px">
<style>
  #__UID__ { --ink:#14161A; --sub:#5F6368; --line:#C8D2E0; --accent:#1B2B4B; --bg:#FFFFFF; --soft:#F6F6F7;
    display:flex; flex-direction:column; gap:6px; color:var(--ink);
    font-family:"Noto Sans JP","Hiragino Sans","Yu Gothic","Meiryo",sans-serif; }
  #__UID__ * { box-sizing:border-box; }
  #__UID__ .bar { display:flex; flex-wrap:wrap; align-items:center; gap:6px; font-size:12px; }
  #__UID__ .bar button { font:inherit; padding:3px 10px; border:1px solid var(--line); border-radius:4px;
    background:var(--bg); color:var(--accent); cursor:pointer; }
  #__UID__ .bar button:hover { border-color:var(--accent); }
  #__UID__ .bar button:focus-visible { outline:2px solid #A88B4E; outline-offset:2px; }
  #__UID__ .bar .tabs button.on { background:var(--accent); color:#fff; border-color:var(--accent); }
  #__UID__ .bar .hint { color:var(--sub); }
  #__UID__ .bar .zoom { min-width:44px; text-align:center; color:var(--sub); }
  #__UID__ .panes { flex:1; min-height:0; display:grid; gap:8px; grid-template-columns:repeat(var(--cols),minmax(0,1fr)); }
  #__UID__ .pane { display:flex; flex-direction:column; min-height:0; border:1px solid var(--line); border-radius:4px; background:var(--bg); }
  #__UID__ .pane h4 { margin:0; padding:4px 8px; font-size:12px; font-weight:600; border-bottom:1px solid var(--line); background:var(--soft); }
  #__UID__ .scroll { flex:1; min-height:0; overflow:auto; cursor:grab; }
  #__UID__ .scroll.drag { cursor:grabbing; }
  #__UID__ .scroll svg { display:block; margin:0 auto; }
  #__UID__ .scroll g.node { cursor:pointer; }
  #__UID__ .scroll g.node.sel > path, #__UID__ .scroll g.node.sel > polygon { stroke-width:3.5px; stroke:#1B2B4B; }
  #__UID__ .detail { height:118px; overflow:auto; border:1px solid var(--line); border-radius:4px; padding:6px 10px;
    font-size:12px; background:var(--bg); }
  #__UID__ .detail dl { display:grid; grid-template-columns:7em 1fr; gap:2px 10px; margin:0; }
  #__UID__ .detail dt { color:var(--sub); } #__UID__ .detail dd { margin:0; }
  #__UID__ .msg { padding:12px; color:var(--sub); font-size:13px; }
  @media (max-width: 760px) { #__UID__ .panes { --cols:1 !important; } #__UID__ .pane.off { display:none; } }
</style>
  <div class="bar">
    <span class="tabs"></span>
    <button class="out" title="縮小">－</button><span class="zoom"></span><button class="in" title="拡大">＋</button>
    <button class="fit">全体を表示</button><button class="one">100%</button>
    <span class="hint">工程をクリックすると詳細を表示／ドラッグで移動／Ctrl＋ホイールで拡大縮小</span>
  </div>
  <div class="panes"></div>
  <div class="detail"><div class="msg">工程をクリックすると、ここに詳細（工程名の全文・作業内容・課題など）を表示します。</div></div>
<script>
(function () {
  const root = document.getElementById("__UID__");
  if (!root || root.dataset.ready) return;  // 同じ部品を2回動かさない
  root.dataset.ready = "1";
  const $ = (sel) => root.querySelector(sel);
  // 図のデータ（Python で JSON にしたもの）。Streamlit はデータだけのスクリプト要素を取り除くため、処理の中に直接書く
  const data = __DATA__;
  const panesEl = $(".panes");
  const views = [];
  let scale = 1;
  panesEl.style.setProperty("--cols", String(data.panes.length));

  function message(text) {
    panesEl.textContent = "";
    const m = document.createElement("div"); m.className = "msg"; m.textContent = text; panesEl.appendChild(m);
  }
  // 図を描く部品（viz.js）は、ページに一度だけ読み込む（再表示のたびに読み込み直さない）
  function loadViz() {
    if (window.Viz) return Promise.resolve();
    if (!window.__bicVizLoading) {
      window.__bicVizLoading = new Promise((resolve, reject) => {
        const s = document.createElement("script");
        s.src = "__VIZ__"; s.onload = resolve; s.onerror = reject;
        document.head.appendChild(s);
      });
    }
    return window.__bicVizLoading;
  }
  function applyScale() {
    for (const v of views) { v.svg.style.width = (v.w * scale) + "px"; v.svg.style.height = (v.h * scale) + "px"; }
    $(".zoom").textContent = Math.round(scale * 100) + "%";
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
    const detail = $(".detail");
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

  loadViz().then(() => Viz.instance()).then((viz) => {
    data.panes.forEach((p) => {
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
    const tabs = $(".tabs");
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
  }).catch(() => message("図を表示する部品を読み込めませんでした（ネットワークを確認してください）。工程は下の表と Excel で確認できます。"));

  $(".in").onclick = () => { scale = Math.min(2.5, scale * 1.2); applyScale(); };
  $(".out").onclick = () => { scale = Math.max(0.3, scale / 1.2); applyScale(); };
  $(".fit").onclick = fit;
  $(".one").onclick = () => { scale = 1; applyScale(); };
})();
</script>
</div>
"""
