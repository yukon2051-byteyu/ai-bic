"""診断レポート（Excel）の作成（B05：結果を手元に残し、関係者に渡せるようにする）。

結果画面と同じ内容を、3つのシートにまとめる。
- サマリー：1枚で要点が分かる（要点・最重要課題・改善案と最初の一歩・不足項目・注意書き）
- 課題：課題ごとの根拠と「事実・推測・要確認」の区別
- 改善案：導入前後の比較、効果（想定）、最初の一歩など

ファイルはその場で作って画面から渡すだけで、サーバーには保存しない。
画面と同じ言葉を使うため、表示の文言は ui.py のものを使う。
"""

from datetime import datetime
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from ai_bic import simulation as sim
from ai_bic.flow import Flow, compare_flows
from ai_bic.models import BusinessInfo, DiagnosisResult, Framework, HearingState
from ai_bic.project import Project, proposal_ids
from ai_bic.ui import DISCLAIMER, EVIDENCE_TEXT, evidence_source, missing_item_text

TITLE = "AI-BIC 業務改善診断レポート"
HEADER_FILL = PatternFill("solid", fgColor="E8ECEF")
WRAP = Alignment(wrap_text=True, vertical="top")
FONT_NAME = "メイリオ"  # 日本語が読みやすいWindows標準のフォント。ない環境では、Excelが別のフォントで表示する
NORMAL = Font(name=FONT_NAME)
BOLD = Font(name=FONT_NAME, bold=True)


def file_name(created_at: datetime) -> str:
    """ダウンロードするファイルの名前（業務名は使わない：ファイル名に使えない文字を避けるため）。"""
    return f"AI-BIC_診断レポート_{created_at:%Y%m%d_%H%M}.xlsx"


def build_report(
    info: BusinessInfo,
    state: HearingState,
    result: DiagnosisResult,
    framework: Framework,
    created_at: datetime,
    demo: bool = False,
) -> bytes:
    """診断結果をExcelファイル（.xlsx）の中身にして返す。"""
    wb = Workbook()
    _summary_sheet(wb.active, info, state, result, framework, created_at, demo)
    _issues_sheet(wb.create_sheet("課題"), result, framework)
    _proposals_sheet(wb.create_sheet("改善案"), result)

    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------
# シートごとの内容
# ---------------------------------------------------------------
def _summary_sheet(ws, info, state, result, framework, created_at, demo) -> None:
    ws.title = "サマリー"
    analysis, proposals = result.analysis, result.proposals
    issues_by_id = {i.issue_id: i for i in analysis.issues}

    rows: list[tuple[str, str]] = [
        ("作成日時", f"{created_at:%Y-%m-%d %H:%M}"),
        ("診断の種類", "デモ（固定の架空データ）" if demo else "AI診断"),
        ("部署", info.department),
        ("業務名", info.task_name),
        ("業務の概要", analysis.business_summary),
        ("ヒアリング", state.finished_reason or ""),
    ]
    if result.attachment_names:
        rows.append(("診断に使った資料", "、".join(result.attachment_names)))

    if proposals:
        top = issues_by_id.get(proposals.summary.top_issue_id)
        rows.append(("要点", proposals.summary.headline))
        if top:
            rows.append(("最も重要な課題", f"{top.title}（{_issue_label(top, result)}）"))
        rows.append(("最初に取り組むこと", proposals.summary.first_action))
        for no, p in enumerate(proposals.proposals, start=1):
            mark = "（最初に取り組む案）" if no == 1 else ""
            rows.append((f"改善案{no}{mark}", f"{p.title}\n最初の一歩：\n{_bullets(p.first_steps)}"))
    else:
        rows.append(("要点", "お聞きした範囲では、明確な課題は確認できませんでした。改善案は作成していません。"))

    issue_lines = [f"{i.title}（{_issue_label(i, result)}）" for i in analysis.issues]
    rows.append(("抽出された課題", _bullets(issue_lines) or "なし"))

    items = {item.id: item for item in framework.hearing_items}
    if result.missing_item_ids:
        lines = [missing_item_text(items[i], state.item_status.get(i)) for i in result.missing_item_ids]
        rows.append(("情報が不足している項目", "結果は参考としてご覧ください。\n" + _bullets(lines)))
    rows.append(("ご注意", DISCLAIMER))

    _put(ws, 1, 1, TITLE).font = Font(name=FONT_NAME, bold=True, size=14)
    for r, (label, value) in enumerate(rows, start=3):
        _put(ws, r, 1, label).font = BOLD
        _put(ws, r, 2, value)
    _format(ws, [22, 90])


