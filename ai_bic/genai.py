"""生成AIの活用案（AI-BIC 3.4）。AIは使わない（AIが書いた案を、入力の文で確かめる）。

- 生成AIを使う案は、種類「生成AI活用」（案の中心が生成AI）か、別の案に併用できる作業（Proposal.genai）として示す
- 社内で承認された生成AIが使えるという根拠が、業務情報・回答・添付資料の文にない限り、
  「社内で承認された生成AIと入力可能なデータ範囲の確認が必要」と必ず示す（AIの判断をそのまま信じない）
- 「クラウド不可」「社外に出せない」などの制約がある案件で、承認の根拠のない生成AI案を最初に取り組む案（1番目）にしない
- AI-BIC は使い方の提案と試行計画までを行う。提案先の生成AIサービスへの接続や業務の実行はしない
"""

import re

from ai_bic.models import BusinessInfo, HearingState, Proposal, ProposalResult

CATEGORY = "生成AI活用"
NOTICE = "社内で承認された生成AIと入力可能なデータ範囲の確認が必要"
APPROVED_TEXT = "社内で承認された生成AIが使える（業務情報・回答で確認済み）"

_AI = re.compile(r"生成AI|ChatGPT|Copilot|コパイロット|Gemini|Claude|AIアシスタント|AIチャット|対話型AI")
_ALLOW = re.compile(r"承認|許可|利用でき|使え(る|ます)|使って(いる|います)|導入済|導入して|利用して|利用可")
_DENY = re.compile(r"ない|不可|禁止|できません|NG|だめ|ダメ|未承認|検討中|分から|不明")
_BLOCKED = re.compile(
    r"(クラウド|社外|外部|インターネット|ネット上)[^。\n]*?(使えない|使えません|不可|禁止|出せない|出せません|出さない|置けない|置けません|"
    r"持ち出せない|持ち出せません|利用できない|利用できません|送れない|送れません)")
_SENSITIVE = re.compile(r"個人情報|氏名|名前|住所|電話番号|社外秘|機密|顧客情報")


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"[。\n！？]", text) if s.strip()]


def input_text(info: BusinessInfo, state: HearingState, attachments_text: str = "") -> str:
    """生成AIの利用条件を確かめる元の文（業務情報・ヒアリングの回答・添付資料）。"""
    parts = [info.overview, info.pain_points, *(t.answer or "" for t in state.turns), attachments_text]
    return "\n".join(p for p in parts if p)


def allowed(text: str) -> bool:
    """社内で承認された生成AIが使えると、入力の文で述べられているか（否定・未定の文は数えない）。"""
    return any(_AI.search(s) and _ALLOW.search(s) and not _DENY.search(s) for s in _sentences(text))


def blocked(text: str) -> bool:
    """クラウドや社外のサービスを使えない・データを社外に出せない、という制約があるか。"""
    return bool(_BLOCKED.search(text))


def sensitive(text: str) -> bool:
    """個人情報や社外秘を扱うと述べられているか。"""
    return bool(_SENSITIVE.search(text))


def uses_genai(p: Proposal) -> bool:
    """案の中心が生成AIか（種類「生成AI活用」）。"""
    return p.category == CATEGORY


def assists(p: Proposal) -> bool:
    """生成AIを、別の案に併用できる作業として示しているか。"""
    return p.genai is not None and not uses_genai(p)


def label(p: Proposal) -> str:
    """比較表・カードに出す見分け（生成AI活用・生成AIを併用）。生成AIを使わない案は空。"""
    return "生成AI活用" if uses_genai(p) else "生成AIを併用" if assists(p) else ""


def condition_text(p: Proposal) -> str:
    """利用条件の表示。承認の根拠がなければ、必ず確認が必要と示す。"""
    if p.genai is None:
        return ""
    return APPROVED_TEXT if p.genai.approved else NOTICE


def normalize(result: ProposalResult, text: str) -> ProposalResult:
    """AIが書いた「承認済み」を、入力の文で確かめる。根拠がなければ未承認に戻し、着手条件に確認を加える。"""
    ok = allowed(text)
    for p in result.proposals:
        normalize_one(p, ok)
    return result


def normalize_one(p: Proposal, ok: bool) -> None:
    if p.genai is None:
        return
    p.genai.approved = p.genai.approved and ok
    if not p.genai.approved and not any("承認" in c for c in p.genai.conditions):
        p.genai.conditions.insert(0, NOTICE)


def problems(result: ProposalResult, text: str) -> list[str]:
    """生成AIの案の確認（output_check から呼ぶ）。違反があれば、AIに作り直させる。"""
    found = []
    ok, no_cloud = allowed(text), blocked(text)
    for i, p in enumerate(result.proposals, start=1):
        if uses_genai(p) and p.genai is None:
            found.append(f"改善案{i}: 生成AI活用なのに、生成AIの使い方（対象工程・入力と出力・人の確認など）がない")
        if p.genai is not None:
            g = p.genai
            empty = [n for n, v in [("対象工程", g.target_step), ("AIへの入力", g.ai_input), ("AIの出力", g.ai_output),
                                    ("人が確認すること", g.human_check), ("効果の見込み", g.effect),
                                    ("最初に試す範囲", g.first_trial)] if not v.strip()]
            if empty:
                found.append(f"改善案{i}: 生成AIの使い方の{'・'.join(empty)}が空")
        # クラウド不可・社外に出せない案件で、承認の根拠がない生成AI案を最初に取り組む案にしない
        if i == 1 and uses_genai(p) and no_cloud and not ok:
            found.append("改善案1: クラウドや社外のサービスを使えない制約があり、社内で承認された生成AIの根拠もないのに、"
                         "生成AI活用の案を最初に取り組む案にしている")
    return found
