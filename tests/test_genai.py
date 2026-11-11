"""生成AIの活用案（AI-BIC 3.4）のテスト：承認の根拠の確かめ方・クラウド不可の案件での扱い・表示・Excel・旧JSON。AIは呼ばない。"""

import json
from io import BytesIO

from openpyxl import load_workbook

from ai_bic import genai
from ai_bic.config import load_framework
from ai_bic.models import BusinessInfo, GenAIUse, HearingState, HearingTurn
from ai_bic.overview import compare_rows
from ai_bic.project import load_project, to_json_bytes
from ai_bic.report import build_report
from ai_bic.ui import compare_table, proposal_brief, proposal_card
from tests.fakes import make_proposal, make_proposal_result
from tests.test_project import make_project

USE = dict(target_step="週次報告の前に、問い合わせの自由記述を分類する", ai_input="匿名化した問い合わせメモ",
           ai_output="分類候補と代表例", human_check="担当者が原文と照らし合わせ、誤分類を直す",
           effect="分類から集計までの時間が減る可能性。確認・修正の時間も含めて測る",
           conditions="利用承認／入力できるデータの範囲", risks="誤分類／原文にない内容の追加",
           first_trial="匿名化した過去20件で、人手とAI補助ありを比べる")


def genai_proposal(approved=False, **kw):
    return make_proposal(category="生成AI活用", genai=GenAIUse(**USE, approved=approved), **kw)


def state(*answers):
    return HearingState(turns=[HearingTurn(turn_no=i, item_id="H6", question="q", answer=a)
                               for i, a in enumerate(answers, start=1)])


INFO = BusinessInfo(company_name=None, department="お客様窓口", task_name="問い合わせの分類", overview="問い合わせメモを読んで分類している",
                    pain_points="分類に時間がかかる")


# --- 承認の根拠と制約の読み取り ---
def test_承認された生成AIが使えると述べた文だけを根拠にする():
    assert genai.allowed("社内で承認されたCopilotが使えます。")
    assert genai.allowed("会社で生成AIの利用が許可されています")
    assert not genai.allowed("生成AIは使えません。")
    assert not genai.allowed("生成AIの利用は検討中です")
    assert not genai.allowed("Excelが使えます。")  # 生成AIの話ではない


def test_クラウド不可や社外に出せない制約を読み取る():
    assert genai.blocked("入館者の名前を含むため、社外のサービスには出せません。")
    assert genai.blocked("社内規定で、外部のクラウドサービスは使えません。")
    assert genai.blocked("設備のデータは社外に出せない決まりです。")
    assert not genai.blocked("予算は相談次第です。")


def test_AIが承認済みと書いても_入力に根拠がなければ未承認に戻し_確認を着手条件に加える():
    result = make_proposal_result([genai_proposal(approved=True)])
    genai.normalize(result, genai.input_text(INFO, state("予算はありません。")))
    g = result.proposals[0].genai
    assert not g.approved and g.conditions == "利用承認／入力できるデータの範囲"  # 「承認」を含む条件が既にあれば重ねない
    assert genai.condition_text(result.proposals[0]) == "社内で承認された生成AIと入力可能なデータ範囲の確認が必要"
    no_cond = make_proposal_result([make_proposal(category="生成AI活用", genai=GenAIUse(**{**USE, "conditions": "匿名化"}))])
    genai.normalize(no_cond, "")
    assert no_cond.proposals[0].genai.conditions == genai.NOTICE + "／匿名化"
    ok = make_proposal_result([genai_proposal(approved=True)])
    genai.normalize(ok, genai.input_text(INFO, state("社内で承認された生成AIが使えます。")))
    assert ok.proposals[0].genai.approved and genai.condition_text(ok.proposals[0]).startswith("社内で承認された生成AIが使える")