def _issues_sheet(ws, result: DiagnosisResult, framework: Framework) -> None:
    viewpoints = {v.id: v.name for v in framework.viewpoints}
    headers = [
        "No", "課題", "重要度", "根拠の区分", "観点", "内容", "判断根拠（ご本人の発言・添付資料）と、そこから言えること",
        "計算式", "想定原因（推測）", "業務への影響", "重要度の理由", "追加で確認したいこと",
    ]
    rows = []
    for no, issue in enumerate(result.analysis.issues, start=1):
        quotes = [
            f"「{ev.quote}」（{evidence_source(ev, result.attachment_names)}）\n　→ 言えること：{ev.supports}"
            for ev in issue.evidences
        ]
        rows.append([
            no,
            issue.title,
            issue.severity,
            _issue_label(issue, result, with_severity=False),
            "・".join(viewpoints.get(v, v) for v in issue.viewpoint_ids),
            issue.summary,
            _bullets(quotes) or "根拠となる発言はありません",
            issue.calculation or "",
            issue.assumed_cause,
            issue.impact,
            issue.severity_reason,
            _bullets(issue.followup_questions),
        ])
    _table(ws, headers, rows, [5, 28, 8, 30, 16, 40, 55, 24, 35, 35, 35, 40])


def _proposals_sheet(ws, result: DiagnosisResult) -> None:
    headers = [
        "No", "改善案", "種類", "導入難易度", "対応する課題", "内容", "BEFORE（今）", "AFTER（改善後・想定）",
        "期待できる効果（想定）", "最初の一歩（今週から始められること）", "推奨理由",
        "より簡単な方法では不十分な理由", "想定リスク", "実施前に確認すべき事項",
    ]
    rows = []
    for no, p in enumerate(result.proposals.proposals if result.proposals else [], start=1):
        rows.append([
            no,
            p.title + ("\n（最初に取り組む案）" if no == 1 else ""),
            p.category,
            p.difficulty,
            "・".join(p.target_issue_ids),
            p.description,
            p.before,
            p.after,
            p.expected_effect,
            _bullets(p.first_steps),
            p.reason,
            p.why_not_simpler or "",
            _bullets(p.risks),
            _bullets(p.prerequisites),
        ])
    if not rows:
        rows.append(["", "課題が確認できなかったため、改善案は作成していません。"] + [""] * (len(headers) - 2))
    _table(ws, headers, rows, [5, 28, 14, 8, 10, 40, 40, 40, 40, 40, 35, 30, 30, 30])


# ---------------------------------------------------------------
# 改善効果の試算（AI-BIC 2.0 機能A）
# ---------------------------------------------------------------
def simulation_file_name(created_at: datetime) -> str:
    return f"AI-BIC_効果試算_{created_at:%Y%m%d_%H%M}.xlsx"


