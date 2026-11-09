"""report.py のテスト（B05：診断レポートのExcel出力）。

デモのシナリオで診断した結果をExcelにし、openpyxl で読み直して中身を確かめる（AIは呼ばない・料金ゼロ）。
"""

from datetime import datetime
from io import BytesIO

from openpyxl import load_workbook

from ai_bic.config import JST, load_framework
from ai_bic.demo_client import DemoClient, demo_answer, demo_attachments, load_scenarios
from ai_bic.hearing import add_answer, request_next_question, run_diagnosis, start_hearing
from ai_bic.models import AnalysisResult, BusinessInfo, DiagnosisResult
from ai_bic.report import build_report, file_name
from ai_bic.ui import DISCLAIMER

FW = load_framework("general")
CREATED_AT = datetime(2026, 11, 3, 9, 5, tzinfo=JST)


def run_demo(scenario_id):
    """デモを最後まで進め、(業務情報, ヒアリングの記録, 診断結果) を返す。"""
    scenario = next(s for s in load_scenarios() if s["id"] == scenario_id)
    info = BusinessInfo(**scenario["business_info"])
    client = DemoClient(scenario)
    state = start_hearing(FW)
    while (turn := request_next_question(client, info, state, FW)) is not None:
        add_answer(state, demo_answer(scenario, turn.turn_no))
    return info, state, run_diagnosis(client, info, state, FW, demo_attachments(scenario))


def open_report(info, state, result, demo=False):
    return load_workbook(BytesIO(build_report(info, state, result, FW, CREATED_AT, demo)))


def sheet_text(ws) -> str:
    return "\n".join(str(c.value) for row in ws.iter_rows() for c in row if c.value is not None)


def test_3つのシートがそろう():
    wb = open_report(*run_demo("sales_report"))
    assert wb.sheetnames == ["サマリー", "課題", "改善案"]


def test_サマリーの1枚で要点と改善案と最初の一歩が分かる():
    info, state, result = run_demo("sales_report")
    text = sheet_text(open_report(info, state, result)["サマリー"])

    assert info.task_name in text and info.department in text
    assert result.proposals.summary.headline in text
    assert result.proposals.summary.first_action in text
    for p in result.proposals.proposals:
        assert p.title in text
        for step in p.first_steps:
            assert step in text
    assert "（最初に取り組む案）" in text
    assert DISCLAIMER in text  # 「AIによる参考情報・効果は想定」の注意書きが残る
    assert "2026-11-03 09:05" in text


def test_不足項目とその理由がレポートにも残る():
    info, state, result = run_demo("repair_reception")  # 5問の上限で終わり、制約条件が不足
    text = sheet_text(open_report(info, state, result)["サマリー"])
    assert "改善に向けた制約条件（不十分）　足りないこと：予算の目安や、使ってよいツール・社内ルールの有無" in text


def test_課題のシートに根拠の区分と引用が残り_格下げも分かる():
    info, state, result = run_demo("sales_report")
    assert result.downgraded  # デモには、わざと根拠のない「本人の申告」を含めている
    ws = open_report(info, state, result)["課題"]
    text = sheet_text(ws)

    assert ws.max_row == len(result.analysis.issues) + 1  # 見出し＋課題の数
    for issue in result.analysis.issues:
        assert issue.title in text
    assert "要確認：判断材料が不足\nAIは「本人の申告」と判断しましたが、根拠となる発言を入力内容の中に確認できなかった" in text
    assert "想定原因（推測）" in text and "計算式" in text
    ev = next(ev for i in result.analysis.issues for ev in i.evidences)
    assert f"「{ev.quote}」" in text
    assert f"→ 言えること：{ev.supports}" in text  # 引用と主張の対応をレポートでも確かめられる（B04）


def test_改善案のシートに導入前後と想定の効果が残る():
    info, state, result = run_demo("sales_report")
    ws = open_report(info, state, result)["改善案"]
    text = sheet_text(ws)

    assert ws.max_row == len(result.proposals.proposals) + 1
    assert "AFTER（改善後・想定）" in text and "期待できる効果（想定）" in text
    for p in result.proposals.proposals:
        assert p.before in text and p.after in text


def test_課題が0件でもレポートを作れる():
    info, state, _ = run_demo("sales_report")
    empty = DiagnosisResult(
        attachment_names=[],
        analysis=AnalysisResult(business_summary="概要", issues=[], no_issue_viewpoints=[]),
        proposals=None,
        downgraded={},
        missing_item_ids=[],
    )
    wb = open_report(info, state, empty)
    assert "明確な課題は確認できませんでした" in sheet_text(wb["サマリー"])
    assert "改善案は作成していません" in sheet_text(wb["改善案"])


def test_イコールで始まる入力は数式にせず文字として保存する():
    # Excelで開いたときに、利用者の入力やAIの出力が数式として計算されないようにする
    info, state, result = run_demo("sales_report")
    info = info.model_copy(update={"task_name": '=HYPERLINK("http://example.com","クリック")'})
    ws = open_report(info, state, result)["サマリー"]

    cell = next(c for row in ws.iter_rows() for c in row if c.value == info.task_name)
    assert cell.data_type == "s"


def test_デモの結果はデモと分かる():
    info, state, result = run_demo("sales_report")
    assert "デモ（固定の架空データ）" in sheet_text(open_report(info, state, result, demo=True)["サマリー"])
    assert "AI診断" in sheet_text(open_report(info, state, result, demo=False)["サマリー"])