# --- 出力チェック（違反ならAIに作り直させる） ---
def test_クラウド不可で承認の根拠がない案件では_生成AI案を最初の案にしない():
    text = genai.input_text(INFO, state("入館者の名前を含むため、社外のサービスには出せません。"))
    first = make_proposal_result([genai_proposal(), make_proposal()])
    assert any("最初に取り組む案" in x for x in genai.problems(first, text))
    second = make_proposal_result([make_proposal(), genai_proposal()])  # 2番目の候補として出すのはよい
    assert genai.problems(second, text) == []
    allowed = genai.input_text(INFO, state("社外のサービスには出せませんが、社内で承認された生成AIは使えます。"))
    assert genai.problems(first, allowed) == []


def test_生成AI活用なのに使い方がない_項目が空なら違反():
    missing = make_proposal_result([make_proposal(category="生成AI活用")])
    assert any("使い方" in x for x in genai.problems(missing, ""))
    empty = make_proposal_result([make_proposal(category="生成AI活用", genai=GenAIUse(**{**USE, "human_check": " "}))])
    assert any("人が確認すること" in x for x in genai.problems(empty, ""))


# --- 表示：比較表・案の要点・カード・Excel ---
def project_with_genai():
    project = make_project(proposals=2)
    project.diagnosis.proposals.proposals = [
        make_proposal(title="様式をそろえる"),
        make_proposal(title="集計を自動で取り込む", category="既存ツールの活用", genai=GenAIUse(**USE)),  # 併用
    ]
    project.diagnosis.proposals.proposals.append(genai_proposal(title="問い合わせの分類を生成AIで補助する"))
    return project


def test_比較表で生成AI活用と併用が見分けられ_対象工程と効果と利用条件を同じ列で比べられる():
    rows = compare_rows(project_with_genai().diagnosis)
    assert [r.genai_label for r in rows] == ["", "生成AIを併用", "生成AI活用"]
    html = compare_table(rows)
    assert html.count("◆ 生成AI活用") == 1 and html.count("◆ 生成AIを併用") == 1
    assert "生成AIの対象工程：週次報告の前に" in html and "効果の見込み：分類から集計まで" in html
    assert "社内で承認された生成AIと入力可能なデータ範囲の確認が必要" in html


def test_案の要点とカードに_6つの項目と利用条件を出す():
    p = genai_proposal()
    for html in (proposal_brief(1, p, []), proposal_card(2, p)):
        for text in ["対象工程", "AIへの入力と出力", "匿名化した問い合わせメモ → 分類候補と代表例", "人が確認すること",
                     "効果の見込み", "確認・修正する時間も含めて", "着手条件と主なリスク", "最初に試す範囲",
                     "社内で承認された生成AIと入力可能なデータ範囲の確認が必要"]:
            assert text in html, text
    assert "生成AIを併用できる作業" in proposal_card(1, make_proposal(genai=GenAIUse(**USE)))
    assert "生成AI" not in proposal_card(1, make_proposal())  # 使わない案には出さない


def test_Excelの改善案シートに_画面と同じ項目と利用条件を出す():
    project = project_with_genai()
    wb = load_workbook(BytesIO(build_report(project.business_info, project.hearing, project.diagnosis,
                                            load_framework("general"), project.created_at, project=project)))
    ws = wb["改善案"]
    header = [c.value for c in ws[1]]
    col = header.index("生成AIの使い方（AI-BIC 3.4）")
    values = [r[col] for r in ws.iter_rows(min_row=2, values_only=True)]
    assert values[0] in ("", None)
    assert "生成AIを併用：社内で承認された生成AIと入力可能なデータ範囲の確認が必要" in values[1]
    assert "生成AI活用" in values[2] and "最初に試す範囲：匿名化した過去20件" in values[2]


# --- 保存と旧JSON ---
def test_生成AIの案は保存して読み込んでも同じで_旧JSONの案も読み込める():
    project = project_with_genai()
    loaded = load_project(to_json_bytes(project))
    assert loaded.diagnosis.proposals.proposals == project.diagnosis.proposals.proposals
    raw = json.loads(to_json_bytes(make_project(proposals=2)))
    for p in raw["diagnosis"]["proposals"]["proposals"]:  # 3.3 までの形（genai がない）
        p.pop("genai", None)
    old = load_project(json.dumps(raw, ensure_ascii=False).encode("utf-8"))
    assert all(p.genai is None and genai.label(p) == "" for p in old.diagnosis.proposals.proposals)
