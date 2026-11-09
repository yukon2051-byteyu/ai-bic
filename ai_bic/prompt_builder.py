"""AIに渡す文章の組み立て（詳細設計書 2章・3.3）。

- システムプロンプト（AIの役割とルール）は ai_bic/prompts/*.md から読み込む
- ユーザーメッセージ（業務情報・ヒアリング記録など）はここで組み立てる

利用者の入力は <business_info> などのタグで囲み、「データ」としてAIに渡す。
入力の中に「</business_info>」のような文字列を書いてタグの外に抜け出し、
AIに指示を出す攻撃（プロンプトインジェクション）を防ぐため、
利用者の入力に含まれる < と > は全角の ＜ ＞ に置き換える。
（引用の照合では normalize() が全角を半角に戻すので、照合には影響しない）
"""

import json
from pathlib import Path

from ai_bic.config import MAX_EXTRA_QUESTIONS, MAX_HEARING_TURNS
from ai_bic.item_rules import askable_item_ids, blocked_reason, question_count
from ai_bic.models import (
    AnalysisResult,
    Attachment,
    BusinessInfo,
    Framework,
    HearingState,
    Proposal,
    ProposalResult,
    ReviewFinding,
)

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
        if status and status.status != "充足" and status.lacking:
            lines.append(f"  足りないこと：{_escape(status.lacking)}")
    lines.append("</hearing_items>")
    askable = askable_item_ids(state, framework)
    lines.append(f"質問してよい項目：{'、'.join(askable) if askable else 'なし'}")
    return "\n".join(lines)


def _viewpoints_block(framework: Framework) -> str:
    lines = ["<viewpoints>"]
    lines += [f"- {v.id} {v.name}" for v in framework.viewpoints]
    lines.append("</viewpoints>")
    return "\n".join(lines)


def build_question_input(
    info: BusinessInfo, state: HearingState, framework: Framework, final: bool = False
) -> str:
    """質問生成のAIに渡す文章。final=True はヒアリング終了後の、判定だけの依頼。"""
    if final:
        request = [
            "ヒアリングは終了しました。最後の回答も含めて、各診断項目の充足状況を判定してください。"
            "質問は作らず、is_sufficient を true、next_item_id・question_type・question・answer_example を null にしてください。"
        ]
    elif state.extra_started:
        # 追加確認（AI-BIC 2.0）：質問してよい項目は、分析に必要な情報が足りない項目だけに絞られている
        remaining = MAX_EXTRA_QUESTIONS - sum(1 for turn in state.turns if turn.extra)
        request = [
            f"これは基本のヒアリングのあとの「追加確認」です（残り{remaining}回まで）。"
            "業務フロー・改善効果の試算・改善案のレビューに必要な情報が足りない項目だけを、最小限の質問で確認します。",
            "各診断項目の充足状況を判定し、「質問してよい項目」の中から、最も重要なものについて質問を1つ作ってください。",
        ]
    else:
        remaining = MAX_HEARING_TURNS - len(state.turns)
        request = [
            f"残りの質問回数: {remaining}回",
            "各診断項目の充足状況を判定し、次の質問を1つ作ってください。",
        ]
    return "\n\n".join(
        [
            _business_info_block(info),
            _hearing_log_block(state),
            _hearing_items_block(state, framework),
            *request,
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


def build_flow_input(
    info: BusinessInfo, state: HearingState, analysis: AnalysisResult, proposals: ProposalResult
) -> str:
    """業務フロー（As-Is・To-Be）のAIに渡す文章（AI-BIC 2.0 機能B）。

    改善案には番号（1から）をつけて渡し、To-Be の proposal_no と対応させる。
    """
    analysis_json = _escape(analysis.model_dump_json(indent=2))
    numbered = [
        {"proposal_no": no, **p.model_dump(include={"title", "category", "description", "before", "after", "target_issue_ids"})}
        for no, p in enumerate(proposals.proposals, start=1)
    ]
    proposals_json = _escape(json.dumps(numbered, ensure_ascii=False, indent=2))
    return "\n\n".join(
        [
            _business_info_block(info),
            _hearing_log_block(state),
            f"<analysis>\n{analysis_json}\n</analysis>",
            f"<proposals>\n{proposals_json}\n</proposals>",
            f"現状の業務フローと、改善案{len(numbered)}つそれぞれの改善後の業務フローを作ってください。",
        ]
    )


def build_review_input(
    info: BusinessInfo, state: HearingState, analysis: AnalysisResult, proposal_no: int, material: str
) -> str:
    """改善案チャレンジレビューのAIに渡す文章（AI-BIC 2.0 機能D）。

    material は review.review_context が作る資料（改善案・試算・改善後フロー・充足度・プログラムの検証結果）。
    改善案や試算には利用者の入力が含まれるため、エスケープしてから渡す。
    """
    return "\n\n".join(
        [
            _business_info_block(info),
            _hearing_log_block(state),
            f"<analysis>\n{_escape(analysis.model_dump_json(indent=2))}\n</analysis>",
            f"<review_material>\n{_escape(material)}\n</review_material>",
            f"改善案{proposal_no}を、6つの観点で批判的に評価してください。",
        ]
    )


def build_revise_input(
    info: BusinessInfo, state: HearingState, analysis: AnalysisResult, proposal: Proposal, adopted: list[ReviewFinding]
) -> str:
    """採用した指摘を反映して改善案を作り直すAIに渡す文章（AI-BIC 2.0 機能D）。"""
    findings = [f.model_dump(include={"viewpoint", "severity", "title", "detail", "suggestion"}) for f in adopted]
    return "\n\n".join(
        [
            _business_info_block(info),
            _hearing_log_block(state),
            f"<analysis>\n{_escape(analysis.model_dump_json(indent=2))}\n</analysis>",
            f"<proposal>\n{_escape(proposal.model_dump_json(indent=2))}\n</proposal>",
            f"<adopted_findings>\n{_escape(json.dumps(findings, ensure_ascii=False, indent=2))}\n</adopted_findings>",
            f"採用された指摘{len(adopted)}件だけを反映して、改善案を作り直してください。",
        ]
    )
