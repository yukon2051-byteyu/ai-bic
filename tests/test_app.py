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


def open_tab(at: AppTest, name: str) -> AppTest:
    """結果画面のタブを開く（AI-BIC 3.2）。name：改善案を選ぶ・不足情報を確認・診断の根拠を見る・小さく試す"""
    at.session_state["result_tab"] = name
    at.run()
    assert not at.exception
    return at


def all_text(at) -> str:
    parts = [m.value for m in at.markdown] + [e.value for e in at.error] + [c.value for c in at.caption]
    parts += [x.value for x in at.warning] + [x.value for x in at.info] + [x.value for x in at.success]
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
    # 初期表示は「改善案を選ぶ」。根拠は「診断の根拠を見る」タブにある（AI-BIC 3.2）
    assert at.session_state.result_tab == "改善案を選ぶ"
    assert "根拠となる発言を入力内容の中に確認できなかった" not in all_text(at)  # 他のタブの詳細は下に続けて出さない
    text = all_text(open_tab(at, "診断の根拠を見る"))
    assert "集計作業が担当者1人に依存している" in text
    assert "根拠となる発言を入力内容の中に確認できなかった" in text  # I5 の格下げ表示
    # 添付ファイルからの根拠が、ファイル名つきで表示される（F09）
    assert "添付ファイル『集計差異の記録.csv』" in text
    assert "診断に使った資料" in text
    # 冒頭の3項目（最大の課題・最初にやること・次の判断に必要な情報）と、比較表
    text = all_text(open_tab(at, "改善案を選ぶ"))
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
    assert "添付ファイル『" in all_text(open_tab(at, "診断の根拠を見る"))  # どのシナリオも添付ファイルを根拠に使っている


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
    text = all_text(open_tab(at, "診断の根拠を見る"))
    assert "情報が不足しています。結果は参考としてご覧ください。" in text
    assert "改善に向けた制約条件（不十分）　足りないこと：予算の目安や、使ってよいツール・社内ルールの有無" in text
    # 情報が足りないまま分析したので、仮定条件も示す（AI-BIC 2.0 ヒアリングの改善 7）
    assert "仮定条件：業務上の問題点または改善の制約条件が未確認のため" in all_text(open_tab(at, "不足情報を確認"))


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

    finish = at.button(key="finish")
    assert finish.label == "ヒアリングを終了して資料添付へ"
    assert "資料の添付は任意です。未確認の項目は結果画面に表示します。" in [c.value for c in at.caption]
    finish.click().run()
    assert at.session_state.stage == "attach"
    at.button(key="diagnose").click().run()

    assert at.session_state.stage == "result"
    # デモの結果は全問回答が前提の固定の内容。途中で終えたことと、何と照合したかを明示する（AI-BIC 3.0）
    text = all_text(at)
    assert "全問回答した場合のサンプル結果" in text and "途中（1問回答）" in text
    assert any(e.label.startswith("今回の入力（途中終了時点：1問回答") for e in open_tab(at, "診断の根拠を見る").expander)


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
def work(at: AppTest, area: str) -> AppTest:
    """「改善案を選ぶ」タブの作業領域を開く（AI-BIC 3.2）。area：brief・flows・sim・review"""
    at.session_state["result_tab"] = "改善案を選ぶ"
    at.session_state["work_area"] = area
    at.run()
    assert not at.exception
    return at


def result_screen(secrets, at: AppTest | None = None, area: str | None = "sim") -> AppTest:
    """デモで結果画面まで進める。at を渡すと、その画面（トップ）から続けて進める。

    area：最後に開いておく作業領域（試算のテストが多いため、既定は試算。None なら初期表示のまま）。
    """
    at = at or new_app(secrets)
    at.button(key="start_demo").click().run()
    at.button(key="submit_info").click().run()
    while at.session_state.stage == "hearing":
        at.button(key="submit_answer").click().run()
    if at.session_state.stage == "readiness":
        at.button(key="extra_skip").click().run()
    at.button(key="diagnose").click().run()
    assert not at.exception
    return work(at, area) if area else at




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
    # 本文と同じ数値（ヒアリングで分かった件数・時間）は「ヒアリング」、それ以外はデモ値（AI-BIC 3.1）
    assert any("◎ ヒアリング" in t and "□ デモ値" in t for t in texts)
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
    assert "質問 1 / 最大5問（必要な情報がそろえば早く終わります）" in all_text(at)


