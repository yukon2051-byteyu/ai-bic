"""ヒアリングの追加確認（AI-BIC 2.0）を本物のAI（Claude API）で確かめる（AI品質評価用）。

⚠️ 実行すると Claude API の料金が発生する（質問用のモデルだけを使う。課題分析と改善案は作らない）。

使い方：
    .\\.venv\\Scripts\\python.exe scripts\\run_extra.py repair_reception

- 基本のヒアリングのあと、3機能に必要な情報が足りなければ追加確認を始め、最大3問まで答える
- 回答は、シナリオの回答（追加確認用の extra_turns を含む）を診断項目ごとに使う（模擬の回答者）
- 結果（質問・理由・充足度・終了理由・費用）を eval_results/ に JSON で保存する
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
from ai_bic.hearing import (  # noqa: E402
    add_answer,
    finish_by_user,
    judge_last_answers,
    request_extra_question,
    request_next_question,
    start_extra,
    start_hearing,
)
from ai_bic.models import BusinessInfo  # noqa: E402
from ai_bic.readiness import evaluate, reason_for, should_offer_extra  # noqa: E402
from scripts.run_scenario import OUTPUT_DIR, YEN_PER_DOLLAR, SimulatedRespondent, usage_summary  # noqa: E402


def show_readiness(state) -> list[dict]:
    rows = [{"機能": f.name, "判定": f.level, "不足（必須）": f.missing_required} for f in evaluate(state)]
    for row in rows:
        print(f"  {row['機能']}：{row['判定']}　不足（必須）：{'・'.join(row['不足（必須）']) or 'なし'}")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("scenario_id")
    parser.add_argument("--stop-after", type=int, default=None,
                        help="この問数に答えたら「ここまでの情報で診断する」を押したことにする（利用者が途中で止める場合）")
    args = parser.parse_args()
    scenario = next(s for s in load_scenarios() if s["id"] == args.scenario_id)
    api_key = config.get_secret(config.SECRET_API_KEY)
    if not api_key:
        print("APIキーが設定されていません（.streamlit/secrets.toml）")
        return 1

    framework = config.load_framework("general")
    info = BusinessInfo(**scenario["business_info"])
    db_path = str(OUTPUT_DIR / "eval_usage.db")
    session_id = uuid.uuid4().hex
    client = ClaudeClient(api_key=api_key, session_id=session_id, db_path=db_path)
    # 追加確認用の回答（extra_turns）も、診断項目ごとに使えるようにする
    respondent = SimulatedRespondent({"turns": scenario["turns"] + scenario.get("extra_turns", [])})
    state = start_hearing(framework)
    record = {"scenario_id": args.scenario_id, "turns": [], "offered": False}

    print(f"■ シナリオ：{scenario['title']}\n")
    try:
        while (turn := request_next_question(client, info, state, framework)) is not None:
            answer = respondent.answer(turn.item_id)
            add_answer(state, answer)
            print(f"Q{turn.turn_no} [{turn.item_id}] {turn.question}\nA{turn.turn_no} {answer}\n")
            if args.stop_after and len(state.turns) >= args.stop_after:
                finish_by_user(state)
                break
        judge_last_answers(client, info, state, framework)
        print(f"→ {state.finished_reason}\n\n■ 基本のヒアリング後の充足度")
        record["readiness_before"] = show_readiness(state)

        if should_offer_extra(state, framework):
            record["offered"] = True
            start_extra(state, framework)
            print(f"\n■ 追加確認を提案（聞く項目：{'・'.join(state.extra_targets)}）\n")
            while (turn := request_extra_question(client, info, state, framework)) is not None:
                reason = reason_for(state, framework, turn.item_id)
                answer = respondent.answer(turn.item_id)
                add_answer(state, answer)
                print(f"追加Q [{turn.item_id}] {turn.question}\n  理由：{reason}\n"
                      f"  この回答で判断できること：{turn.decides or '（なし）'}\n  A：{answer}\n")
            # 上限で終わると最後の回答はまだ判定していない。アプリと同じく、診断の前に判定し直す
            judge_last_answers(client, info, state, framework)
            print(f"→ {state.extra_finished_reason}\n\n■ 追加確認後の充足度（最後の回答も判定したあと）")
            record["readiness_after"] = show_readiness(state)
        else:
            lacking = [f.name for f in evaluate(state) if f.level == "不足"]
            if lacking:
                print(f"\n→ {'・'.join(lacking)}に必要な情報が不足しているが、もう質問できる項目がない"
                      "（同じ項目は2回まで・「分からない」と答えた項目は聞かない）ため、追加確認は提案されなかった。"
                      "結果画面には仮定条件を表示する")
            else:
                print("\n→ 3機能に必要な情報がそろっているため、追加確認は提案されなかった")
    except AIError as e:
        print(f"AIエラー：{e.kind}（{e.detail}）")
        return 1

    usage = usage_summary(db_path, session_id)
    total = sum(u["dollars"] for u in usage)
    print(f"\n■ 利用状況（実測）：AI呼び出し{len(usage)}回、合計 ${total:.4f}（約{total * YEN_PER_DOLLAR:.1f}円、1ドル={YEN_PER_DOLLAR}円で換算）")
    record.update(hearing=state.model_dump(), usage=usage, total_dollars=round(total, 5))
    stamp = datetime.now(config.JST).strftime("%Y%m%d_%H%M%S")
    out = OUTPUT_DIR / f"{args.scenario_id}_extra_{stamp}.json"
    out.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"結果を保存しました：{out.relative_to(config.BASE_DIR)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
