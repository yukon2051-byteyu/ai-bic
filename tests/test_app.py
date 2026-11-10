"""画面の結合テスト（Streamlit の AppTest で app.py を実際に動かす）。

ブラウザを使わずに、ボタンを押す・入力するといった操作を再現する。
AIモードは本物のAPIを呼ばない範囲（パスワード・回数制限）まで確認する。
"""

from datetime import datetime

import pytest
from streamlit.testing.v1 import AppTest

from ai_bic import config, guard
from ai_bic.config import DAILY_DIAGNOSIS_LIMIT, JST
from ai_bic.usage_log import count_slots_on, init_db, reserve_slot


@pytest.fixture
def secrets(monkeypatch):
    """Secrets を偽物に差し替える（本物の secrets.toml や環境変数に左右されないように）。"""
    values = {}
    monkeypatch.setattr(guard, "get_secret", lambda name: values.get(name) or None)
    return values


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "usage_test.db")
    monkeypatch.setattr(config, "DB_PATH", path)
    return path


APP_PATH = str(config.BASE_DIR / "app.py")  # 相対パスはテストファイルの場所が基準になるため絶対パスにする


SALES = "月次売上集計（営業管理部）"


def new_app(secrets, scenario: str | None = SALES) -> AppTest:
    """アプリを起動する。デモは、既存のテストが前提にしている月次売上集計を選んでおく。

    デモの初期表示は製造業（月次生産実績集計）。初期表示のまま使うテストは scenario=None を渡す。
    """
    at = AppTest.from_file(APP_PATH, default_timeout=60)  # 結果画面の処理が増えたため（PCの負荷で30秒を超えることがあった）
    at.run()
    assert not at.exception
    if scenario:
        at.selectbox(key="demo_scenario").set_value(scenario).run()
    return at


def all_text(at) -> str:
    parts = [m.value for m in at.markdown] + [e.value for e in at.error] + [c.value for c in at.caption]
    return "\n".join(str(p) for p in parts)


# ---------------------------------------------------------------
# デモモード：トップ → 業務情報 → ヒアリング → 診断結果
# ---------------------------------------------------------------
def test_デモモードで最後まで診断できる(secrets):
    at = new_app(secrets)
    assert at.session_state.stage == "top"

    at.button(key="start_demo").click().run()
    assert at.session_state.stage == "input"
    assert at.session_state.mode == "demo"
    assert "デモモード：AIは使用していません" in all_text(at)  # デモであることを常に表示（F05）

    # 入力欄にはシナリオの内容が入っている（決定事項E2）
    assert at.text_input(key="f_department").value == "営業管理部"
    at.button(key="submit_info").click().run()
    assert at.session_state.stage == "hearing"
    assert len(at.session_state.hearing_state.turns) == 1

    # 用意された回答で4問に答えると、資料の添付に進む（ここではAIを呼ばない）
    for _ in range(4):
        at.button(key="submit_answer").click().run()
        assert not at.exception
    assert at.session_state.stage == "attach"
    assert at.session_state.diagnosis is None
    assert "集計差異の記録.csv" in at.expander[0].label  # 見本ファイルを添付済みで表示

    at.button(key="diagnose").click().run()

    assert at.session_state.stage == "result"
    text = all_text(at)
    assert "集計作業が担当者1人に依存している" in text
    assert "根拠となる発言を入力内容の中に確認できなかった" in text  # I5 の格下げ表示
    # 添付ファイルからの根拠が、ファイル名つきで表示される（F09）
    assert "添付ファイル『集計差異の記録.csv』" in text
    assert "診断に使った資料" in text
    # 冒頭の3項目（最大の課題・最初にやること・次の判断に必要な情報）と、比較表
    assert "最大の課題" in text and "最初にやること" in text and "次の判断に必要な情報" in text
    assert "主に減らすもの" in text and "着手前に確認すること" in text
    # 選んだ案の詳細（導入前後の比較・最初の一歩）
    at.radio(key="compare_plan").set_value("P2").run()
    text = all_text(at)
    assert "BEFORE" in text and "AFTER" in text
    assert "5つの営業所の現在の様式を集め、項目の違いを一覧表にする" in text
    at.radio(key="compare_plan").set_value("P3").run()
    assert "表計算ソフトの集計機能による自動集計" in all_text(at)

    # 新しい診断を始めるとトップに戻り、内容は消える
    at.button(key="new").click().run()
    assert at.session_state.stage == "top"
    assert at.session_state.business_info is None


