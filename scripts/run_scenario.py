"""架空シナリオを本物のAI（Claude API）で通して実行する（動作確認・AI品質評価用）。

⚠️ 実行すると Claude API の料金が発生する。

使い方：
    .\\.venv\\Scripts\\python.exe scripts\\run_scenario.py sales_report
    .\\.venv\\Scripts\\python.exe scripts\\run_scenario.py sales_report --no-attachments

- 利用者の回答は、AIが質問した診断項目に対応する、シナリオの回答を使う（模擬の回答者）。
  本物のAIはデモと違う順番や内容で質問しうるため、回の順番ではなく診断項目で回答を選ぶ
- 利用状況は本番用（usage.db）とは別のファイルに記録し、1日の回数制限にも数えない
- 結果（質問・回答・分析・提案・トークン数・費用）を eval_results/ にJSONで保存する
"""

import argparse
import json
import sqlite3
import sys
import time
import uuid
from contextlib import closing
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai_bic import config  # noqa: E402
from ai_bic.ai_client import ClaudeClient  # noqa: E402
from ai_bic.demo_client import demo_attachments, load_scenarios  # noqa: E402
from ai_bic.errors import AIError  # noqa: E402
from ai_bic.hearing import add_answer, request_next_question, run_diagnosis, start_hearing  # noqa: E402
from ai_bic.overview import valid_relations  # noqa: E402
from ai_bic.flow import time_per_case  # noqa: E402
from ai_bic.flow_ai import generate_flows  # noqa: E402
from ai_bic.models import BusinessInfo, DiagnosisResult, HearingState  # noqa: E402
from ai_bic.project import new_project  # noqa: E402

OUTPUT_DIR = config.BASE_DIR / "eval_results"

# 料金（1MTokあたりのドル。2026年10月時点の公表価格。変わった場合はここを直す）
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}
YEN_PER_DOLLAR = 150  # 想定の為替

UNKNOWN_ANSWER = "すみません、その点は分かりません。"


class SimulatedRespondent:
    """シナリオの回答を、診断項目ごとに順番に返す模擬の回答者。"""

    def __init__(self, scenario: dict):
        self.answers: dict[str, list[str]] = {}
        for turn in scenario["turns"]:
            self.answers.setdefault(turn["next_item_id"], []).append(turn["demo_answer"])

    def answer(self, item_id: str) -> str:
        remaining = self.answers.get(item_id, [])
        return remaining.pop(0) if remaining else UNKNOWN_ANSWER


def usage_summary(db_path: str, session_id: str) -> list[dict]:
    with closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT call_type, model, input_tokens, output_tokens, status, error_type "
            "FROM usage_log WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
    result = []
    for call_type, model, tin, tout, status, error_type in rows:
        price_in, price_out = PRICES.get(model, (0, 0))
        dollars = tin / 1_000_000 * price_in + tout / 1_000_000 * price_out
        result.append(
            {
                "call_type": call_type, "model": model, "input_tokens": tin,
                "output_tokens": tout, "status": status, "error_type": error_type,
                "dollars": round(dollars, 5),
            }
        )
    return result