def build_simulation_report(project: Project, created_at: datetime) -> bytes:
    """効果試算を Excel にする。3シート：試算結果・入力値（出どころつき）・計算式。"""
    titles = {pid: f"案{no}：{p.title}" for no, (pid, p) in enumerate(_proposals(project), start=1)}
    results, inputs, formulas = [], [], []
    for pid, title in titles.items():
        base = project.simulations.get(pid)
        if base is None:
            continue
        for scenario in project.scenarios.get(pid) or sim.blank_scenarios():
            values = sim.scenario_inputs(base, scenario)
            r = sim.simulate(values)
            results.append([
                title, scenario.name, _num(r.annual_saved_hours), _num(r.annual_saved_yen), _num(r.annual_running_cost),
                _num(r.annual_net_yen), _num(r.initial_investment), sim.payback_text(r), _num(r.first_year_net_yen),
            ])
            fields = sim.NUMBER_FIELDS if scenario.name == "標準" else sim.SCENARIO_FIELDS
            for name in fields:
                value = getattr(values, name)
                source = values.source_of(name) or ""
                if scenario.name != "標準" and getattr(scenario, name) is None and value is not None:
                    source = f"自動（標準の値 × 倍率。{source}）"
                inputs.append([title, scenario.name, sim.label(name), _num(value), source or "未入力"])
            formulas += [[title, scenario.name, f] for f in r.formulas]

    wb = Workbook()
    ws = wb.active
    ws.title = "試算結果"
    _table(ws, ["改善案", "シナリオ", "年間削減時間（時間）", "年間削減効果額（円）", "年間運用費（円）",
                "年間純効果額（円）", "初期投資額（円）", "投資回収期間", "初年度純効果額（円）"],
           results, [34, 10, 14, 16, 14, 16, 14, 22, 16])
    notes = [
        f"作成日時：{created_at:%Y-%m-%d %H:%M}",
        "試算は入力した値による想定です。実測の結果ではありません。",
        sim.NOTE_TIME_VALUE,
        "悲観・楽観の「自動」の値は、標準の値に一定の倍率をかけた仮の幅です（根拠のある予測ではありません）。",
    ]
    if project.mode == "demo":
        notes.insert(0, "デモ：架空のサンプルによる試算です（AIは使用していません）。")
    for offset, text in enumerate(notes, start=len(results) + 3):
        _put(ws, offset, 1, text)
    _table(wb.create_sheet("入力値"), ["改善案", "シナリオ", "項目", "値", "出どころ"], inputs, [34, 10, 26, 14, 34])
    _table(wb.create_sheet("計算式"), ["改善案", "シナリオ", "計算式"], formulas, [34, 10, 90])
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------
# 業務フロー（AI-BIC 2.0 機能B）：As-Is／To-Be の比較と工程一覧
# ---------------------------------------------------------------
def flow_file_name(created_at: datetime) -> str:
    return f"AI-BIC_業務フロー_{created_at:%Y%m%d_%H%M}.xlsx"


STEP_HEADERS = ["工程ID", "工程名", "種類", "担当", "作業内容", "使用システム", "手作業", "1件あたりの時間（分）",
                "時間の出どころ", "実施割合（%）", "待ち・手戻り", "課題・ボトルネック", "要確認", "変化", "改善内容", "次の工程"]
STEP_WIDTHS = [7, 28, 7, 16, 30, 18, 7, 12, 12, 10, 22, 30, 7, 9, 34, 22]