@pytest.mark.parametrize(
    ("title", "department", "answers", "expected_text"),
    [
        ("修理依頼の受付・手配（お客様相談室）", "お客様相談室", 5, "より簡単な方法では不十分な理由"),
        ("交通費の精算・承認（総務部）", "総務部", 2, "会計ソフトの取り込み用ファイルを自動作成"),
    ],
)
def test_プルダウンで別のシナリオを選んで最後まで診断できる(
    secrets, title, department, answers, expected_text
):
    at = new_app(secrets)
    assert len(at.selectbox(key="demo_scenario").options) >= 3

    at.selectbox(key="demo_scenario").set_value(title).run()
    at.button(key="start_demo").click().run()
    assert at.text_input(key="f_department").value == department
    at.button(key="submit_info").click().run()

    for _ in range(answers):
        at.button(key="submit_answer").click().run()
        assert not at.exception
    if at.session_state.stage == "readiness":  # 修理受付は制約条件が不十分で、追加確認を提案される
        at.button(key="extra_skip").click().run()
    assert at.session_state.stage == "attach"
    at.button(key="diagnose").click().run()

    assert at.session_state.stage == "result"
    at.radio(key="compare_plan").set_value("P3").run()  # 比較の下には、選んだ案の詳細だけを出す
    assert expected_text in all_text(at)
    assert "添付ファイル『" in all_text(at)  # どのシナリオも添付ファイルを根拠に使っている


def test_修理受付のデモは不足項目を表示する(secrets):
    at = new_app(secrets)
    at.selectbox(key="demo_scenario").set_value("修理依頼の受付・手配（お客様相談室）").run()
    at.button(key="start_demo").click().run()
    at.button(key="submit_info").click().run()
    for _ in range(5):
        at.button(key="submit_answer").click().run()
    at.button(key="extra_skip").click().run()  # 追加確認をせずに分析する
    at.button(key="diagnose").click().run()
    # 5問で終わり、5問目の回答（予算は相談次第）も判定したうえで、不足項目と理由を表示（B01）
    text = all_text(at)
    assert "情報が不足しています。結果は参考としてご覧ください。" in text
    assert "改善に向けた制約条件（不十分）　足りないこと：予算の目安や、使ってよいツール・社内ルールの有無" in text
    # 情報が足りないまま分析したので、仮定条件も示す（AI-BIC 2.0 ヒアリングの改善 7）
    assert "仮定条件：業務上の問題点または改善の制約条件が未確認のため" in text


def test_結果画面の冒頭と末尾に_診断レポートのダウンロードボタンがある(secrets):
    at = new_app(secrets)
    at.button(key="start_demo").click().run()
    at.button(key="submit_info").click().run()
    while at.session_state.stage == "hearing":
        at.button(key="submit_answer").click().run()
    at.button(key="diagnose").click().run()

    assert not at.exception
    keys = [b.proto.id for b in at.get("download_button") if "download_report" in b.proto.id]
    assert len(keys) == 2  # 長い結果を読み終えた後でも、すぐ保存できるように末尾にも置く
    assert "download_report_top" in keys[0] and "download_report_bottom" in keys[-1]
    assert "結果画面からExcelのレポートをダウンロード" not in all_text(at)  # 案内はトップ画面だけ


def test_トップ画面で_レポートで結果を控えるよう案内する(secrets):
    at = new_app(secrets)
    assert "結果画面からExcelのレポートをダウンロードして控えてください" in all_text(at)


def test_ヒアリングの途中で_ここまでの情報で診断できる(secrets):
    at = new_app(secrets)
    at.button(key="start_demo").click().run()
    at.button(key="submit_info").click().run()
    at.button(key="submit_answer").click().run()  # 1問だけ回答

    at.button(key="finish").click().run()
    assert at.session_state.stage == "attach"
    at.button(key="diagnose").click().run()

    assert at.session_state.stage == "result"
    assert "情報が不足しています" in all_text(at)  # 不足項目を明示（R6）


