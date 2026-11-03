"""demo_client.py とデモデータのテスト（F05）。

デモのデータも、本物のAIの回答と同じチェックに合格することを確認する。
"""

import pytest

from ai_bic.config import load_framework
from ai_bic.demo_client import DemoClient, demo_answer, demo_attachments, load_scenarios
from ai_bic.hearing import (
    FINISH_MAX_TURNS,
    FINISH_SUFFICIENT,
    add_answer,
    request_next_question,
    run_diagnosis,
    start_hearing,
)
from ai_bic.models import BusinessInfo
from ai_bic.output_check import (
    _quote_found,
    check_analysis,
    check_proposals,
    check_question_result,
)
from ai_bic.validation import validate_business_info

FW = load_framework("general")
SCENARIOS = load_scenarios()


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_デモシナリオは最後まで進み_全データがチェックに合格する(scenario):
    info, errors = validate_business_info(scenario["business_info"])
    assert errors == {}  # 業務情報が入力チェックに合格する

    client = DemoClient(scenario)
    state = start_hearing(FW)
    while True:
        # 質問データが、本物と同じ内容チェックに合格する
        assert check_question_result(client.ask_next_question(info, state, FW), state, FW) == []
        turn = request_next_question(client, info, state, FW)
        if turn is None:
            break
        add_answer(state, demo_answer(scenario, turn.turn_no))

    assert len(state.turns) == len(scenario["turns"])
    result = run_diagnosis(client, info, state, FW, demo_attachments(scenario))
    assert check_analysis(client.analyze(info, state, FW), FW) == []
    assert check_proposals(result.proposals, result.analysis) == []


# わざと根拠のない「本人の申告」にしてある課題（引用照合の仕組みを見せるため）
INTENTIONALLY_UNSUPPORTED = {("sales_report", "I5")}


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_デモの引用は1件ずつすべて元の文章に実在する(scenario):
    # 課題単位ではなく引用単位で確認する（1件でも誤った引用があれば見つける）
    info = scenario["business_info"]
    business = " ".join(v or "" for v in info.values())
    answers = {n: t["demo_answer"] for n, t in enumerate(scenario["turns"], start=1)}
    files = {n: a.text for n, a in enumerate(demo_attachments(scenario), start=1)}

    for issue in scenario["analysis"]["issues"]:
        if (scenario["id"], issue["issue_id"]) in INTENTIONALLY_UNSUPPORTED:
            continue
        for ev in issue["evidences"]:
            source = {"業務情報": business, "回答": answers.get(ev["turn_no"], ""),
                      "添付ファイル": files.get(ev["file_no"], "")}[ev["source"]]
            assert _quote_found(ev["quote"], [source]), f'{issue["issue_id"]}: {ev["quote"]}'


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_どのシナリオも見本ファイルを持ち_根拠に使っている(scenario):
    assert demo_attachments(scenario)
    sources = {ev["source"] for i in scenario["analysis"]["issues"] for ev in i["evidences"]}
    assert "添付ファイル" in sources


def test_シナリオが2本以上あり_IDとタイトルが重複しない():
    assert len(SCENARIOS) >= 2
    assert len({s["id"] for s in SCENARIOS}) == len(SCENARIOS)
    assert len({s["title"] for s in SCENARIOS}) == len(SCENARIOS)


def run_demo(scenario_id):
    """デモを最後まで進め、(ヒアリングの記録, 診断結果) を返す。"""
    scenario = next(s for s in SCENARIOS if s["id"] == scenario_id)
    info = BusinessInfo(**scenario["business_info"])
    client = DemoClient(scenario)
    state = start_hearing(FW)
    while (turn := request_next_question(client, info, state, FW)) is not None:
        add_answer(state, demo_answer(scenario, turn.turn_no))
    return state, run_diagnosis(client, info, state, FW, demo_attachments(scenario))


def test_売上集計のデモは_4問で全項目がそろって終了する():
    state, result = run_demo("sales_report")
    assert len(state.turns) == 4
    assert state.finished_reason == FINISH_SUFFICIENT
    assert result.missing_item_ids == []


def test_修理受付のデモは_5問の上限で終了し_不足項目が残る():
    state, result = run_demo("repair_reception")
    assert len(state.turns) == 5
    assert state.finished_reason == FINISH_MAX_TURNS
    assert result.missing_item_ids == ["H6"]  # 「予算は相談次第」で制約条件が確認できていない


def test_修理受付のデモには_あいまいな回答への確認質問がある():
    scenario = next(s for s in SCENARIOS if s["id"] == "repair_reception")
    # 2問目の「けっこう多いです」を受けて、3問目で同じ項目（H3）を確認する
    assert scenario["turns"][1]["next_item_id"] == scenario["turns"][2]["next_item_id"] == "H3"
    assert scenario["turns"][2]["question_type"] == "確認"


def test_交通費精算のデモは_入力が詳しいので2問で終了する():
    state, result = run_demo("travel_expense")
    assert len(state.turns) == 2
    assert state.finished_reason == FINISH_SUFFICIENT
    assert result.missing_item_ids == []
    # 業務情報で分かっている項目（目的・作業内容・時間・ツール）は質問していない
    assert {t.item_id for t in state.turns} == {"H5", "H6"}


def test_交通費精算のデモは_AIが最初から要確認とした課題を含む():
    _, result = run_demo("travel_expense")
    by_id = {i.issue_id: i.evidence_type for i in result.analysis.issues}
    assert by_id["I3"] == "要確認"
    assert result.downgraded == {}  # 格下げではなく、AI自身の判断


def test_交通費精算のデモは_制約を守り自動化を含む():
    _, result = run_demo("travel_expense")
    categories = [p.category for p in result.proposals.proposals]
    assert "自動化" in categories
    assert "新規システム導入" not in categories
    # 「クラウドサービスは使えない」という制約に反する案を出していない
    for p in result.proposals.proposals:
        assert "クラウド" not in p.description


def test_3本のシナリオで_5種類の改善の種類のうち4種類以上を見せられる():
    categories = {
        p["category"] for s in SCENARIOS for p in s["proposals"]["proposals"]
    }
    assert len(categories) >= 4


def test_修理受付のデモは_格下げがなく_新規システム導入には理由がある():
    _, result = run_demo("repair_reception")
    assert result.downgraded == {}
    new_system = [p for p in result.proposals.proposals if p.category == "新規システム導入"]
    assert len(new_system) == 1
    assert new_system[0].why_not_simpler
    # 新規システム導入は、より簡単な案の後（最後）に置かれている
    assert result.proposals.proposals[-1].category == "新規システム導入"


def test_売上集計のデモでは_根拠のない本人の申告の課題だけが格下げされる():
    scenario = next(s for s in SCENARIOS if s["id"] == "sales_report")
    info = BusinessInfo(**scenario["business_info"])
    client = DemoClient(scenario)
    state = start_hearing(FW)
    while (turn := request_next_question(client, info, state, FW)) is not None:
        add_answer(state, demo_answer(scenario, turn.turn_no))

    result = run_diagnosis(client, info, state, FW)

    # I5 は「提出が遅れる営業所がある」という、利用者が言っていない引用を根拠にしている
    assert list(result.downgraded) == ["I5"]
    by_id = {i.issue_id: i.evidence_type for i in result.analysis.issues}
    assert by_id == {"I1": "本人の申告", "I2": "本人の申告", "I3": "本人の申告", "I4": "推測", "I5": "要確認"}


def test_デモの回答は範囲外の回なら空():
    assert demo_answer(SCENARIOS[0], 99) == ""
