"""AIに渡す文章の組み立て（詳細設計書 2章・3.3）。

- システムプロンプト（AIの役割とルール）は ai_bic/prompts/*.md から読み込む
- ユーザーメッセージ（業務情報・ヒアリング記録など）はここで組み立てる

利用者の入力は <business_info> などのタグで囲み、「データ」としてAIに渡す。
入力の中に「</business_info>」のような文字列を書いてタグの外に抜け出し、
AIに指示を出す攻撃（プロンプトインジェクション）を防ぐため、
利用者の入力に含まれる < と > は全角の ＜ ＞ に置き換える。
（引用の照合では normalize() が全角を半角に戻すので、照合には影響しない）
"""

from pathlib import Path

from ai_bic.config import MAX_HEARING_TURNS
from ai_bic.item_rules import askable_item_ids, blocked_reason, question_count
from ai_bic.models import AnalysisResult, Attachment, BusinessInfo, Framework, HearingState

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def load_system_prompt(name: str) -> str:
    """ai_bic/prompts/<name>.md を読み込む（question_system など）。"""
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def _escape(text: str | None) -> str:
    """利用者の入力に含まれる < > を全角にして、タグとして解釈されないようにする。"""
    return (text or "").replace("<", "＜").replace(">", "＞")


def _business_info_block(info: BusinessInfo) -> str:
    return "\n".join(
        [
            "<business_info>",
            f"企業名: {_escape(info.company_name) or '（未入力）'}",
            f"部署名: {_escape(info.department)}",
            f"業務名: {_escape(info.task_name)}",
            f"業務概要: {_escape(info.overview)}",
            f"現在困っていること: {_escape(info.pain_points)}",
            "</business_info>",
        ]
    )


def _hearing_log_block(state: HearingState) -> str:
    if not state.turns:
        return "<hearing_log>\n（まだ質問していません）\n</hearing_log>"

    lines = ["<hearing_log>"]
    for turn in state.turns:
        answer = _escape(turn.answer) if turn.answer is not None else "（未回答）"
        lines += [
            f'<turn no="{turn.turn_no}" item="{turn.item_id}">',
            f"質問: {_escape(turn.question)}",
            f"回答: {answer}",
            "</turn>",
        ]
    lines.append("</hearing_log>")
    return "\n".join(lines)


def _hearing_items_block(state: HearingState, framework: Framework) -> str:
    """診断項目の一覧と、現在の充足状況・質問回数・まだ質問してよいか。

    質問回数などはAIに数えさせず、プログラムが数えて渡す（item_rules.py）。
    """
    lines = ["<hearing_items>"]
    for item in framework.hearing_items:
        status = state.item_status.get(item.id)
        lines += [
            f"- {item.id} {item.name}：{item.description}",
            f"  充足の条件：{item.sufficient_when}",
            f"  現在の状況：{status.status if status else '未確認'}",
            f"  これまでの質問回数：{question_count(state, item.id)}回",
        ]
        reason = blocked_reason(state, item.id)
        if reason:
            lines.append(f"  ※この項目はもう質問できません（{reason}）")
        if status and status.known_facts:
            lines.append(f"  分かっていること：{_escape(status.known_facts)}")
    lines.append("</hearing_items>")
    askable = askable_item_ids(state, framework)
    lines.append(f"質問してよい項目：{'、'.join(askable) if askable else 'なし'}")
    return "\n".join(lines)


def _viewpoints_block(framework: Framework) -> str:
    lines = ["<viewpoints>"]
    lines += [f"- {v.id} {v.name}" for v in framework.viewpoints]
    lines.append("</viewpoints>")
    return "\n".join(lines)


def build_question_input(info: BusinessInfo, state: HearingState, framework: Framework) -> str:
    """質問生成のAIに渡す文章。"""
    remaining = MAX_HEARING_TURNS - len(state.turns)
    return "\n\n".join(
        [
            _business_info_block(info),
            _hearing_log_block(state),
            _hearing_items_block(state, framework),
            f"残りの質問回数: {remaining}回",
            "各診断項目の充足状況を判定し、次の質問を1つ作ってください。",
        ]
    )


def _attachments_block(attachments: list[Attachment]) -> str:
    """添付ファイル（F09）。根拠に使えるよう、ファイルに番号を付ける。"""
    if not attachments:
        return "<attachments>\n（添付ファイルはありません）\n</attachments>"
    lines = ["<attachments>"]
    for no, a in enumerate(attachments, start=1):
        lines += [f'<file no="{no}" name="{_escape(a.name)}">', _escape(a.text), "</file>"]
    lines.append("</attachments>")
    return "\n".join(lines)


def build_analysis_input(
    info: BusinessInfo,
    state: HearingState,
    framework: Framework,
    attachments: list[Attachment] | None = None,
) -> str:
    """課題分析のAIに渡す文章。添付ファイルは課題分析にだけ渡す（決定事項：費用の抑制）。"""
    return "\n\n".join(
        [
            _business_info_block(info),
            _hearing_log_block(state),
            _attachments_block(attachments or []),
            _hearing_items_block(state, framework),
            _viewpoints_block(framework),
            "評価観点ごとに業務の課題を分析してください。",
        ]
    )


def build_proposal_input(
    info: BusinessInfo, state: HearingState, analysis: AnalysisResult
) -> str:
    """改善提案のAIに渡す文章。分析結果は引用照合（格下げ）を反映したものを渡すこと。"""
    # 分析結果には利用者の発言の引用が含まれるので、同じくエスケープする
    analysis_json = _escape(analysis.model_dump_json(indent=2))
    return "\n\n".join(
        [
            _business_info_block(info),
            _hearing_log_block(state),
            f"<analysis>\n{analysis_json}\n</analysis>",
            "課題分析の結果をもとに、改善案を最大3つ提案してください。",
        ]
    )