def test_資料の添付の段階で中止するとトップに戻る(secrets):
    at = new_app(secrets)
    at.button(key="start_demo").click().run()
    at.button(key="submit_info").click().run()
    at.button(key="finish").click().run()

    at.button(key="cancel").click().run()

    assert at.session_state.stage == "top"
    assert at.session_state.attachments == []


def test_診断を中止するとトップに戻り内容が消える(secrets):
    at = new_app(secrets)
    at.button(key="start_demo").click().run()
    at.button(key="submit_info").click().run()

    at.button(key="cancel").click().run()

    assert at.session_state.stage == "top"
    assert at.session_state.hearing_state is None


# ---------------------------------------------------------------
# 入力ミス（異常系）
# ---------------------------------------------------------------
def test_必須項目が空なら次に進めず_項目ごとにエラーを表示する(secrets):
    at = new_app(secrets)
    at.button(key="start_demo").click().run()
    at.text_input(key="f_department").set_value("")
    at.text_area(key="f_pain_points").set_value("   ")
    at.button(key="submit_info").click().run()

    assert at.session_state.stage == "input"
    errors = [e.value for e in at.error]
    assert "部署名を入力してください" in errors
    assert "現在困っていることを入力してください" in errors
    # 入力済みの他の項目は消えていない
    assert at.text_input(key="f_task_name").value == "月次売上集計"


def test_回答が空なら次の質問に進まない(secrets):
    at = new_app(secrets)
    at.button(key="start_demo").click().run()
    at.button(key="submit_info").click().run()
    at.text_area(key="answer_1").set_value("")
    at.button(key="submit_answer").click().run()

    assert "回答を入力してください" in [e.value for e in at.error]
    assert len(at.session_state.hearing_state.turns) == 1


# ---------------------------------------------------------------
# AIモードの入口（APIは呼ばない範囲）
# ---------------------------------------------------------------
def test_APIキーやパスワードが未設定ならAI診断は使えない(secrets):
    at = new_app(secrets)
    assert "AI診断は現在利用できません" in all_text(at)
    assert not [b for b in at.button if b.key == "start_ai"]


def test_パスワードが違えばAI診断を始められない(secrets, db):
    secrets.update(ANTHROPIC_API_KEY="dummy", APP_PASSWORD="correct")
    at = new_app(secrets)
    at.text_input(key="password").set_value("wrong")
    at.button(key="start_ai").click().run()

    assert at.session_state.stage == "top"
    assert "パスワードが正しくありません。" in [e.value for e in at.error]


def test_正しいパスワードならAI診断の入力画面に進む(secrets, db):
    secrets.update(ANTHROPIC_API_KEY="dummy", APP_PASSWORD="correct")
    at = new_app(secrets)
    at.text_input(key="password").set_value("correct")
    at.button(key="start_ai").click().run()

    assert at.session_state.stage == "input"
    assert at.session_state.mode == "ai"
    assert at.text_input(key="f_department").value == ""  # AI診断では入力欄は空
    # 「診断を始める」の時点で、その診断の枠を1つ確保している（X01）
    assert count_slots_on(db, datetime.now(JST).date()) == 1


def test_1日の上限に達していればAI診断を始められない(secrets, db):
    secrets.update(ANTHROPIC_API_KEY="dummy", APP_PASSWORD="correct")
    init_db(db)
    now = datetime.now(JST)
    for n in range(DAILY_DIAGNOSIS_LIMIT):
        reserve_slot(db, f"s{n}", DAILY_DIAGNOSIS_LIMIT, now)

    at = new_app(secrets)
    at.text_input(key="password").set_value("correct")
    at.button(key="start_ai").click().run()

    assert at.session_state.stage == "top"
    assert any("上限" in e.value for e in at.error)


