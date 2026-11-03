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
    assert result.downgraded_issue_ids  # デモには、わざと根拠のない「事実」を含めている
    ws = open_report(info, state, result)["課題"]
    text = sheet_text(ws)

    assert ws.max_row == len(result.analysis.issues) + 1  # 見出し＋課題の数
    for issue in result.analysis.issues:
        assert issue.title in text
    assert "要確認：判断材料が不足（AIは「事実」と判断したが" in text
    assert "想定原因（推測）" in text
    quote = next(ev.quote for i in result.analysis.issues for ev in i.evidences)
    assert f"「{quote}」" in text


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
        downgraded_issue_ids=[],
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
