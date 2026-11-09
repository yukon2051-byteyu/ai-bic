"""ui.py のテスト（画面の部品）。

AIの回答や利用者の入力をHTMLに埋め込むため、<script> などがそのまま
動いてしまわないこと（XSS対策）を中心に確認する。
"""

from ai_bic import ui
from ai_bic.models import DiagnosisSummary, Evidence, HearingTurn, Issue
from tests.fakes import make_proposal

ATTACK = '<script>alert("x")</script><img src=x onerror=alert(1)>'


def make_issue(**override) -> Issue:
    data = {
        "issue_id": "I1", "title": "転記の負担", "summary": "転記に時間がかかる",
        "viewpoint_ids": ["V1"], "evidence_type": "本人の申告",
        "evidences": [Evidence(source="業務情報", turn_no=None, file_no=None, quote="転記に時間がかかり", supports="転記に時間がかかっている")],
        "calculation": None,
        "assumed_cause": "様式の違い", "impact": "残業", "severity": "高",
        "severity_reason": "月20時間以上", "followup_questions": ["何時間かかりますか"],
    }
    data.update(override)
    return Issue(**data)


def test_escは記号を文字として表示できる形に変える():
    assert ui.esc("<b>&\"'") == "&lt;b&gt;&amp;&quot;&#x27;"
    assert ui.esc(None) == ""


def test_escはドル記号も数式にならない形にする():
    # Streamlit は $...$ を数式として表示するため
    assert "$" not in ui.esc("費用は$20")


def test_課題カードに埋め込まれた攻撃の文字列は動かない():
    issue = make_issue(title=ATTACK, summary=ATTACK, followup_questions=[ATTACK])
    card = ui.issue_card(1, issue, ATTACK, [ATTACK], downgraded=None)
    assert "<script>" not in card
    assert "<img" not in card
    assert "&lt;script&gt;" in card


def test_改善案カードと質問とサマリーも攻撃の文字列を無効化する():
    proposal = make_proposal(title=ATTACK, before=ATTACK, after=ATTACK, first_steps=[ATTACK])
    turn = HearingTurn(turn_no=1, item_id="H5", question=ATTACK, answer=ATTACK)
    summary = DiagnosisSummary(headline=ATTACK, top_issue_id="I1", first_action=ATTACK)
    top = make_issue(title=ATTACK)
    for html in [ui.proposal_card(1, proposal), ui.qa(turn), ui.summary_card(summary, top, [top], 1)]:
        assert "<script>" not in html
        assert "<img" not in html


def test_課題カードには重要度と根拠の種別と格下げの注記が出る():
    message = "AIは「本人の申告」と判断しましたが、根拠となる発言を確認できなかったため「要確認」としています。"
    card = ui.issue_card(2, make_issue(), "観点：業務効率", ["業務情報"], downgraded=message)
    assert "02" in card
    assert "重要度 高" in card
    assert "本人の申告：発言で確認済み" in card
    assert message in card  # 格下げの理由を、そのまま表示する
    assert "転記に時間がかかり" in card
    assert "→ この引用から言えること：転記に時間がかかっている" in card  # 引用と主張の対応（B04）


def test_計算結果の課題には計算式が出る():
    card = ui.issue_card(
        1, make_issue(evidence_type="計算結果", calculation="90分×20日÷60＝30時間"), "観点", ["業務情報"], None
    )
    assert "計算結果：発言の数値から計算" in card
    assert "90分×20日÷60＝30時間" in card


def test_改善案カードには導入前後と最初の一歩が出る():
    card = ui.proposal_card(1, make_proposal())
    assert "BEFORE" in card and "AFTER" in card
    assert "最初の一歩" in card
    assert "現在の様式を集めて違いを一覧にする" in card


def test_サマリーは最重要課題を太字の枠で示し_件数を数える():
    summary = DiagnosisSummary(headline="最大の課題は属人化です", top_issue_id="I1", first_action="手順を書き出す")
    issues = [
        make_issue(issue_id="I1", title="担当者1人に依存", severity="高"),
        make_issue(issue_id="I2", severity="中", evidence_type="要確認"),
        make_issue(issue_id="I3", severity="高", evidence_type="推測"),
    ]
    card = ui.summary_card(summary, issues[0], issues, 3)

    assert '<div class="t-main">担当者1人に依存</div>' in card
    assert "重要度 高" in card
    assert '<div class="t-main">手順を書き出す</div>' in card
    assert '<span class="n">3</span><span>抽出した課題</span>' in card
    assert '<span class="n hot">2</span><span>重要度「高」</span>' in card  # 「高」があれば強調
    assert '<span class="n">1</span><span>要確認</span>' in card


def test_重要度高がなければ件数を強調しない():
    summary = DiagnosisSummary(headline="要点", top_issue_id="I1", first_action="一手")
    issues = [make_issue(severity="中")]
    card = ui.summary_card(summary, issues[0], issues, 1)
    assert '<span class="n">0</span><span>重要度「高」</span>' in card


def test_最重要課題が見つからなくても表示が崩れない():
    summary = DiagnosisSummary(headline="要点", top_issue_id="I9", first_action="一手")
    card = ui.summary_card(summary, None, [], 0)
    assert "課題を特定できませんでした" in card


def test_重要度高の課題だけ_カードを金色で強調する():
    high = ui.issue_card(1, make_issue(severity="高"), "観点", ["業務情報"], downgraded=None)
    mid = ui.issue_card(2, make_issue(severity="中"), "観点", ["業務情報"], downgraded=None)
    assert 'class="bic-card sev-high"' in high
    assert 'class="bic-card sev-mid"' in mid


def test_課題の補足は表にまとめる():
    card = ui.issue_card(1, make_issue(), "観点", ["業務情報"], downgraded=None)
    assert '<dl class="bic-dl">' in card
    assert "<dt>想定原因（推測）</dt><dd>様式の違い</dd>" in card
    assert "<dt>追加で確認したいこと</dt>" in card


def test_1つ目の改善案にだけ_最初に取り組む案の印がつく():
    first = ui.proposal_card(1, make_proposal())
    second = ui.proposal_card(2, make_proposal())
    assert "最初に取り組む案" in first and "recommended" in first
    assert "最初に取り組む案" not in second and "recommended" not in second


def test_改善案の推奨理由を強調枠で示す():
    card = ui.proposal_card(1, make_proposal(reason="費用をかけずに始められる"))
    assert '<div class="bic-reason"><b>推奨理由</b>費用をかけずに始められる</div>' in card


def test_書体の指定でアイコンを壊さない():
    # 部品全体（class に st- を含む要素）に書体を指定すると、アイコンが
    # 「visibility」「arrow_right」のような文字のまま表示される不具合が起きた（再発防止）
    assert '[class*="st-"]' not in ui.CSS
    assert '[data-testid="stIconMaterial"]' in ui.CSS
    assert '"Material Symbols Rounded" !important' in ui.CSS


def test_進み具合は今の段階を強調する():
    steps = [("input", "01 業務情報"), ("hearing", "02 ヒアリング")]
    bar = ui.topbar(steps, "hearing")
    assert '<span class="now">02 ヒアリング</span>' in bar
    assert '<span class="done">01 業務情報</span>' in bar
    assert "bic-steps" not in ui.topbar(steps, "top")  # トップでは進み具合を出さない