# ---------------------------------------------------------------
# 改善効果の試算（AI-BIC 2.0 機能A。B06 を置き換え）
# ---------------------------------------------------------------
def result_screen(secrets, at: AppTest | None = None) -> AppTest:
    """デモで結果画面まで進める。at を渡すと、その画面（トップ）から続けて進める。"""
    at = at or new_app(secrets)
    at.button(key="start_demo").click().run()
    at.button(key="submit_info").click().run()
    while at.session_state.stage == "hearing":
        at.button(key="submit_answer").click().run()
    if at.session_state.stage == "readiness":
        at.button(key="extra_skip").click().run()
    at.button(key="diagnose").click().run()
    assert not at.exception
    return at




def set_inputs(at: AppTest, pid: str, **values) -> AppTest:
    if at.radio(key="sim_plan").value != pid:  # 入力欄は、選んでいる案のものだけ表示される
        at.radio(key="sim_plan").set_value(pid).run()
    for name, value in values.items():
        at.number_input(key=f"sim_{pid}_{name}").set_value(value).run()
    return at


FULL = dict(minutes_per_case=30.0, cases_per_month=200.0, hourly_cost=3000.0, reduction_rate=40.0,
            initial_cost=500000.0, monthly_running_cost=20000.0, other_initial_cost=100000.0)


def metrics(at: AppTest) -> list[str]:
    return [m.value for m in at.metric][:3]


def test_デモでは試算にデモ値が入っていて_すぐに結果を見られる(secrets):
    """デモで新機能の価値を確認できる（指示書 8章）。値は架空のサンプルと明示する。"""
    at = result_screen(secrets)
    text = all_text(at)
    assert "改善効果の試算" in text
    assert at.number_input(key="sim_P1_minutes_per_case").value == 144  # 144分×5件＝月12時間
    assert "□ デモ値" in text and "架空" in text
    assert "未入力の項目" not in text
    # 案1：月12時間×10%×12か月＝14.4時間、×3,000円＝43,200円、60,000円÷3,600円＝16.7か月
    assert metrics(at) == ["14.4時間", "43,200円", "16.7か月"]
    simulations = at.session_state.project.simulations
    assert all(s.source_of("hourly_cost") == "デモ値" for s in simulations.values())
    assert len(simulations) == 3  # 3つの改善案すべてに入っている


def test_試算の案の切り替えは_番号と短い名前の2段で_選んだ案の正式名と詳細を出す(secrets):
    at = new_app(secrets)
    at.selectbox(key="demo_scenario").set_value("交通費の精算・承認（総務部）").run()
    at = result_screen(secrets, at)
    plans = at.radio(key="sim_plan")
    assert list(plans.options) == ["案1", "案2", "案3"]
    captions = list(plans.proto.captions)
    assert captions == ["記入見本と提出前チェック", "表計算で精算を標準化", "会計ソフトへの連携"]
    assert not any("…" in c for c in captions)  # 省略しない
    assert plans.value == "P1"
    assert "案1：精算書の記入見本と、提出前チェックリストの配布" in all_text(at)  # 正式な名前は詳細の見出しに出す
    plans.set_value("P3").run()
    assert not at.exception
    text = all_text(at)
    assert "案3：精算データから、会計ソフトの取り込み用ファイルを自動作成" in text
    assert "案1：精算書の記入見本" not in text  # 選んでいない案の詳細は出さない
    assert at.number_input(key="sim_P3_minutes_per_case")  # 入力欄も選んだ案のもの


def test_短い名前がない改善案は_正式な名前を省略せずにタブに出す():
    from ai_bic.views.simulator import tab_name
    from tests.fakes import make_proposal

    p = make_proposal()
    assert p.short_title is None  # 古い保存データ（短い名前がない）も読める
    assert tab_name(p) == p.title
    assert tab_name(p.model_copy(update={"short_title": "  見本の配布 "})) == "見本の配布"


def test_デモの改善案にはすべて_重ならない短い名前がある():
    from ai_bic.demo_client import load_scenarios

    for s in load_scenarios():
        shorts = [p["short_title"] for p in s["proposals"]["proposals"]]
        assert len(set(shorts)) == len(shorts) and all(0 < len(x) <= 14 for x in shorts), s["id"]


