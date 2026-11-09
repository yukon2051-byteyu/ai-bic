"""project.py のテスト（案件データの保存・読み込みと、古い結果の判定：AI-BIC 2.0）。"""

import json

import pytest

from ai_bic import config
from ai_bic.flow import Flow, FlowStep
from ai_bic.models import AnalysisResult, BusinessInfo, DiagnosisResult, HearingState
from ai_bic.project import (
    SCHEMA_VERSION,
    ProjectFileError,
    file_name,
    load_project,
    mark_updated,
    new_project,
    outdated_reasons,
    proposal_ids,
    status,
    to_json_bytes,
)
from ai_bic.simulation import SimulationInputs
from tests.fakes import make_proposal, make_proposal_result


def make_project(mode="ai", proposals=2, **kwargs):
    info = BusinessInfo(company_name=None, department="製造部", task_name="月次生産実績集計", overview="o", pain_points="p")
    diagnosis = DiagnosisResult(
        attachment_names=[],
        analysis=AnalysisResult(business_summary="", issues=[], no_issue_viewpoints=[]),
        proposals=make_proposal_result([make_proposal() for _ in range(proposals)]),
        downgraded={},
        missing_item_ids=[],
    )
    return new_project(mode, info, HearingState(), diagnosis, **kwargs)


def filled_project():
    p = make_project()
    p.as_is = Flow(steps=[FlowStep(id="S1", name="転記", minutes_per_case=30)], next_no=2)
    mark_updated(p, "as_is")
    p.to_be["P1"] = p.as_is
    mark_updated(p, "to_be", "P1")
    p.simulations["P1"] = SimulationInputs(minutes_per_case=30, sources={"minutes_per_case": "ヒアリング"})
    mark_updated(p, "simulation", "P1")
    mark_updated(p, "review", "P1")
    return p


# --- 古い結果の判定（指示書 6章） ---
def test_作った直後は完了_作っていなければ未着手():
    p = filled_project()
    assert status(p, "simulation", "P1") == "完了"
    assert status(p, "review", "P1") == "完了"
    assert status(p, "simulation", "P2") == "未着手"


def test_改善案を変更すると_後の結果がすべて古くなる():
    p = filled_project()
    mark_updated(p, "proposal", "P1")  # 改善案を再生成した
    assert status(p, "to_be", "P1") == "再作成が必要"
    assert status(p, "simulation", "P1") == "再計算が必要"
    assert status(p, "review", "P1") == "再レビューが必要"
    assert "改善案が変更されました" in outdated_reasons(p, "review", "P1")


def test_ほかの改善案の変更には影響されない():
    p = filled_project()
    mark_updated(p, "proposal", "P2")
    assert status(p, "review", "P1") == "完了"


def test_試算だけを変えると_レビューだけが古くなる():
    p = filled_project()
    mark_updated(p, "simulation", "P1")
    assert status(p, "to_be", "P1") == "完了"
    assert status(p, "review", "P1") == "再レビューが必要"


def test_元が古いままの結果を元にした結果も古い():
    p = filled_project()
    mark_updated(p, "as_is")  # 現状フローを直した → 改善後フローが古い
    assert status(p, "to_be", "P1") == "再作成が必要"
    # レビューは as_is に直接は依存しないが、元にした改善後フローが古いので古い
    assert status(p, "review", "P1") == "再レビューが必要"


def test_作り直せば完了に戻る():
    p = filled_project()
    mark_updated(p, "proposal", "P1")
    for kind in ("to_be", "simulation", "review"):
        mark_updated(p, kind, "P1")
    assert status(p, "review", "P1") == "完了"


# --- 保存と読み込み ---
def test_保存したファイルを読み込むと同じ案件に戻る():
    p = filled_project()
    loaded = load_project(to_json_bytes(p))
    assert loaded == p
    assert loaded.simulations["P1"].source_of("minutes_per_case") == "ヒアリング"
    assert status(loaded, "review", "P1") == "完了"  # 版の記録も戻る


def test_ファイル名でデモと実案件を区別する():
    assert file_name(make_project()).startswith("AI-BIC_案件_")
    assert file_name(make_project("demo", demo_scenario_id="manufacturing")).startswith("AI-BIC_デモ案件_")


def test_デモの案件はデモのまま読み込まれる():
    p = load_project(to_json_bytes(make_project("demo", demo_scenario_id="manufacturing")))
    assert p.mode == "demo"


def _raw(p) -> dict:
    return json.loads(to_json_bytes(p))


def _bytes(raw: dict) -> bytes:
    return json.dumps(raw, ensure_ascii=False).encode("utf-8")


@pytest.mark.parametrize(
    "data, message",
    [
        (b"{not json", "JSON形式"),
        ("あ".encode("cp932"), "JSON形式"),
        (b"[1, 2]", "AI-BIC で保存した"),
        (b'{"app": "other"}', "AI-BIC で保存した"),
        (b'{"app": "AI-BIC", "schema_version": "1"}', "版番号"),
        (b'{"app": "AI-BIC", "schema_version": true}', "版番号"),
    ],
)
def test_読み込めないファイルは理由を伝える(data, message):
    with pytest.raises(ProjectFileError, match=message):
        load_project(data)


def test_新しい版のファイルは読み込まない():
    raw = _raw(make_project())
    raw["schema_version"] = SCHEMA_VERSION + 1
    with pytest.raises(ProjectFileError, match="新しい版"):
        load_project(_bytes(raw))


def test_大きすぎるファイルは読み込まない():
    with pytest.raises(ProjectFileError, match="大きすぎ"):
        load_project(b" " * (config.MAX_PROJECT_FILE_BYTES + 1))


def test_中身が壊れたファイルは内容を表示せずに断る():
    raw = _raw(filled_project())
    raw["as_is"]["edges"] = [{"source": "S1", "target": "S9", "label": "秘密の内容"}]
    with pytest.raises(ProjectFileError) as e:
        load_project(_bytes(raw))
    assert "as_is" in str(e.value) and "秘密" not in str(e.value)


def test_存在しない改善案のデータははじく():
    raw = _raw(filled_project())
    raw["simulations"]["P9"] = raw["simulations"]["P1"]
    with pytest.raises(ProjectFileError):
        load_project(_bytes(raw))


def test_BOMつきのファイルも読める():
    p = make_project()
    assert load_project(b"\xef\xbb\xbf" + to_json_bytes(p)) == p


def test_改善案のIDは並び順():
    assert proposal_ids(make_project(proposals=3)) == ["P1", "P2", "P3"]
