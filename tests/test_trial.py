"""trial.py のテスト（確定した案の試行と効果測定。AI-BIC 3.0 第4段階）。AIは呼ばない。"""

from io import BytesIO

import pytest
from openpyxl import load_workbook

from ai_bic import facts
from ai_bic import trial as tr
from ai_bic.config import JST, load_framework
from ai_bic.demo_client import load_scenarios
from ai_bic.project import TrialMetric, load_project, to_json_bytes
from ai_bic.report import build_report
from tests.test_facts import with_simulations

SAMPLE = next(s for s in load_scenarios() if s["id"] == "production_report")["trial_sample"]


def decided(pid="P2"):
    project = with_simulations()
    facts.decide(project, pid)
    return project


# --- 計画 ---
def test_計画は確定した案のリスクと_確定時点の試算の前提を引き継ぐ():
    project = decided()
    project.simulations["P2"] = project.simulations["P2"].model_copy(update={"minutes_per_case": 999.0})  # 確定後の変更
    plan = tr.new_plan(project)
    assert plan.pid == "P2" and plan.risks == project.decision.proposal.risks
    assert "1件あたりの作業時間（分）：300（デモ値）" in plan.assumptions  # 確定した時点の値
    time = plan.metrics[0]
    assert (time.baseline, time.estimate, time.target, time.actual) == (300, 240, 240, None)  # 20%削減の試算値
    assert [m.kind for m in plan.metrics] == ["数値", "数値", "確認"]


def test_確定していなければ計画を作れない():
    with pytest.raises(ValueError):
        tr.new_plan(with_simulations())


# --- 測定の評価 ---
def test_数値は改善前との差と_目標への到達を計算し_未測定は未測定と示す():
    m = TrialMetric(name="時間", unit="分", baseline=60, target=30, actual=25)
    r = tr.evaluate(m)
    assert r.status == "達成" and r.diff == -35 and r.diff_text == "25 − 60 = -35分（-58.3%）"
    assert tr.evaluate(m.model_copy(update={"actual": 31})).status == "未達"
    assert tr.evaluate(m.model_copy(update={"actual": None})).status == "未測定"
    assert tr.evaluate(m.model_copy(update={"target": None})).status == "目標未設定"
    assert tr.evaluate(TrialMetric(name="件数", target=10, direction="以上", actual=12)).status == "達成"


def test_確認の項目は成功で達成_失敗で未達_未確認なら未測定():
    m = TrialMetric(name="例外", kind="確認", check_target="すべて取り込める")
    assert tr.evaluate(m).status == "未測定"
    assert tr.evaluate(m.model_copy(update={"check_result": "成功"})).status == "達成"
    assert tr.evaluate(m.model_copy(update={"check_result": "失敗"})).status == "未達"


# --- 判定の目安と判断（指示書 6章 5） ---
def test_製造部デモの案2のサンプルは_時間と数量は合格し_例外が残るため修正して再試行が目安になる():
    plan = tr.demo_plan(decided("P2"), SAMPLE)
    assert plan.sample and plan.scope.startswith("プレス2号機の1台分")
    assert [tr.evaluate(m).status for m in plan.metrics] == ["達成", "達成", "未達"]
    g = tr.guide(plan)
    assert g.label == "修正して再試行"
    assert "日付の書き方の違いを取り込めるか" in g.reason


def test_必須の測定が未入力なら判定保留で_判断を記録できない():
    plan = tr.new_plan(decided())
    assert tr.guide(plan).hold
    with pytest.raises(ValueError, match="判定保留"):
        tr.record_decision(plan, "展開する", "理由")


def test_すべて合格なら展開する_合格なしなら見送るが目安になり_判断と理由を記録する():
    plan = tr.demo_plan(decided(), SAMPLE)
    plan.metrics[2] = plan.metrics[2].model_copy(update={"check_result": "成功"})
    assert tr.guide(plan).label == "展開する"
    plan.metrics = [m.model_copy(update={"actual": 99, "check_result": "失敗"}) for m in plan.metrics]
    assert tr.guide(plan).label == "見送る"
    plan.metrics[2] = plan.metrics[2].model_copy(update={"required": False, "check_result": None})  # 参考の項目は未測定でもよい
    assert not tr.guide(plan).hold
    with pytest.raises(ValueError):
        tr.record_decision(plan, "見送る", "  ")
    tr.record_decision(plan, "見送る", "時間が減らなかった")
    assert (plan.decision, plan.reason) == ("見送る", "時間が減らなかった") and plan.decided_at


# --- 保存・再開と Excel ---
def test_試行の計画と実測と判断は案件ファイルに保存して再開できる():
    project = decided()
    project.trial = tr.demo_plan(project, SAMPLE)
    tr.record_decision(project.trial, "修正して再試行", "日付の書き方の例外が残った")
    restored = load_project(to_json_bytes(project))
    assert restored.trial == project.trial


def test_Excelの試行判定シートで_採用理由_試算_合格条件_実測_判断を追える():
    project = decided()
    project.trial = tr.demo_plan(project, SAMPLE)
    tr.record_decision(project.trial, "修正して再試行", "日付の書き方の例外が残った")
    from datetime import datetime

    data = build_report(project.business_info, project.hearing, project.diagnosis, load_framework("general"),
                        datetime(2026, 11, 11, tzinfo=JST), demo=True, project=project)
    ws = load_workbook(BytesIO(data))["試行・判定"]
    text = "\n".join(str(c.value) for row in ws.iter_rows() for c in row if c.value is not None)
    for expected in ["案2：", "採用理由（推奨理由）", "年間削減時間", "1台分の転記・照合の時間", "30分以下", "25 − 60 = -35分",
                     "判定の目安", "修正して再試行（", "日付の書き方の例外が残った", "デモ用の架空のサンプル", "未達"]:
        assert expected in text, expected


def test_案が未確定なら_試行判定シートにそう書く():
    from datetime import datetime

    project = with_simulations()
    data = build_report(project.business_info, project.hearing, project.diagnosis, load_framework("general"),
                        datetime(2026, 11, 11, tzinfo=JST), project=project)
    ws = load_workbook(BytesIO(data))["試行・判定"]
    assert ws["A3"].value == "改善案は、まだ確定していません。"