@pytest.mark.parametrize("title", ["修理依頼の受付・手配（お客様相談室）", "交通費の精算・承認（総務部）"])
def test_ほかのデモにも試算のデモ値が入っている(secrets, title):
    at = new_app(secrets)
    at.selectbox(key="demo_scenario").set_value(title).run()
    at = result_screen(secrets, at)
    assert not at.exception
    assert "—" not in metrics(at)
    assert "シナリオ" in all_text(at) or at.dataframe


def test_試算は_入力を変えるとすぐに更新され_出どころはユーザー入力になる(secrets):
    at = set_inputs(result_screen(secrets), "P1", **FULL)
    assert not at.exception
    assert metrics(at) == ["480時間", "1,440,000円", "6か月"]
    assert "30分 × 200件 ÷ 60 ＝ 100時間" in all_text(at)
    changed = at.session_state.project.simulations["P1"]
    assert changed.source_of("minutes_per_case") == "ユーザー入力"  # デモ値の144分から変えた
    assert changed.source_of("hourly_cost") == "デモ値"  # デモ値と同じ3,000円のままなので変わらない

    at = set_inputs(at, "P1", reduction_rate=20.0)
    assert metrics(at)[0] == "240時間"


def test_削減率はスライダーでも変えられ_入力欄と連動する(secrets):
    at = set_inputs(result_screen(secrets), "P1", **FULL)
    at.slider(key="sim_P1_rate_slider").set_value(60.0).run()
    assert at.number_input(key="sim_P1_reduction_rate").value == 60.0
    assert at.session_state.project.simulations["P1"].reduction_rate == 60


def test_純効果がマイナスなら回収見込みなしと表示する(secrets):
    at = set_inputs(result_screen(secrets), "P1", **{**FULL, "monthly_running_cost": 200000.0})
    assert metrics(at)[2] == "回収見込みなし"


def test_試算を変えると案件の版が上がる(secrets):
    """改善案のレビューが、古い試算のまま有効に見えないための記録（指示書 6章）。"""
    at = result_screen(secrets)
    before = at.session_state.project.stamps["simulation:P1"].version
    at = set_inputs(at, "P1", hourly_cost=3500.0)
    assert at.session_state.project.stamps["simulation:P1"].version == before + 1


def test_改善案が複数あれば前提の違いを示す(secrets):
    at = result_screen(secrets)
    assert len(at.session_state.project.simulations) >= 2  # 月次売上集計のデモは改善案が複数
    at = set_inputs(at, "P1", **FULL)
    at = set_inputs(at, "P2", **{**FULL, "cases_per_month": 300.0})
    assert "前提条件が改善案によって異なる" in "\n".join(w.value for w in at.warning)


def test_試算結果をExcelでダウンロードできる(secrets):
    at = result_screen(secrets)
    assert "download_simulation" in [b.key for b in at.get("download_button")]


def test_新しい診断を始めると_前の試算は残らない(secrets):
    at = set_inputs(result_screen(secrets), "P1", hourly_cost=5000.0)
    at.button(key="new").click().run()
    assert at.session_state.stage == "top"
    at = result_screen(secrets, at)
    assert at.number_input(key="sim_P1_hourly_cost").value == 3000  # 変えた値ではなく、デモ値に戻る


def test_試算の最上部に全改善案の削減時間と回収期間を並べる(secrets):
    """PCで、どの案がどれだけ減り何か月で回収できるかを一目で見られる（開発者の指摘）。"""
    at = result_screen(secrets)
    overview = next(df.value for df in at.dataframe if "改善案" in df.value.columns)
    assert list(overview["改善案"].str[:2]) == ["案1", "案2", "案3"]
    assert list(overview.columns[:5]) == ["改善案", "年間削減時間", "年間削減効果額", "年間純効果額", "投資回収期間"]
    assert overview.loc[1, "年間削減時間"] == "57.6時間" and overview.loc[1, "投資回収期間"] == "6.3か月"


def test_入力値の出どころは1行にまとめ_欄ごとに出さない(secrets):
    at = result_screen(secrets)
    texts = [m.value for m in at.markdown] + [c.value for c in at.caption]
    assert any("□ デモ値（すべての項目）" in t for t in texts)
    assert not any(c.value == "□ デモ値" for c in at.caption)  # 入力欄の下ごとの表示はしない



