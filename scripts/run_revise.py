"""改善案の作り直し（AI-BIC 2.0 機能D）を本物のAI（Claude API）で確かめる（AI品質評価用）。

⚠️ 実行すると Claude API の料金が発生する（作り直しの1回だけ。レビューは保存したものを再利用する）。

使い方：
    .\\.venv\\Scripts\\python.exe scripts\\run_revise.py eval_results\\sales_report_review_P1_XXXX.json 0 3

- run_review.py が保存したレビューを読み込み、指定した番号（0から）の指摘を「採用」して作り直す
- 採用した指摘だけが反映されたか、元の課題から外れていないか（プログラムのチェック）、変更前後の違いを表示する
"""

import argparse
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai_bic import config  # noqa: E402
from ai_bic import project as pj  # noqa: E402
from ai_bic import review as rv  # noqa: E402
from ai_bic.ai_client import ClaudeClient  # noqa: E402
from ai_bic.errors import AIError  # noqa: E402
from ai_bic.project import ReviewRecord  # noqa: E402
from scripts.run_review import build_project  # noqa: E402
from scripts.run_scenario import OUTPUT_DIR, YEN_PER_DOLLAR, usage_summary  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("review_file", help="run_review.py が保存したレビューの結果")
    parser.add_argument("adopt", type=int, nargs="+", help="採用する指摘の番号（0から）")
    args = parser.parse_args()
    api_key = config.get_secret(config.SECRET_API_KEY)
    if not api_key:
        print("APIキーが設定されていません（.streamlit/secrets.toml）")
        return 1

    saved_review = json.loads(Path(args.review_file).read_text(encoding="utf-8"))
    pid = saved_review["pid"]
    source = json.loads(Path(saved_review["source"]).read_text(encoding="utf-8"))
    project = build_project(source, pid)
    record = ReviewRecord.model_validate(saved_review["review"])
    record.decisions = {no: "採用" for no in args.adopt}
    project.reviews[pid] = record
    pj.mark_updated(project, "review", pid)
    before = project.diagnosis.proposals.proposals[int(pid[1:]) - 1]

    print("■ 採用した指摘")
    for f in rv.adopted_findings(project, pid):
        print(f"  [{f.severity}・{f.viewpoint}] {f.title}\n    提案：{f.suggestion}")

    db_path = str(OUTPUT_DIR / "eval_usage.db")
    session_id = uuid.uuid4().hex
    client = ClaudeClient(api_key=api_key, session_id=session_id, db_path=db_path)
    try:
        rv.revise(client, project, pid)
    except AIError as e:
        print(f"AIエラー：{e.kind}（{e.detail}）")
        return 1
    after = project.diagnosis.proposals.proposals[int(pid[1:]) - 1]

    print("\n■ 変えたこと（AIの説明）")
    for note in project.revision_notes[pid]:
        print(f"  - {note}")
    print("\n■ 変更前後の違い（プログラムが比較）")
    for name, b, a in rv.diff(before, after):
        print(f"  ◆ {name}\n    変更前：{b.replace(chr(10), ' ')}\n    変更後：{a.replace(chr(10), ' ')}")
    unchanged = [name for field, name in rv.DIFF_FIELDS.items() if getattr(before, field) == getattr(after, field)]
    print(f"\n  変わらなかった項目：{'、'.join(unchanged)}")
    print(f"  対象の課題：{before.target_issue_ids} → {after.target_issue_ids}")
    print("  関連する結果の状態：" + "／".join(
        f"{name} {pj.status(project, kind, pid)}" for kind, name in [("to_be", "改善後フロー"), ("simulation", "試算"), ("review", "レビュー")]))

    usage = usage_summary(db_path, session_id)
    total = sum(u["dollars"] for u in usage)
    for u in usage:
        print(f"\n利用状況（実測）：{u['call_type']} 入力{u['input_tokens']} 出力{u['output_tokens']} {u['status']} ${u['dollars']:.4f}")
    print(f"合計：${total:.4f}（約{total * YEN_PER_DOLLAR:.1f}円、1ドル={YEN_PER_DOLLAR}円で換算）")
    stamp = datetime.now(config.JST).strftime("%Y%m%d_%H%M%S")
    out = OUTPUT_DIR / f"{source['scenario_id']}_revise_{pid}_{stamp}.json"
    out.write_text(json.dumps({"review_file": args.review_file, "adopted": args.adopt,
                               "before": before.model_dump(), "after": after.model_dump(),
                               "changes": project.revision_notes[pid], "usage": usage,
                               "total_dollars": round(total, 5)}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n結果を保存しました：{out.relative_to(config.BASE_DIR)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