# ---------------------------------------------------------------
# ヒアリング画面の構成（AI-BIC 3.1）
# ---------------------------------------------------------------
def hearing_screen(secrets, answers: int = 0) -> AppTest:
    at = new_app(secrets, scenario=None)
    at.button(key="start_demo").click().run()
    at.button(key="submit_info").click().run()
    for _ in range(answers):
        at.button(key="submit_answer").click().run()
    assert not at.exception
    return at


def test_現在の質問はカードにまとめ_回答例は1回だけ_ボタンは回答して次へと分からない(secrets):
    at = hearing_screen(secrets)
    question = at.session_state.hearing_state.turns[0]
    assert f'<div class="bic-question">{question.question}</div>' in all_text(at)
    area = at.text_area(key="answer_1")
    assert area.label == "回答" and not area.placeholder  # 入力欄の中に回答例を重ねない
    examples = [c.value for c in at.caption if c.value.startswith("回答例")]
    assert examples == [f"回答例　{question.answer_example}"]
    assert at.button(key="submit_answer").label == "回答して次へ"
    assert at.button(key="submit_unknown").label == "分からない"
    assert "これまでの回答" not in [e.label for e in at.expander]  # 履歴が0件なら出さない


def test_これまでの回答は件数つきで_最初は閉じて表示する(secrets):
    at = hearing_screen(secrets, answers=2)
    history = next(e for e in at.expander if e.label.startswith("これまでの回答"))
    assert history.label == "これまでの回答（2件）" and not history.proto.expanded
    assert at.text_area(key="answer_3")  # 3問目の回答欄
    assert at.session_state.scroll_count == 2  # 次の質問に進むたびに、質問カードの先頭へ画面を動かす


def test_同じ回答を2回送っても_1回分しか記録しない(secrets):
    at = hearing_screen(secrets, answers=1)
    turns = at.session_state.hearing_state.turns
    assert [t.answer is not None for t in turns] == [True, False]
    # 送ったあとは「次の質問を準備しています」の間ボタンを出さず、再表示後は次の質問のボタンになる
    at.button(key="submit_answer").click().run()
    assert sum(t.answer is not None for t in at.session_state.hearing_state.turns) == 2


def test_最後の追加確認では主ボタンが回答して資料添付へになる(secrets):
    at = repair_until_readiness(secrets)
    at.button(key="extra_start").click().run()
    state = at.session_state.hearing_state
    expected = "回答して資料添付へ" if len([t for t in state.turns if t.extra]) >= 3 else "回答して次へ"
    assert at.button(key="submit_answer").label == expected


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
    assert "追加確認 1 / 最大3問" in all_text(at)
    assert any("確認する理由" in c.value for c in at.caption)
    at.button(key="submit_answer").click().run()  # デモの回答（制約条件）
    # 必要な情報がそろったので、上限の3問を待たずに資料の添付へ進む
    assert at.session_state.stage == "attach"
    at.button(key="diagnose").click().run()
    assert "追加確認で、分析に必要な情報がそろいました" in all_text(open_tab(at, "診断の根拠を見る"))
    text = all_text(open_tab(at, "不足情報を確認"))
    assert "業務上の問題点または改善の制約条件が未確認" not in text  # 答えた項目の仮定条件は出さない
    # 残る仮定は、欠けている値と影響だけを具体的に示す（AI-BIC 3.2）
    assert "仮定条件：試算の前提：月の修理依頼の受付件数は、月の営業日数が不明のため未確定。" in text


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
    work(at, "flows").button(key="flow_generate").click().run()
    assert len([h for h in at.get("html") if "bicflow" in h.proto.body]) == 1
    work(at, "review").selectbox(key="review_pid").set_value("P2").run()
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
def production_result(secrets, finish_after: int | None = None, extra: str = "answer") -> AppTest:
    """製造部デモの結果画面。

    finish_after：その数だけ答えてヒアリングを途中で終える。
    extra：基本の4問のあとの追加確認（2問）を answer＝答える／skip＝このまま分析する／unknown＝「分からない」と答える。
    """
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
        if extra == "skip":
            at.button(key="extra_skip").click().run()
        else:
            at.button(key="extra_start").click().run()
            while at.session_state.stage == "hearing":
                at.button(key="submit_unknown" if extra == "unknown" else "submit_answer").click().run()
    at.button(key="diagnose").click().run()
    assert not at.exception
    return at


