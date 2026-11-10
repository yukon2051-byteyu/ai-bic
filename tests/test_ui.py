"""ui.py のテスト（画面の部品）。

AIの回答や利用者の入力をHTMLに埋め込むため、<script> などがそのまま
動いてしまわないこと（XSS対策）を中心に確認する。
"""

from ai_bic import ui
from ai_bic.overview import CONFIRMATION, CompareRow, Gap, Overview, RelationTag
from ai_bic.models import ProposalRelation, Evidence, HearingTurn, Issue
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


def test_改善案カードと質問と冒頭と比較表も攻撃の文字列を無効化する():
    proposal = make_proposal(title=ATTACK, before=ATTACK, after=ATTACK, first_steps=[ATTACK])
    turn = HearingTurn(turn_no=1, item_id="H5", question=ATTACK, answer=ATTACK)
    top = make_issue(title=ATTACK)
    ov = Overview(top_issue=top, top_confirmation=CONFIRMATION["本人の申告"], first_action=ATTACK,
                  gaps=[Gap("item:H3", ATTACK, ATTACK, [ATTACK], True)])
    rows = [CompareRow(1, ATTACK, ATTACK, ATTACK, ATTACK, False, ATTACK, 0, [RelationTag("先にやる", 2, ATTACK)])]
    relation = ProposalRelation(kind="順序", first=1, second=2, reason=ATTACK)
    for html in [ui.proposal_card(1, proposal), ui.qa(turn), ui.decision_card(ov, 2), ui.gap_list(ov.gaps),
                 ui.compare_table(rows), ui.relation_list([relation])]:
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


def test_冒頭は最大の課題_最初にやること_次の判断に必要な情報の順で_確認状態を記号と文字で示す():
    ov = Overview(top_issue=make_issue(title="担当者1人に依存", severity="高"),
                  top_confirmation=CONFIRMATION["本人の申告"], first_action="手順を書き出す",
                  gaps=[Gap("item:H3", "作業時間を教えてください", "試算に必要", ["改善効果の試算"], True),
                        Gap("issue:I2", "誤りは月に何件ですか", "根拠がない", ["案1"], False),
                        Gap("followup:I1", "3つ目", "参考", ["案2"], False)])
    card = ui.decision_card(ov, 2)
    assert card.index("最大の課題") < card.index("最初にやること") < card.index("次の判断に必要な情報")
    assert "✓ 確認済み（ご本人の発言・資料）" in card and "△ AIの提案" in card
    assert "作業時間を教えてください" in card and "誤りは月に何件ですか" in card
    assert "3つ目" not in card and "（ほか1件）" in card  # 最初の画面に収めるため、残りは件数で示す
    assert "！は重要な未確認の情報です" in card


def test_不足情報がなければ_冒頭でそう伝える():
    ov = Overview(top_issue=None, top_confirmation="", first_action="一手")
    card = ui.decision_card(ov, 2)
    assert "課題を特定できませんでした" in card and "大きな不足はありません" in card


def test_比較表は改善案_主に減らすもの_着手前に確認することの順で_狭い画面用の項目名を持つ():
    rows = [CompareRow(1, "変換表と手順", "正式名", "業務標準化", "読み替えの誤り", False, "書き方を一覧にする", 2,
                       [RelationTag("先にやる", 2, "準備になる")]),
            CompareRow(2, "自動取り込み", "正式名2", "既存ツールの活用", "課題A、課題B", True, "特になし", 0, [])]
    table = ui.compare_table(rows)
    assert table.index("<th>改善案</th>") < table.index("<th>主に減らすもの</th>") < table.index("<th>着手前に確認すること</th>")
    assert 'data-label="主に減らすもの"' in table and 'data-label="着手前に確認すること"' in table
    assert "案2の前に先にやる" in table and 'title="準備になる"' in table
    assert "（ほか2件）" in table and "（対象の課題から）" in table


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



# --- TOP画面（コンパクト化） ---
def test_特徴カードの質問数は実装の設定と一致する():
    from ai_bic import config

    cards = ui.features()
    total = config.MAX_HEARING_TURNS + config.MAX_EXTRA_QUESTIONS
    assert f"必要なことだけ、最大{total}問" in cards
    assert f"基本{config.MAX_HEARING_TURNS}問。情報が不足する場合のみ、最大{config.MAX_EXTRA_QUESTIONS}問を追加できます。" in cards
    assert cards.count('class="no"') == 3  # 3カラムのカードは3枚のまま



def test_どの画面でもブランド名はTOPへのリンク():
    for current in (None, "hearing", "result"):
        bar = ui.topbar([("hearing", "02 ヒアリング"), ("result", "04 診断結果")], current)
        assert '<a class="bic-brand" href="./" target="_self"' in bar
        assert "入力中の内容は消えます" in bar  # 押す前に分かるように


def test_ご利用上の注意はラベルだけ太字で_配色はこのバナーだけに限る():
    html = ui.notice("ご利用上の注意", ATTACK)
    assert '<span class="bic-notice-label">ご利用上の注意</span>' in html
    assert "<script>" not in html  # 本文も HTML として解釈させない
    assert "--notice-bg: #E7EDF5" in ui.CSS and "--notice-line: #647C9B" in ui.CSS
    assert ".bic-note.bic-notice {" in ui.CSS  # 普通の注記（.bic-note）の配色は変えない
    assert "bic-notice" not in ui.note("x")


def test_タブ名とアイコンは共通の設定にまとめ_アイコンはSVGの原本を使う():
    import xml.etree.ElementTree as ET

    from ai_bic import config

    assert config.APP_PAGE_TITLE == "AI-BIC｜業務改善診断"
    svg = ET.parse(config.APP_FAVICON).getroot()
    ns = "{http://www.w3.org/2000/svg}"
    rect, line = svg.find(ns + "rect"), svg.find(ns + "polyline")
    assert rect.get("fill") == ui.ACCENT  # 既存のブランドの濃紺
    assert line.get("stroke") == "#C9B37E"
    assert line.get("stroke-linecap") == "round" and line.get("stroke-linejoin") == "round"
    ys = [float(p.split(",")[1]) for p in line.get("points").split()]
    downs = sum(1 for a, b in zip(ys, ys[1:]) if b > a)  # 画面の y は下向きが大きい
    assert ys[-1] < ys[0] and downs == 1  # 右上がりで、途中で一度だけ下がる
    assert svg.find(ns + "text") is None  # 文字は入れない
