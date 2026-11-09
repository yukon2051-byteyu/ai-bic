"""結果画面の進み具合（AI-BIC 2.0 指示書 6章・7.3）。

STEP 1〜6 の状態（未着手・作業中・完了・要確認・再作成／再計算／再レビューが必要）と、次にすることを、
案件データの版の記録（project.py）からプログラムで判定する。AIは使わない。
"""

from dataclasses import dataclass

from ai_bic import project as pj
from ai_bic import simulation as sim
from ai_bic.project import Project
from ai_bic.readiness import evaluate

OUTDATED = ("再作成が必要", "再計算が必要", "再レビューが必要")


@dataclass
class StepState:
    no: int
    name: str
    status: str  # 未着手・作業中・完了・要確認・再作成が必要・再計算が必要・再レビューが必要
    detail: str = ""  # 改善案ごとの状態など
    action: str = ""  # この STEP で次にすること


def _merge(states: dict[str, str]) -> tuple[str, str]:
    """改善案ごとの状態を1つにまとめる。古い結果があれば、それを最優先で示す（古い結果を有効に見せない）。"""
    values = list(states.values())
    detail = "／".join(f"案{pid[1:]} {s}" for pid, s in states.items())
    for outdated in OUTDATED:
        if outdated in values:
            return outdated, detail
    if all(v == "完了" for v in values):
        return "完了", detail
    if all(v == "未着手" for v in values):
        return "未着手", detail
    return "作業中", detail


def steps(project: Project) -> list[StepState]:
    pids = pj.proposal_ids(project)
    lacking = [f.name for f in evaluate(project.hearing) if f.level == "不足"]
    result = [StepState(1, "ヒアリング", "要確認" if lacking else "完了",
                        f"情報が不足：{'・'.join(lacking)}（仮定条件で分析）" if lacking else "",
                        "結果画面の「仮定条件」を確かめ、分かった情報で試算や業務フローを直す")]

    if project.as_is is None:
        result.append(StepState(2, "現状の業務フロー", "未着手", "", "「業務フロー」で現状の業務フローを作る"))
    else:
        confirm = [s.id for s in project.as_is.steps if s.needs_confirmation]
        status = "要確認" if confirm else ("完了" if pj.status(project, "as_is") == "完了" else "作業中")
        result.append(StepState(2, "現状の業務フロー", status,
                                f"要確認の工程：{'、'.join(confirm)}" if confirm else "",
                                "「要確認」の工程が実際の業務と合っているか確かめ、表で直す"))
    if not pids:
        return result

    to_be = {pid: pj.status(project, "to_be", pid) for pid in pids}
    status, detail = _merge(to_be)
    result.append(StepState(3, "改善案と改善後フロー", status, detail,
                            "改善後フローを確認し、「確認した」を押すか表で直す" if status in OUTDATED
                            else "「業務フロー」で改善後フローを確認する"))

    sims = {}
    for pid in pids:
        s = pj.status(project, "simulation", pid)
        if s == "完了" and sim.simulate(project.simulations[pid]).missing:
            s = "作業中"  # 未入力の項目がある
        sims[pid] = s
    status, detail = _merge(sims)
    result.append(StepState(4, "改善効果の試算", status, detail,
                            "試算の値を見直し、「試算の値を確認した」を押す" if status in OUTDATED
                            else "「改善効果の試算」で未入力の項目を入れる"))

    reviews = {pid: pj.status(project, "review", pid) for pid in pids}
    status, detail = _merge(reviews)
    result.append(StepState(5, "改善案レビュー", status, detail,
                            "改善案をレビューし直す" if status in OUTDATED else "「改善案チャレンジレビュー」で改善案をレビューする"))

    chosen = project.selected_proposal
    result.append(StepState(6, "改善案の確定", "完了" if chosen else "未着手",
                            f"案{chosen[1:]}を確定" if chosen else "", "レビューを踏まえて、改善案を確定する"))
    return result


def next_action(states: list[StepState]) -> str | None:
    """次にすること（完了していない最初の STEP）。すべて完了なら None。"""
    for s in states:
        if s.status != "完了":
            return f"STEP {s.no}（{s.name}）：{s.action}"
    return None