def build_flow_report(project: Project, created_at: datetime) -> bytes:
    """業務フローを Excel にする。比較サマリー・現状フロー・改善後フロー（改善案ごと）のシート。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "比較サマリー"
    rows = []
    for no, (pid, proposal) in enumerate(_proposals(project), start=1):
        to_be = project.to_be.get(pid)
        if project.as_is is None or to_be is None:
            continue
        d = compare_flows(project.as_is, to_be)
        rows.append([
            f"案{no}：{proposal.title}", len(project.as_is.steps), len(to_be.steps),
            _num(d.as_is_minutes.minutes_per_case), _num(d.to_be_minutes.minutes_per_case),
            "、".join(f"{k}{v}" for k, v in d.counts.items()) or "なし",
            len(d.as_is_minutes.unknown_step_ids) + len(d.to_be_minutes.unknown_step_ids),
            _bullets(project.kept_controls.get(pid, [])),
        ])
    _table(ws, ["改善案", "工程数（現状）", "工程数（改善後）", "1件あたりの時間（現状・分）", "1件あたりの時間（改善後・分）",
                "変化した工程", "時間が不明な工程の数", "品質管理・内部統制のために残した工程"],
           rows, [34, 10, 10, 14, 14, 26, 12, 50])
    notes = [
        f"作成日時：{created_at:%Y-%m-%d %H:%M}",
        "1件あたりの時間は、工程ごとの「時間×実施割合」を1回ずつ足した値です（分岐や差し戻しで二重に数えない）。"
        "時間が不明な工程は含みません。改善後は「廃止」の工程を含みません。",
        "時間の出どころが「AI推定」の値は、AIの推定（参考値）です。",
    ]
    if project.mode == "demo":
        notes.insert(0, "デモ：事前に作った架空のサンプルです（AIは使用していません）。")
    for offset, text in enumerate(notes, start=len(rows) + 3):
        _put(ws, offset, 1, text)
    if project.as_is is not None:
        _table(wb.create_sheet("現状フロー"), STEP_HEADERS, _step_rows(project.as_is), STEP_WIDTHS)
    for no, pid in enumerate(proposal_ids(project), start=1):
        if pid in project.to_be:
            _table(wb.create_sheet(f"改善後フロー（案{no}）"), STEP_HEADERS, _step_rows(project.to_be[pid]), STEP_WIDTHS)
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _step_rows(flow: Flow) -> list[list]:
    rows = []
    for s in flow.steps:
        nexts = [f"{e.target}（{e.label}）" if e.label else e.target for e in flow.edges if e.source == s.id]
        manual = {True: "はい", False: "いいえ", None: "不明"}[s.manual]
        rows.append([
            s.id, s.name, s.kind, s.actor, s.description, s.system, manual, s.minutes_per_case,
            s.minutes_source or "", s.occurrence_rate, s.wait_or_rework, s.problem,
            "要確認" if s.needs_confirmation else "", s.change, s.change_note, "、".join(nexts),
        ])
    return rows


def _proposals(project: Project):
    proposals = project.diagnosis.proposals.proposals if project.diagnosis.proposals else []
    return list(zip(proposal_ids(project), proposals))


def _num(value: float | None):
    """Excel に入れる数値（小数1桁に丸める）。未計算は空欄。"""
    return None if value is None else round(value, 1)


# ---------------------------------------------------------------
# 共通の部品
# ---------------------------------------------------------------
def _issue_label(issue, result: DiagnosisResult, with_severity: bool = True) -> str:
    """課題の「重要度・根拠の区分」。根拠を確認できず格下げした課題は、その旨も残す（画面と同じ）。

    サマリー（with_severity=True）は短く「格下げ」とだけ書き、課題のシートには理由まで書く。
    """
    label = EVIDENCE_TEXT[issue.evidence_type]
    reason = result.downgraded.get(issue.issue_id)
    if reason:
        label += "（格下げ）" if with_severity else f"\n{reason}"
    return f"重要度 {issue.severity}・{label}" if with_severity else label


def _bullets(values: list[str]) -> str:
    return "\n".join(f"・{v}" for v in values)


def _put(ws: Worksheet, row: int, col: int, value):
    """セルに値を書く。「=」で始まる文字も、数式ではなく文字として扱う。

    AIの出力や利用者の入力が「=」で始まると、openpyxl は数式として保存し、
    Excelで開いたときに計算されてしまう（数式の埋め込み対策）。
    """
    cell = ws.cell(row=row, column=col, value=value)
    if isinstance(value, str) and value.startswith("="):
        cell.data_type = "s"
    cell.alignment = WRAP
    cell.font = NORMAL
    return cell


def _table(ws: Worksheet, headers: list[str], rows: list[list], widths: list[int]) -> None:
    for c, header in enumerate(headers, start=1):
        cell = _put(ws, 1, c, header)
        cell.font = BOLD
        cell.fill = HEADER_FILL
    for r, values in enumerate(rows, start=2):
        for c, value in enumerate(values, start=1):
            _put(ws, r, c, value)
    ws.freeze_panes = "C2"  # 見出しと課題名（改善案名）を固定して、横に長い表を読みやすくする
    _format(ws, widths)


def _format(ws: Worksheet, widths: list[int]) -> None:
    for c, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(c)].width = width
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True  # 印刷するときは横幅を1ページに収める
