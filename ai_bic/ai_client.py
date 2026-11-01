"""本物のAI（Claude API）の呼び出し（詳細設計書 3.3）。

hearing.AIClient と同じ3つの関数を持つ。処理の流れ：
1. prompt_builder でAIに渡す文章を作る
2. Claude API を構造化出力（messages.parse ＋ Pydantic）で呼ぶ
3. output_check で内容を確認し、違反があれば1回だけ呼び直す
4. 成功・失敗にかかわらず、利用状況を記録する（本文は記録しない）

SDKの例外や停止理由は、すべて errors.AIError（種類つき）に変換して上に伝える。
"""

from collections.abc import Callable

import anthropic
from pydantic import BaseModel, ValidationError

from ai_bic import config
from ai_bic.errors import AIError
from ai_bic.models import (
    AnalysisResult,
    BusinessInfo,
    Framework,
    HearingState,
    ProposalResult,
    QuestionResult,
)
from ai_bic.output_check import check_analysis, check_proposals, check_question_result
from ai_bic.prompt_builder import (
    build_analysis_input,
    build_proposal_input,
    build_question_input,
    load_system_prompt,
)
from ai_bic.usage_log import init_db, record_call


class ClaudeClient:
    """Claude API を使うAIクライアント。

    sdk_client はテストで偽物のSDKを渡すときに使う。通常は省略する。
    """

    def __init__(self, api_key: str, session_id: str, db_path: str, sdk_client=None):
        self.session_id = session_id
        self.db_path = db_path
        self.sdk = sdk_client or anthropic.Anthropic(
            api_key=api_key,
            max_retries=config.API_MAX_RETRIES,  # 利用制限・障害・通信エラーはSDKが自動で再試行
            timeout=config.API_TIMEOUT_SECONDS,
        )
        init_db(db_path)

    # -----------------------------------------------------------
    # 共通の呼び出し方（hearing.AIClient）
    # -----------------------------------------------------------
    def ask_next_question(
        self, info: BusinessInfo, state: HearingState, framework: Framework
    ) -> QuestionResult:
        return self._call_checked(
            call_type="question",
            model=config.MODEL_QUESTION,
            effort=config.EFFORT_QUESTION,
            max_tokens=config.MAX_TOKENS_QUESTION,
            system_name="question_system",
            user_input=build_question_input(info, state, framework),
            output_format=QuestionResult,
            check=lambda r: check_question_result(r, state, framework),
        )

    def analyze(
        self, info: BusinessInfo, state: HearingState, framework: Framework
    ) -> AnalysisResult:
        return self._call_checked(
            call_type="analysis",
            model=config.MODEL_ANALYSIS,
            effort=config.EFFORT_ANALYSIS,
            max_tokens=config.MAX_TOKENS_ANALYSIS,
            system_name="analysis_system",
            user_input=build_analysis_input(info, state, framework),
            output_format=AnalysisResult,
            check=lambda r: check_analysis(r, framework),
        )

    def propose(
        self, info: BusinessInfo, state: HearingState, analysis: AnalysisResult
    ) -> ProposalResult:
        return self._call_checked(
            call_type="proposal",
            model=config.MODEL_PROPOSAL,
            effort=config.EFFORT_PROPOSAL,
            max_tokens=config.MAX_TOKENS_PROPOSAL,
            system_name="proposal_system",
            user_input=build_proposal_input(info, state, analysis),
            output_format=ProposalResult,
            check=lambda r: check_proposals(r, analysis),
        )

    # -----------------------------------------------------------
    # 内部の処理
    # -----------------------------------------------------------
    def _call_checked(
        self,
        call_type: str,
        model: str,
        effort: str,
        max_tokens: int,
        system_name: str,
        user_input: str,
        output_format: type[BaseModel],
        check: Callable[[BaseModel], list[str]],
    ):
        """AIを呼び、内容チェックに失敗したら決められた回数だけ呼び直す。"""
        system = load_system_prompt(system_name)

        for _ in range(config.INVALID_OUTPUT_RETRY + 1):
            result, usage = self._call(call_type, model, effort, max_tokens, system, user_input, output_format)
            problems = check(result)
            self._record(call_type, model, usage, "invalid_output" if problems else None)
            if not problems:
                return result

        # 詳細には違反の件数だけを入れる（違反の内容には利用者の発言が含まれうるため）
        raise AIError("invalid_output", f"{call_type}: 内容チェックで{len(problems)}件の違反")

    def _call(self, call_type, model, effort, max_tokens, system, user_input, output_format):
        """Claude API を1回呼ぶ。成功したら (結果, 使用量) を返し、失敗なら AIError を発生させる。

        失敗した場合はここで利用状況を記録する。成功した場合の記録は、
        内容チェックの結果が分かってから _call_checked で行う。
        """
        try:
            response = self.sdk.messages.parse(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user_input}],
                output_format=output_format,
                output_config={"effort": effort},
            )
        except anthropic.APIError as e:
            kind = _error_kind(e)
            self._record(call_type, model, None, kind)
            raise AIError(kind, type(e).__name__) from e
        except (ValidationError, ValueError) as e:
            # 回答をデータの形に変換できなかった（構造化出力では通常起きない）
            self._record(call_type, model, None, "invalid_output")
            raise AIError("invalid_output", type(e).__name__) from e

        usage = response.usage
        if response.stop_reason == "refusal":
            self._record(call_type, model, usage, "refusal")
            raise AIError("refusal")
        if response.stop_reason == "max_tokens":
            self._record(call_type, model, usage, "truncated")
            raise AIError("truncated", f"max_tokens={max_tokens}")
        if response.parsed_output is None:
            self._record(call_type, model, usage, "invalid_output")
            raise AIError("invalid_output", "parsed_output is None")

        return response.parsed_output, usage

    def _record(self, call_type: str, model: str, usage, error_type: str | None) -> None:
        """利用状況を1行記録する。本文は渡さない。"""
        record_call(
            self.db_path,
            self.session_id,
            call_type,
            model,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            status="error" if error_type else "success",
            error_type=error_type,
        )


def _error_kind(e: anthropic.APIError) -> str:
    """SDKの例外を、エラーの種類（errors.py の kind）に変換する。"""
    # 時間切れも通信エラーとして扱う（APITimeoutError は APIConnectionError の一種）
    if isinstance(e, anthropic.APIConnectionError):
        return "connection"
    if isinstance(e, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return "auth"
    if isinstance(e, anthropic.RateLimitError):
        return "rate_limit"
    if isinstance(e, anthropic.APIStatusError) and e.status_code >= 500:
        return "server"
    return "bad_request"