def test_追加確認まで答えて重要な不足がなければ_主ボタンは改善案を比較する(secrets):
    at = production_result(secrets)
    assert at.button(key="go_compare").proto.type == "primary"
    assert "不足情報を確認する" not in [b.label for b in at.button if b.proto.type == "primary"]


def test_追加確認をせずに重要な不足があれば_主ボタンは不足情報を確認する(secrets):
    at = production_result(secrets, extra="skip")
    assert at.button(key="go_gaps").proto.type == "primary"
    assert at.button(key="go_compare").proto.type == "secondary"
    text = all_text(open_tab(at, "不足情報を確認"))
    assert "不足情報の確認" in text and "データの取得（Power Query）" in text


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
        assert s.work_area == key[3:]  # 押した作業領域だけをその場に出す（AI-BIC 3.2）
        if key == "go_sim":
            assert at.radio(key="sim_plan").value == "P2"  # 試算の画面でも、選び直さずに案2が選ばれている
    assert at.selectbox(key="review_pid").value == "P2"
    at.button(key="back_to_compare").click().run()  # 比較表へ戻る
    assert at.session_state.work_area == "brief" and at.radio(key="compare_plan").value == "P2"


def test_業務フローを見るでは_選んだ案の改善後を現状と並べて表示する(secrets):
    at = production_result(secrets)
    at.radio(key="compare_plan").set_value("P3").run()
    at.button(key="go_flows").click().run()
    at.button(key="flow_generate").click().run()
    assert at.radio(key="flow_view").value == "左右に並べて比較"
    assert at.selectbox(key="flow_pid").value == "P3"


def test_6ステップの進み具合は_作業状況を見るの中にまとめる(secrets):
    at = production_result(secrets)
    assert "作業状況を見る" in [e.label for e in at.expander]


# ---------------------------------------------------------------
# ヒアリングとデモの整合（AI-BIC 3.0 第2段階）
# ---------------------------------------------------------------
def test_製造部デモの追加確認は_判断できることを添えて_回答済みの内容を聞き直さない(secrets):
    at = new_app(secrets, scenario=None)
    at.button(key="start_demo").click().run()
    at.button(key="submit_info").click().run()
    for _ in range(4):
        at.button(key="submit_answer").click().run()
    assert at.session_state.stage == "readiness"  # 使えるExcelの機能とCSVの形式が分からない
    at.button(key="extra_start").click().run()
    text = all_text(at)
    assert "Power Query" in text and "この回答で判断できること　案2（CSVの自動取り込み）を採れるか" in text
    at.button(key="submit_answer").click().run()
    assert "変換表を一度作れば使い続けられるか" in all_text(at)
    questions = [t.question for t in at.session_state.hearing_state.turns]
    assert len(questions) == len(set(questions)) == 6  # 基本4問＋追加2問（最大3問）。同じ質問はしない


def test_追加確認で分からないと答えた項目は_未確認の前提として結果に引き継ぐ(secrets):
    at = production_result(secrets, extra="unknown")
    text = all_text(open_tab(at, "不足情報を確認"))
    assert "「分からない」と回答されたため、未確認の前提として" in text
    assert at.button(key="go_gaps").proto.type == "primary"


def test_デモを途中で終えても_結果と引用の確認状態はサンプル回答の基準でそろう(secrets):
    """途中までの入力と照合すると、同じ引用が「確認済み」と「根拠不足」に分かれていた（AI-BIC 3.0）。"""
    at = production_result(secrets, finish_after=1)
    s = at.session_state
    text = all_text(open_tab(at, "診断の根拠を見る"))
    assert "全問回答した場合のサンプル結果" in text and "途中（1問回答）" in text
    assert s.diagnosis.downgraded == {}  # サンプル回答と照合するため、格下げは起きない
    assert "2問目のサンプル回答" in text  # 引用の出どころも、サンプル回答と明示する
    assert "2問目の回答" not in text
    # 途中までの入力は、判定に使わず別に持つ
    assert sum(t.answer is not None for t in s.project.partial_hearing.turns) == 1
    assert len(s.project.hearing.turns) == 6
    assert at.button(key="go_compare").proto.type == "primary"  # サンプルは全問回答のため、重要な不足はない


