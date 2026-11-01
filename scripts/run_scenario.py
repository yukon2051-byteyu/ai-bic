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
from ai_bic.models import BusinessInfo  # noqa: E402

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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("scenario_id", help="シナリオのID（例：sales_report）")
    parser.add_argument("--no-attachments", action="store_true", help="添付ファイルを使わない")
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
    suffix = "_noattach" if args.no_attachments else ""
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
        mark = "（格下げ）" if issue.issue_id in result.downgraded_issue_ids else ""
        print(f"{issue.issue_id} [{issue.severity}・{issue.evidence_type}{mark}] {issue.title}")
        for ev in issue.evidences:
            print(f"    根拠（{ev.source} turn={ev.turn_no} file={ev.file_no}）：{ev.quote}")
    if result and result.proposals:
        s = result.proposals.summary
        print(f"\n■ 診断サマリー（最重要課題：{s.top_issue_id}）\n  要点：{s.headline}\n  最初の一手：{s.first_action}")
        for n, p in enumerate(result.proposals.proposals, start=1):
            print(f"\n案{n} [{p.category}・難易度{p.difficulty}] {p.title} → {', '.join(p.target_issue_ids)}")
            print(f"  BEFORE：{p.before}\n  AFTER ：{p.after}")
            for step in p.first_steps:
                print(f"  □ {step}")

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
