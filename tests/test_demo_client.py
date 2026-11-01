"""demo_client.py とデモデータのテスト（F05）。

デモのデータも、本物のAIの回答と同じチェックに合格することを確認する。
"""

import pytest

from ai_bic.config import load_framework
from ai_bic.demo_client import DemoClient, demo_answer, load_scenarios
from ai_bic.hearing import add_answer, request_next_question, run_diagnosis, start_hearing
from ai_bic.models import BusinessInfo
from ai_bic.output_check import check_analysis, check_proposals, check_question_result
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
    result = run_diagnosis(client, info, state, FW)
    assert check_analysis(client.analyze(info, state, FW), FW) == []
    assert check_proposals(result.proposals, result.analysis) == []
    assert result.missing_item_ids == []


def test_売上集計のデモでは_根拠のない事実の課題だけが格下げされる():
    scenario = next(s for s in SCENARIOS if s["id"] == "sales_report")
    info = BusinessInfo(**scenario["business_info"])
    client = DemoClient(scenario)
    state = start_hearing(FW)
    while (turn := request_next_question(client, info, state, FW)) is not None:
        add_answer(state, demo_answer(scenario, turn.turn_no))

    result = run_diagnosis(client, info, state, FW)

    # I5 は「提出が遅れる営業所がある」という、利用者が言っていない引用を根拠にしている
    assert result.downgraded_issue_ids == ["I5"]
    by_id = {i.issue_id: i.evidence_type for i in result.analysis.issues}
    assert by_id == {"I1": "事実", "I2": "事実", "I3": "事実", "I4": "推測", "I5": "要確認"}


def test_デモの回答は範囲外の回なら空():
    assert demo_answer(SCENARIOS[0], 99) == ""