def test_全問回答のときは_サンプル結果の表示を出さない(secrets):
    at = production_result(secrets)
    assert "全問回答した場合のサンプル結果" not in all_text(at)
    assert at.session_state.project.partial_hearing is None


def test_途中で終えたデモの案件は_保存して再開してもサンプル結果の表示が残る(secrets):
    from ai_bic.project import load_project, to_json_bytes

    at = production_result(secrets, finish_after=2)
    restored = load_project(to_json_bytes(at.session_state.project))
    assert sum(t.answer is not None for t in restored.partial_hearing.turns) == 2


# ---------------------------------------------------------------
# 不足情報への回答と、関係する結果の更新（AI-BIC 3.0 第3段階）
# ---------------------------------------------------------------
def answer_gap(at: AppTest, key: str, text: str, source: str = "本人の申告") -> AppTest:
    open_tab(at, "不足情報を確認")
    fk = key.replace(":", "_")
    at.text_area(key=f"gap_answer_{fk}").input(text)
    at.radio(key=f"gap_source_{fk}").set_value(source)
    at.button(key=f"gap_submit_{fk}").click().run()
    assert not at.exception
    return at


def test_不足情報にその場で答えると_要確認から回答済みになり_重要な不足がなくなれば主ボタンが変わる(secrets):
    at = production_result(secrets, extra="skip")
    assert at.button(key="go_gaps").proto.type == "primary"
    answer_gap(at, "item:H6", "利用中のExcelでPower Queryが使えます（情報システム担当に確認）")
    s = at.session_state
    assert s.project.gap_answers["item:H6"].source == "本人の申告"
    assert "回答を保存しました（本人の申告）" in all_text(at)
    answer_gap(at, "item:H2", "形式は毎月同じです", source="添付資料")
    assert at.button(key="go_compare").proto.type == "primary"  # 重要な不足がなくなった
    assert "✓ 回答済み（添付資料）" in all_text(at)


def fix_value(at: AppTest, qid: str, value: float, source: str = "本人の申告") -> AppTest:
    open_tab(at, "診断の根拠を見る")
    at.selectbox(key="qfix_qid").set_value(qid)
    at.number_input(key="qfix_value").set_value(value)
    at.radio(key="qfix_source").set_value(source)
    at.button(key="qfix_submit").click().run()
    assert not at.exception
    return at


def test_月20時間と転記12時間から_割合60パーセントを式つきで表示し_本文と不足情報と試算で同じ値を使う(secrets):
    at = production_result(secrets)
    text = all_text(open_tab(at, "診断の根拠を見る"))
    assert "12時間 ÷ 20時間 × 100 ＝ 60%" in text and "20時間 × 60 ÷ 4台 ＝ 300分" in text
    assert "合計と内訳" not in text and "fact:numbers" not in at.session_state.project.gap_answers  # 4つの数値を機械的に求めない
    sim = at.session_state.project.simulations["P1"]
    assert sim.minutes_per_case == 300 and sim.source_of("minutes_per_case") == "ヒアリング"


def test_内訳が合計を超えると割合を出さず_入力の確認を求める(secrets):
    at = fix_value(production_result(secrets), "Q2", 25.0)  # 転記の時間を25時間に直す（合計20時間を超える）
    text = all_text(at)
    assert "内訳が合計を上回っています" in text and "÷ 20時間 × 100 ＝" not in text


def test_合計が0なら割合と1件あたりの時間を計算しない(secrets):
    at = fix_value(production_result(secrets), "Q1", 0.0)
    assert "分母が0" in all_text(at)