def print_flows(project) -> None:
    """業務フローの中身を、確認しやすい形で表示する。"""
    def show(name, flow):
        t = time_per_case(flow)
        print(f"\n  [{name}] 工程{len(flow.steps)}個・1件あたり{t.minutes_per_case:.1f}分（時間不明 {len(t.unknown_step_ids)}個）")
        for s in flow.steps:
            minutes = f"{s.minutes_per_case:g}分（{s.minutes_source}）" if s.minutes_per_case is not None else "時間不明"
            flags = "".join(f"【{x}】" for x, on in [("要確認", s.needs_confirmation), ("問題", bool(s.problem))] if on)
            change = "" if s.change == "変更なし" else f"［{s.change}：{s.change_note}］"
            print(f"    {s.id} {flags}{s.name}／{s.actor or '担当不明'}／{minutes}・{s.occurrence_rate:g}%{change}")
        print("    接続：" + "、".join(f"{e.source}→{e.target}" + (f"({e.label})" if e.label else "") for e in flow.edges))

    cases = f"月{project.cases_per_month:g}件（{project.cases_source}）" if project.cases_per_month is not None else "月の件数は不明"
    print(f"\n  1件＝{project.case_unit}／{cases}")
    show("現状", project.as_is)
    for pid, flow in project.to_be.items():
        show(f"改善後 {pid}", flow)
        for kept in project.kept_controls.get(pid, []):
            print(f"    残した統制：{kept}")
        for task in project.setup_tasks.get(pid, []):
            print(f"    初回の準備作業：{task}")
    print("\n  確認したいこと：" + " ／ ".join(project.flow_questions))
    for note in project.flow_notes:
        print(f"  照合：{note}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("scenario_id", help="シナリオのID（例：sales_report）")
    parser.add_argument("--no-attachments", action="store_true", help="添付ファイルを使わない")
    parser.add_argument("--flows", action="store_true", help="診断のあとに業務フロー（AI-BIC 2.0 機能B）も作る")
    parser.add_argument("--reuse", metavar="JSON",
                        help="保存した結果のヒアリングと診断を再利用し、業務フローだけを作る（費用を抑える）")
    args = parser.parse_args()

    scenarios = {s["id"]: s for s in load_scenarios()}
    if args.scenario_id not in scenarios:
        print(f"シナリオが見つかりません。選べるID：{', '.join(scenarios)}")
        return 1
    scenario = scenarios[args.scenario_id]

    api_key = config.get_secret(config.SECRET_API_KEY)
    if not api_key:
        print("APIキーが設定されていません（.streamlit/secrets.toml）")
        return 1

    OUTPUT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(config.JST).strftime("%Y%m%d_%H%M%S")
    suffix = ("_noattach" if args.no_attachments else "") + ("_flows" if args.flows or args.reuse else "")
    db_path = str(OUTPUT_DIR / "eval_usage.db")
    session_id = uuid.uuid4().hex

    framework = config.load_framework("general")
    info = BusinessInfo(**scenario["business_info"])
    client = ClaudeClient(api_key=api_key, session_id=session_id, db_path=db_path)
    respondent = SimulatedRespondent(scenario)
    attachments = [] if args.no_attachments else demo_attachments(scenario)
    state = start_hearing(framework)

    print(f"■ シナリオ：{scenario['title']}（添付：{len(attachments)}件）\n")
    started = time.perf_counter()
    if args.reuse:
        # 保存した結果のヒアリングと診断を使い、AIは業務フローだけ呼ぶ（指示文を直したあとの確認の費用を抑える）
        saved = json.loads(Path(args.reuse).read_text(encoding="utf-8"))
        state = HearingState.model_validate(saved["hearing"])
        result = DiagnosisResult.model_validate(saved["diagnosis"])
        args.flows = True
        print(f"→ {args.reuse} のヒアリングと診断を再利用します（AIは業務フローだけ呼びます）\n")
    else:
        try:
            while True:
                t0 = time.perf_counter()
                turn = request_next_question(client, info, state, framework)
                if turn is None:
                    break
                answer = respondent.answer(turn.item_id)
                add_answer(state, answer)
                print(f"Q{turn.turn_no} [{turn.item_id}]（{time.perf_counter() - t0:.1f}秒）{turn.question}")
                print(f"A{turn.turn_no} {answer}\n")
            print(f"→ {state.finished_reason}\n")

            t0 = time.perf_counter()
            result = run_diagnosis(client, info, state, framework, attachments)
            print(f"■ 診断（分析＋提案：{time.perf_counter() - t0:.1f}秒）\n")
        except AIError as e:
            print(f"AIエラー：{e.kind}（{e.detail}）")
            result = None

    for issue in result.analysis.issues if result else []:
        mark = "（格下げ）" if issue.issue_id in result.downgraded else ""
        print(f"{issue.issue_id} [{issue.severity}・{issue.evidence_type}{mark}] {issue.title}")
        if issue.issue_id in result.downgraded:
            print(f"    {result.downgraded[issue.issue_id]}")
        if issue.calculation:
            print(f"    計算式：{issue.calculation}")
        for ev in issue.evidences:
            print(f"    根拠（{ev.source} turn={ev.turn_no} file={ev.file_no}）：{ev.quote}")
            print(f"      → 言えること：{ev.supports}")
    if result and result.proposals:
        s = result.proposals.summary
        print(f"\n■ 診断サマリー（最重要課題：{s.top_issue_id}）\n  要点：{s.headline}\n  最初の一手：{s.first_action}")
        for n, p in enumerate(result.proposals.proposals, start=1):
            print(f"\n案{n} [{p.category}・難易度{p.difficulty}] {p.title} → {', '.join(p.target_issue_ids)}")
            print(f"  短い名前：{p.short_title or '（なし）'}／主に減らすもの：{p.reduces or '（なし）'}")
            print(f"  着手前に確認すること：{' ／ '.join(p.prerequisites) or '（なし）'}")
            print(f"  BEFORE：{p.before}\n  AFTER ：{p.after}")
            for step in p.first_steps:
                print(f"  □ {step}")
        # 案どうしの関係（AI-BIC 3.0）。プログラムが不正と判断した関係は画面に出さない
        count = len(result.proposals.proposals)
        kept = valid_relations(result.proposals.relations, count)
        print(f"\n■ 案どうしの関係（AIの出力{len(result.proposals.relations)}件、画面に出すもの{len(kept)}件）")
        for r in result.proposals.relations:
            mark = "" if r in kept else "【出さない】"
            print(f"  {mark}{r.kind}：案{r.first}→案{r.second}　{r.reason}")

    project = None
    if args.flows and result and result.proposals:
        project = new_project("ai", info, state, result)
        t0 = time.perf_counter()
        try:
            generate_flows(client, project)
            print(f"\n■ 業務フロー（{time.perf_counter() - t0:.1f}秒）")
            print_flows(project)
        except AIError as e:
            print(f"\n業務フローのAIエラー：{e.kind}（{e.detail}）")

    usage = usage_summary(db_path, session_id)
    total = sum(u["dollars"] for u in usage)
    print("\n■ 利用状況（実測）")
    for u in usage:
        print(f"  {u['call_type']:9} {u['model']:18} 入力{u['input_tokens']:>6} 出力{u['output_tokens']:>6} "
              f"{u['status']} {u['error_type'] or ''} ${u['dollars']:.4f}")
    print(f"  合計：${total:.4f}（約{total * YEN_PER_DOLLAR:.1f}円、1ドル={YEN_PER_DOLLAR}円で換算）"
          f"／所要時間：{time.perf_counter() - started:.0f}秒")

    out = OUTPUT_DIR / f"{args.scenario_id}{suffix}_{stamp}.json"
    out.write_text(
        json.dumps(
            {
                "scenario_id": args.scenario_id,
                "attachments": [a.name for a in attachments],
                "models": {"question": config.MODEL_QUESTION, "analysis": config.MODEL_ANALYSIS,
                           "proposal": config.MODEL_PROPOSAL},
                "hearing": state.model_dump(),
                "diagnosis": result.model_dump() if result else None,
                "flows": project.model_dump(
                    mode="json", include={"as_is", "to_be", "kept_controls", "setup_tasks", "case_unit",
                                          "cases_per_month", "cases_source", "flow_questions", "flow_notes"}
                ) if project and project.as_is else None,
                "usage": usage,
                "total_dollars": round(total, 5),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n結果を保存しました：{out.relative_to(config.BASE_DIR)}")
    return 0 if result else 1


if __name__ == "__main__":
    sys.exit(main())
