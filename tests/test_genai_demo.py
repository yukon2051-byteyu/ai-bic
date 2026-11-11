"""生成AIの活用案（AI-BIC 3.4 段階2）：デモ・レビュー・試算・試行。AIは呼ばない（固定データのデモ）。"""

import json
from io import BytesIO

from openpyxl import load_workbook

from ai_bic import genai
from ai_bic import trial as tr
from ai_bic.demo_client import load_scenarios
from ai_bic.models import Proposal
from ai_bic.project import load_project, to_json_bytes
from tests.test_app import INQUIRY, PRODUCTION, all_text, new_app, open_tab, result_screen, work  # noqa: F401
from tests.test_app import secrets  # noqa: F401（フィクスチャ）

SCENARIOS = {s["id"]: s for s in load_scenarios()}


def test_生成AIのデモはデモ一覧の1番目で_初期表示になる():
    assert load_scenarios()[0]["id"] == "inquiry_report"


# --- 固定データの確かめ ---
def test_問い合わせデモの案1は生成AI活用で_入力と出力と人の確認と試行の範囲が分かる():
    p = Proposal(**SCENARIOS["inquiry_report"]["proposals"]["proposals"][0])
    assert genai.uses_genai(p) and p.genai.approved
    assert "氏名・電話番号を消した" in p.genai.ai_input and "原文と照らし合わせ" in p.genai.human_check
    assert "20件" in p.genai.first_trial and "確認・修正" in p.genai.effect
    others = [Proposal(**x) for x in SCENARIOS["inquiry_report"]["proposals"]["proposals"][1:]]
    assert [genai.label(x) for x in others] == ["", ""]  # 件数の集計はExcelのルールで行い、生成AIは使わない


def test_製造部デモは数値の取り込みはPower_Queryの案で_異常メモの分類だけを生成AIの併用として示し承認の確認を求める():
    p2 = Proposal(**SCENARIOS["production_report"]["proposals"]["proposals"][1])
    assert p2.category == "既存ツールの活用" and genai.assists(p2)
    assert "異常メモ" in p2.genai.target_step and "数値の集計とは別" in p2.genai.target_step
    assert not p2.genai.approved and genai.condition_text(p2) == genai.NOTICE


def test_クラウド不可の交通費精算デモには_生成AIの案を入れていない():
    assert not any(p.get("genai") or p["category"] == "生成AI活用"
                   for p in SCENARIOS["travel_expense"]["proposals"]["proposals"])


def test_承認の文に否定の続きがあっても_前半の承認を読み取る():
    assert genai.allowed("社内で承認された生成AI（社内向けのチャット）は使えますが、お客様の氏名は入力してはいけない決まりです。")
    assert not genai.allowed("生成AIは使えませんが、Excelは使えます。")


# --- 試行：生成AIの案の測定項目は「測定候補」 ---
def test_生成AIの案の測定項目は測定候補として入り_必須を決めるまで判定保留():
    from tests.test_trial import decided

    project = decided("P1")
    project.decision.proposal = Proposal(**SCENARIOS["inquiry_report"]["proposals"]["proposals"][0])
    plan = tr.new_plan(project)
    labels = [tr.metric_label(m) for m in plan.metrics]
    assert labels[:3] == ["確認・修正込みの総作業時間（分／20件）", "誤分類の件数（件／20件）", "原文で裏付けられない記述の件数（件／20件）"]
    assert not any(m.required for m in plan.metrics) and all(tr.CANDIDATE_NOTE in m.note for m in plan.metrics)
    g = tr.guide(plan)
    assert g.hold and "必須の測定項目が決まっていません" in g.reason
    plan.metrics[1].required = True  # 利用者が誤分類の件数を必須にし、合格基準を決めて測った
    plan.metrics[1].actual = 1
    assert tr.guide(plan).label == "展開する"


# --- 画面：初期表示のデモで、比較 → レビュー → 確定 → 試行 → 保存・Excel ---
def test_生成AIのデモで比較_レビュー_試算_確定_試行_保存_Excelまで追える(secrets):  # noqa: F811
    at = result_screen(secrets, new_app(secrets, scenario=None), area=None)
    assert at.session_state.business_info.task_name == "問い合わせメモの分類と週次報告"
    text = all_text(open_tab(at, "改善案を選ぶ"))
    assert "◆ 生成AI活用" in text and "生成AIの対象工程：週次報告の前に" in text
    assert "社内で承認された生成AIが使える（業務情報・回答で確認済み）" in text
    text = all_text(work(at, "brief"))
    for x in ["AIへの入力と出力", "人が確認すること", "最初に試す範囲", "匿名化した先週分のメモ20件"]:
        assert x in text, x
    assert "確認・修正する時間を差し引いた正味の値" in all_text(work(at, "sim"))  # 試算の案内
    at = work(at, "review")
    at.button(key="review_run_P1").click().run()
    review = at.session_state.project.reviews["P1"].result
    assert {f.viewpoint for f in review.findings} == {"生成AIの利用"}
    titles = " ".join(f.title for f in review.findings)
    for x in ["誤分類", "代表例", "確認・修正", "個人情報"]:  # 誤分類・根拠の追跡・確認の負担・入力データ
        assert x in titles, x
    at.button(key="review_decide_P1").click().run()
    at.button(key="review_trial_P1").click().run()
    plan = at.session_state.project.trial
    assert not any(m.required for m in plan.metrics)
    assert "必須の測定項目が決まっていません" in all_text(at)
    at.button(key="trial_demo").click().run()
    plan = at.session_state.project.trial
    assert plan.sample and tr.guide(plan).label == "修正して再試行"
    assert "匿名化したメモ20件" in all_text(at)
    # 保存して読み込んでも、案の生成AIの欄と測定項目が残る。Excel にも出る
    loaded = load_project(to_json_bytes(at.session_state.project))
    assert loaded.diagnosis.proposals.proposals[0].genai.target_step.startswith("週次報告の前に")
    assert [m.name for m in loaded.trial.metrics] == [m.name for m in plan.metrics]
    from ai_bic.report import build_report
    from ai_bic.config import load_framework

    project = at.session_state.project
    wb = load_workbook(BytesIO(build_report(project.business_info, project.hearing, project.diagnosis,
                                            load_framework("general"), project.created_at, demo=True, project=project)))
    sheet = "\n".join(str(c.value) for r in wb["改善案"].iter_rows() for c in r if c.value)
    assert "生成AI活用：社内で承認された生成AIが使える" in sheet
    trial_sheet = "\n".join(str(c.value) for r in wb["試行・判定"].iter_rows() for c in r if c.value)
    assert "確認・修正込みの総作業時間（分／20件）" in trial_sheet and "修正して再試行" in trial_sheet
    raw = json.loads(to_json_bytes(project))
    raw["diagnosis"]["proposals"]["proposals"][0].pop("genai")  # 旧JSON の形でも読み込める
    assert load_project(json.dumps(raw, ensure_ascii=False).encode("utf-8")).diagnosis.proposals.proposals[0].genai is None