def test_確認した数値で試算は計算し直し_手入力した値は残して元の値が変わったと示す(secrets):
    at = production_result(secrets)
    at.radio(key="compare_plan").set_value("P2").run()
    at.button(key="go_sim").click().run()
    at.number_input(key="sim_P2_minutes_per_case").set_value(200.0).run()  # 案2だけ手入力
    fix_value(at, "Q1", 24.0)  # 月の集計時間を24時間に直す → 1台分は 24×60÷4＝360分
    open_tab(at, "改善案を選ぶ")
    assert at.session_state.sim_plan == "P2"  # タブを切り替えても、選んでいた案は残る
    sims = at.session_state.project.simulations
    assert sims["P1"].minutes_per_case == 360 and sims["P1"].source_of("minutes_per_case") == "本人の申告"
    assert sims["P2"].minutes_per_case == 200  # 手入力の値は残す
    assert "元の値が変わりました" in all_text(at)
    assert at.number_input(key="sim_P2_minutes_per_case").value == 200
    at.button(key="sim_restore_P2_minutes_per_case").click().run()
    assert at.session_state.project.simulations["P2"].minutes_per_case == 360
    assert at.number_input(key="sim_P2_minutes_per_case").value == 360


def test_回答で前提が変わると_対象案の過去のレビューは再レビューが必要と表示する(secrets):
    at = production_result(secrets, extra="skip")
    at.radio(key="compare_plan").set_value("P2").run()
    at.button(key="go_review").click().run()
    at.button(key="review_run_P2").click().run()
    answer_gap(at, "item:H6", "社外にデータを出せません")
    assert "前提が変わりました。再レビューが必要です" in all_text(open_tab(at, "改善案を選ぶ"))


def test_確定した案は_確定後に前提が変わると再確認が必要と表示し_確定時点の内容を残す(secrets):
    at = production_result(secrets, extra="skip")
    at.radio(key="compare_plan").set_value("P2").run()
    at.button(key="go_review").click().run()
    at.button(key="review_decide_P2").click().run()
    project = at.session_state.project
    assert project.decision.pid == "P2"
    answer_gap(at, "item:H6", "社外にデータを出せません")
    assert "確定したあとに前提が変わりました。再確認が必要です" in all_text(open_tab(at, "改善案を選ぶ"))
    at.button(key="review_redecide_P2").click().run()
    assert "再確認が必要です" not in all_text(at)


def test_回答を反映して改善案を見直せる_デモはAIを使わない(secrets):
    at = production_result(secrets, extra="skip")
    answer_gap(at, "item:H6", "社外にデータを出せません")
    at.selectbox(key="rethink_pid").set_value("P2").run()
    assert "影響範囲：見直すと、この案の改善後フロー・試算・レビューが「要更新」になります" in all_text(at)
    at.button(key="rethink_run").click().run()
    project = at.session_state.project
    assert len(project.proposal_history["P2"]) == 1  # 見直す前の改善案は残る
    assert "社外にデータを出せません" in " ".join(project.diagnosis.proposals.proposals[1].prerequisites)
    assert project.ai_calls == {}


# ---------------------------------------------------------------
# 結果画面の4つのタブ（AI-BIC 3.2）
# ---------------------------------------------------------------
def test_初期表示は改善案を選ぶタブで_他のタブの詳細は下に続けて出さない(secrets):
    at = production_result(secrets, extra="skip")
    assert at.session_state.result_tab == "改善案を選ぶ"
    text = all_text(at)
    assert 'id="sec-compare"' in text
    assert all(f'id="sec-{s}"' not in text for s in ("gaps", "evidence", "trial"))  # 他のタブの見出しは描かない


def test_上部のボタンは対応するタブを開き_その先頭へ移動する(secrets):
    at = production_result(secrets, extra="skip")
    before = at.session_state.scroll_count
    at.button(key="go_gaps").click().run()
    assert at.session_state.result_tab == "不足情報を確認"
    assert 'id="sec-gaps"' in all_text(at) and 'id="sec-compare"' not in all_text(at)
    at.button(key="go_compare").click().run()
    assert at.session_state.result_tab == "改善案を選ぶ" and 'id="sec-compare"' in all_text(at)
    assert at.session_state.scroll_count == before + 2  # どちらも画面を動かす（タブの並びを上へ）