def test_ファイル名は作成日時から作る():
    assert file_name(CREATED_AT) == "AI-BIC_診断レポート_20261103_0905.xlsx"


def test_すべてのセルをメイリオで表示する():
    wb = open_report(*run_demo("sales_report"))
    fonts = {c.font.name for ws in wb for row in ws.iter_rows() for c in row if c.value is not None}
    assert fonts == {"メイリオ"}


# ---------------------------------------------------------------
# 改善効果の試算（AI-BIC 2.0 機能A）
# ---------------------------------------------------------------
def simulation_book(mode="ai", title=None, **inputs):
    from ai_bic.project import new_project
    from ai_bic.report import build_simulation_report
    from ai_bic.simulation import SimulationInputs, blank_scenarios

    info, state, result = run_demo("sales_report")
    project = new_project(mode, info, state, result, demo_scenario_id="sales_report" if mode == "demo" else None)
    if title is not None:
        project.diagnosis.proposals.proposals[0].title = title
    project.simulations["P1"] = SimulationInputs(**inputs)
    project.scenarios["P1"] = blank_scenarios()
    return load_workbook(BytesIO(build_simulation_report(project, CREATED_AT)))


FULL_SIM = dict(task_name="月次集計", minutes_per_case=30, cases_per_month=200, hourly_cost=3000, reduction_rate=40,
                initial_cost=500_000, monthly_running_cost=20_000, other_initial_cost=100_000,
                sources={"minutes_per_case": "ヒアリング"})


def test_試算のExcelは3シナリオの結果と入力値の出どころと計算式を残す():
    wb = simulation_book(**FULL_SIM)
    assert wb.sheetnames == ["試算結果", "入力値", "計算式"]
    rows = list(wb["試算結果"].iter_rows(min_row=2, max_row=4, values_only=True))
    assert [r[1] for r in rows] == ["悲観", "標準", "楽観"]
    standard = rows[1]
    assert standard[2] == 480 and standard[3] == 1_440_000 and standard[7] == "6か月"
    inputs = sheet_text(wb["入力値"])
    assert "ヒアリング" in inputs and "自動（標準の値 × 倍率" in inputs
    assert "30分 × 200件 ÷ 60 ＝ 100時間" in sheet_text(wb["計算式"])
    assert "現金" in sheet_text(wb["試算結果"])  # 削減効果額は時間の金額換算であることを明記


def test_未入力の試算は空欄と未入力で残す():
    wb = simulation_book(task_name="月次集計")
    standard = list(wb["試算結果"].iter_rows(min_row=3, max_row=3, values_only=True))[0]
    assert standard[2] is None and standard[7] == "未入力の項目があるため計算できません"
    assert "未入力" in sheet_text(wb["入力値"])


def test_デモの試算はデモと分かる():
    assert "架空のサンプル" in sheet_text(simulation_book("demo", **FULL_SIM)["試算結果"])


def test_試算のExcelでもイコールで始まる改善案名は数式にしない():
    wb = simulation_book(title="=HYPERLINK(1)", **FULL_SIM)
    cell = wb["試算結果"]["A2"]
    assert cell.value == "案1：=HYPERLINK(1)"  # 先頭は「案1：」だが、念のため全セルを確かめる
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                assert c.data_type != "f"


# ---------------------------------------------------------------
# 業務フロー（AI-BIC 2.0 機能B）
# ---------------------------------------------------------------
def flow_book(mode="demo"):
    from ai_bic.demo_client import DemoClient
    from ai_bic.flow_ai import generate_flows
    from ai_bic.project import new_project
    from ai_bic.report import build_flow_report

    info, state, result = run_demo("sales_report")
    project = new_project(mode, info, state, result, demo_scenario_id="sales_report" if mode == "demo" else None)
    scenario = next(s for s in load_scenarios() if s["id"] == "sales_report")
    generate_flows(DemoClient(scenario), project)
    return load_workbook(BytesIO(build_flow_report(project, CREATED_AT)))


def test_業務フローのExcelは比較サマリーと工程一覧を残す():
    wb = flow_book()
    assert wb.sheetnames == ["比較サマリー", "現状フロー", "改善後フロー（案1）", "改善後フロー（案2）", "改善後フロー（案3）"]
    summary = sheet_text(wb["比較サマリー"])
    assert "案3：" in summary and "廃止1" in summary and "二重に数えない" in summary
    assert "デモ" in summary
    as_is = sheet_text(wb["現状フロー"])
    assert "商品ごとの売上を集計用ファイルに転記する" in as_is and "要確認" in as_is
    assert "S8（数字が合わない）" in as_is  # 次の工程と分岐の条件（S7 部長の確認 → S8 原因調査）



def test_情報が足りないまま分析した場合は_仮定条件もレポートに残す():
    """AI-BIC 2.0 ヒアリングの改善 7：未確認事項と仮定条件を明示する。"""
    info, state, result = run_demo("repair_reception")  # 制約条件（H6）が不十分のまま分析
    summary = sheet_text(open_report(info, state, result)["サマリー"])
    assert "仮定条件" in summary and "改善の制約条件が未確認のため" in summary
    info, state, result = run_demo("sales_report")  # すべて充足
    assert "仮定条件" not in sheet_text(open_report(info, state, result)["サマリー"])
