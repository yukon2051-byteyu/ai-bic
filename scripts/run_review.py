"""改善案チャレンジレビューを本物のAI（Claude API）で1回実行する（AI品質評価用）。

⚠️ 実行すると Claude API の料金が発生する。

使い方：
    .\\.venv\\Scripts\\python.exe scripts\\run_review.py eval_results\\sales_report_flows_XXXX.json P1

- 保存した結果（run_scenario.py の --flows / --reuse の出力）のヒアリング・診断・業務フローを再利用し、
  AIはレビューだけ呼ぶ（費用を抑える）
- 効果試算は、シナリオのデモ値を「ユーザー入力」として入れる（試算の値がない状態のレビューを避けるため）
- 結果（判定・指摘・根拠の照合・プログラムの検証・費用）を eval_results/ に JSON で保存する
"""

import argparse
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai_bic import config  # noqa: E402
from ai_bic.ai_client import ClaudeClient  # noqa: E402
from ai_bic.demo_client import load_scenarios  # noqa: E402
from ai_bic.errors import AIError  # noqa: E402
from ai_bic.flow import Flow  # noqa: E402
from ai_bic.models import BusinessInfo, DiagnosisResult, HearingState  # noqa: E402
from ai_bic.project import mark_updated, new_project  # noqa: E402
from ai_bic.review import generate_review  # noqa: E402
from ai_bic.simulation import SimulationInputs, blank_scenarios  # noqa: E402
from scripts.run_scenario import OUTPUT_DIR, YEN_PER_DOLLAR, usage_summary  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("saved", help="run_scenario.py が保存した結果（業務フローを含むもの）")
    parser.add_argument("pid", help="レビューする改善案のID（例：P1）")
    args = parser.parse_args()

    saved = json.loads(Path(args.saved).read_text(encoding="utf-8"))
    scenario = next(s for s in load_scenarios() if s["id"] == saved["scenario_id"])
    api_key = config.get_secret(config.SECRET_API_KEY)
    if not api_key:
        print("APIキーが設定されていません（.streamlit/secrets.toml）")
        return 1

    info = BusinessInfo(**scenario["business_info"])
    project = new_project("ai", info, HearingState.model_validate(saved["hearing"]),
                          DiagnosisResult.model_validate(saved["diagnosis"]))
    flows = saved["flows"]
    project.as_is = Flow.model_validate(flows["as_is"])
    project.to_be = {pid: Flow.model_validate(f) for pid, f in flows["to_be"].items()}
    project.kept_controls = flows.get("kept_controls", {})
    project.setup_tasks = flows.get("setup_tasks", {})
    mark_updated(project, "as_is")
    for pid in project.to_be:
        mark_updated(project, "to_be", pid)
    no = int(args.pid[1:])
    demo = scenario["simulation"]
    values = {**demo["base"], **demo["proposals"][no - 1]}
    project.simulations[args.pid] = SimulationInputs(
        task_name=info.task_name, **values, sources={k: "ユーザー入力" for k in values})
    project.scenarios[args.pid] = blank_scenarios()
    mark_updated(project, "simulation", args.pid)

    db_path = str(OUTPUT_DIR / "eval_usage.db")
    session_id = uuid.uuid4().hex
    client = ClaudeClient(api_key=api_key, session_id=session_id, db_path=db_path)
    print(f"■ {scenario['title']}／案{no}：{project.diagnosis.proposals.proposals[no - 1].title}\n")
    try:
        record = generate_review(client, project, args.pid)
    except AIError as e:
        print(f"AIエラー：{e.kind}（{e.detail}）")
        return 1

    r = record.result
    print(f"判定：AI「{record.ai_verdict}」→ 最終「{record.verdict}」")
    for reason in record.override_reasons:
        print(f"  上書きの理由：{reason}")
    print(f"要約：{r.summary}\n")
    for i, f in enumerate(r.findings):
        mark = "（根拠：未確認）" if i in record.unverified else ""
        print(f"[{f.severity}・{f.viewpoint}] {f.title}{mark}\n  {f.detail}\n  根拠：{f.evidence}\n  提案：{f.suggestion}")
    for name, items in [("評価できる点", r.strengths), ("不足している情報", r.missing_info),
                        ("代替案", r.alternatives), ("次に確認すべき事項", r.next_checks)]:
        print(f"\n{name}：" + " ／ ".join(items))
    print("\nプログラムの検証：" + (" ／ ".join(f"[{x.severity}] {x.message}" for x in record.rule_findings) or "なし"))

    usage = usage_summary(db_path, session_id)
    total = sum(u["dollars"] for u in usage)
    for u in usage:
        print(f"\n利用状況（実測）：{u['call_type']} 入力{u['input_tokens']} 出力{u['output_tokens']} {u['status']} ${u['dollars']:.4f}")
    print(f"合計：${total:.4f}（約{total * YEN_PER_DOLLAR:.1f}円、1ドル={YEN_PER_DOLLAR}円で換算）")

    stamp = datetime.now(config.JST).strftime("%Y%m%d_%H%M%S")
    out = OUTPUT_DIR / f"{saved['scenario_id']}_review_{args.pid}_{stamp}.json"
    out.write_text(json.dumps({"source": args.saved, "pid": args.pid, "review": record.model_dump(mode="json"),
                               "usage": usage, "total_dollars": round(total, 5)}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\n結果を保存しました：{out.relative_to(config.BASE_DIR)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