# ---------------------------------------------------------------
# ヒアリングの改善（AI-BIC 2.0）：情報の充足度に応じた追加確認
# ---------------------------------------------------------------
def repair_until_readiness(secrets) -> AppTest:
    at = new_app(secrets)
    at.selectbox(key="demo_scenario").set_value("修理依頼の受付・手配（お客様相談室）").run()
    at.button(key="start_demo").click().run()
    at.button(key="submit_info").click().run()
    for _ in range(5):
        at.button(key="submit_answer").click().run()
    assert not at.exception
    return at


def test_基本の質問は質問n_5で進み具合を表示する(secrets):
    at = new_app(secrets)
    at.button(key="start_demo").click().run()
    at.button(key="submit_info").click().run()
    assert "質問 1 / 5" in [p.proto.text for p in at.get("progress")][0]


def test_必要な情報が足りなければ_理由とともに追加確認を提案する(secrets):
    at = repair_until_readiness(secrets)
    assert at.session_state.stage == "readiness"
    text = all_text(at)
    assert "より正確な分析のための追加確認" in text
    assert "改善案レビューに必要な「改善に向けた制約条件」が不十分です" in text
    table = at.dataframe[0].value
    assert dict(zip(table["機能"], table["判定"])) == {
        "業務フロー": "✓ 十分", "改善効果の試算": "✓ 十分", "改善案レビュー": "✕ 不足"}
    labels = [b.label for b in at.button]
    assert "追加で確認する（最大3問）" in labels and "このまま分析する" in labels


def test_追加確認を選ぶと_追加の質問に答えて分析に進める(secrets):
    at = repair_until_readiness(secrets)
    at.button(key="extra_start").click().run()
    assert at.session_state.stage == "hearing"
    assert "追加確認 1 / 最大 3" in [p.proto.text for p in at.get("progress")][0]
    assert any("確認する理由" in c.value for c in at.caption)
    at.button(key="submit_answer").click().run()  # デモの回答（制約条件）
    # 必要な情報がそろったので、上限の3問を待たずに資料の添付へ進む
    assert at.session_state.stage == "attach"
    at.button(key="diagnose").click().run()
    text = all_text(at)
    assert "追加確認で、分析に必要な情報がそろいました" in text
    assert "仮定条件" not in text


def test_分からないボタンで答えると_その項目はもう聞かない(secrets):
    at = repair_until_readiness(secrets)
    at.button(key="extra_start").click().run()
    at.button(key="submit_unknown").click().run()
    state = at.session_state.hearing_state
    assert state.turns[-1].answer == "分かりません。"
    # 制約条件は「分からない」と答えたので、追加で聞ける項目がなくなり、資料の添付へ進む
    assert at.session_state.stage == "attach"
    assert state.extra_finished_reason is not None


def test_情報がそろっていれば追加確認を出さない(secrets):
    at = new_app(secrets)
    at.button(key="start_demo").click().run()  # 月次売上集計：最後にすべて充足
    at.button(key="submit_info").click().run()
    while at.session_state.stage == "hearing":
        at.button(key="submit_answer").click().run()
    assert at.session_state.stage == "attach"



def test_トップの注意事項は_個人情報_AIへの送信_保存しないことを残す(secrets):
    """TOP画面のコンパクト化で、重要な注意事項を削らない。"""
    text = all_text(new_app(secrets))
    assert "個人情報や社外秘の情報は入力しないでください" in text
    assert "入力内容はAI（Anthropic社のClaude）に送信され" in text
    assert "診断結果は保存されません" in text



# ---------------------------------------------------------------
# 製造業デモ（指示書 8章：月次生産実績集計。デモの初期表示）
# ---------------------------------------------------------------
def test_デモの初期表示は製造業で_業務フロー_試算_レビューまでAPIなしで試せる(secrets):
    at = new_app(secrets, scenario=None)
    assert at.selectbox(key="demo_scenario").value == "月次生産実績集計（製造部）"
    at = result_screen(secrets, at)
    assert at.session_state.business_info.department == "製造部 生産管理課"
    # 試算：デモ値（300分×4件＝月20時間）で、案2（データの取り込み）は転記の時間が大きく減る
    overview = next(df.value for df in at.dataframe if "改善案" in df.value.columns)
    assert overview.loc[1, "年間削減時間"] == "120時間"
    # 業務フロー → 試算への反映 → レビュー → 作り直し
    at.button(key="flow_generate").click().run()
    assert len([h for h in at.get("html") if "bicflow" in h.proto.body]) == 1
    at.selectbox(key="review_pid").set_value("P2").run()
    at.button(key="review_run_P2").click().run()
    assert "△ 条件付き推奨" in all_text(at)
    assert at.session_state.project.ai_calls == {}  # APIは呼んでいない