def test_タブを切り替えても_送信前の回答と選んだ案は消えない(secrets):
    at = production_result(secrets, extra="skip")
    at.radio(key="compare_plan").set_value("P3").run()
    open_tab(at, "不足情報を確認")
    at.text_area(key="gap_answer_item_H6").input("書きかけの回答").run()
    open_tab(at, "診断の根拠を見る")
    open_tab(at, "改善案を選ぶ")
    assert at.radio(key="compare_plan").value == "P3"
    open_tab(at, "不足情報を確認")
    assert at.text_area(key="gap_answer_item_H6").value == "書きかけの回答"
    assert "item:H6" not in at.session_state.project.gap_answers  # 送信していないので、記録はしない


# ---------------------------------------------------------------
# 改善案を選ぶタブの作業領域（AI-BIC 3.2）
# ---------------------------------------------------------------
def test_最初は比較表と選んだ案の要点だけで_フロー_試算_レビューは下に並ばない(secrets):
    at = production_result(secrets)
    text = all_text(at)
    assert 'class="bic-brief"' in text
    for label in ("目的", "変更内容", "期待する効果（想定）", "着手前の条件", "主なリスク"):
        assert f"<dt>{label}</dt>" in text
    assert all(f'id="sec-{s}"' not in text for s in ("flows", "sim", "review"))
    assert "詳しい説明を読む（導入前後・最初の一歩・推奨理由など）" in [e.label for e in at.expander]


def test_作業領域を切り替えても_試算の値とレビューの採否は残る(secrets):
    at = production_result(secrets)
    at.radio(key="compare_plan").set_value("P2").run()
    at.button(key="go_sim").click().run()
    at.number_input(key="sim_P2_hourly_cost").set_value(4200.0).run()
    at.button(key="go_review").click().run()
    assert 'id="sec-sim"' not in all_text(at) and 'id="sec-review"' in all_text(at)
    at.button(key="review_run_P2").click().run()
    key = next(r.key for r in at.radio if r.key and r.key.startswith("review_decision_P2_"))
    at.radio(key=key).set_value("採用").run()
    at.button(key="go_sim").click().run()
    assert at.number_input(key="sim_P2_hourly_cost").value == 4200
    at.button(key="go_review").click().run()
    assert at.radio(key=key).value == "採用"
    assert "review_decide_P2" in [b.key for b in at.button]  # 確定は、レビューの判定・採否と同じ場所


# ---------------------------------------------------------------
# 「あとで確認する」（確認予定。AI-BIC 3.2）
# ---------------------------------------------------------------
def test_ヒアリングのあとで確認するボタンは_確認予定として記録し_同じ項目を聞き直さない(secrets):
    at = hearing_screen(secrets)
    assert at.button(key="submit_planned").label == "あとで確認する"
    at.text_area(key="answer_1").input("未集計なので、1か月記録して確かめます")
    at.button(key="submit_planned").click().run()
    state = at.session_state.hearing_state
    first = state.turns[0]
    assert first.kind == "確認予定" and first.answer == "未集計なので、1か月記録して確かめます"
    assert all(t.item_id != first.item_id for t in state.turns[1:])  # 同じ項目は聞き直さない


def test_不足情報のタブで確認予定にでき_試行の測定項目に追加できる(secrets):
    at = production_result(secrets, extra="skip")
    open_tab(at, "不足情報を確認")
    at.text_input(key="gap_plan_fact_hourly_cost").input("経理に確認する予定")
    at.button(key="gap_planbtn_fact_hourly_cost").click().run()
    project = at.session_state.project
    assert project.gap_answers["fact:hourly_cost"].kind == "確認予定"
    assert "◷ 確認予定" in all_text(at) and "確認予定：経理に確認する予定" in all_text(at)
    # 案を確定して試行計画を作ったあと、測定項目に追加する（重ねて追加しない）
    work(at, "review").button(key="review_decide_P1").click().run()
    at.button(key="review_trial_P1").click().run()
    open_tab(at, "不足情報を確認")
    at.button(key="gap_totrial_fact_hourly_cost").click().run()
    names = [m.name for m in at.session_state.project.trial.metrics]
    at.button(key="gap_totrial_fact_hourly_cost").click().run()
    assert [m.name for m in at.session_state.project.trial.metrics] == names
    assert "同じ測定項目が、試行計画にすでにあります" in all_text(at)