def test_結果画面の上部に進み具合と次にすることを表示する(secrets):
    at = result_screen(secrets)
    text = all_text(at)
    assert "STEP 6" in text and "改善案の確定" in text
    assert "次にすること　STEP 2（現状の業務フロー）" in text


def test_試算が古くなったら再計算が必要と表示し_確認すれば消える(secrets):
    from ai_bic import project as pj

    at = result_screen(secrets)
    pj.mark_updated(at.session_state.project, "proposal", "P1")
    at.run()
    assert any("再計算が必要" in w.value for w in at.warning)
    at.button(key="sim_ack_P1").click().run()
    assert not any(w.value.startswith("再計算が必要") for w in at.warning)


# ---------------------------------------------------------------
# 結果画面の冒頭と改善案の比較（AI-BIC 3.0 第1段階）
# ---------------------------------------------------------------
def production_result(secrets, finish_after: int | None = None) -> AppTest:
    """製造部デモの結果画面。finish_after を渡すと、その数だけ答えてヒアリングを途中で終える。"""
    at = new_app(secrets, scenario=None)
    at.button(key="start_demo").click().run()
    at.button(key="submit_info").click().run()
    answered = 0
    while at.session_state.stage == "hearing":
        if finish_after is not None and answered >= finish_after:
            at.button(key="finish").click().run()
            break
        at.button(key="submit_answer").click().run()
        answered += 1
    if at.session_state.stage == "readiness":
        at.button(key="extra_skip").click().run()
    at.button(key="diagnose").click().run()
    assert not at.exception
    return at


def test_重要な不足がなければ_主ボタンは改善案を比較する(secrets):
    at = production_result(secrets)
    assert at.button(key="go_compare").proto.type == "primary"
    assert "不足情報を確認する" not in [b.label for b in at.button if b.proto.type == "primary"]


def test_途中で終えて重要な不足があれば_主ボタンは不足情報を確認する(secrets):
    at = production_result(secrets, finish_after=1)
    assert at.button(key="go_gaps").proto.type == "primary"
    assert at.button(key="go_compare").proto.type == "secondary"
    assert "不足情報の確認" in all_text(at)


def test_比較で選んだ案は_業務フロー_試算_レビューに引き継がれる(secrets):
    at = production_result(secrets)
    assert "案1を先にやる → 案2を次に試す" in all_text(at)  # 製造部デモの案どうしの関係
    assert "案2と案3は併用できる" in all_text(at)
    at.radio(key="compare_plan").set_value("P2").run()
    for key in ("go_flows", "go_sim", "go_review"):
        at.button(key=key).click().run()
        assert not at.exception
        s = at.session_state
        assert (s.compare_plan, s.sim_plan, s.flow_pid, s.review_pid) == ("P2", "P2", "P2", "P2"), key
    assert at.radio(key="sim_plan").value == "P2"  # 試算の画面でも、選び直さずに案2が選ばれている
    assert at.selectbox(key="review_pid").value == "P2"


def test_業務フローを見るでは_選んだ案の改善後を現状と並べて表示する(secrets):
    at = production_result(secrets)
    at.button(key="flow_generate").click().run()
    at.radio(key="compare_plan").set_value("P3").run()
    at.button(key="go_flows").click().run()
    assert at.radio(key="flow_view").value == "左右に並べて比較"
    assert at.selectbox(key="flow_pid").value == "P3"


def test_6ステップの進み具合は_作業状況を見るの中にまとめる(secrets):
    at = production_result(secrets)
    assert "作業状況を見る" in [e.label for e in at.expander]
