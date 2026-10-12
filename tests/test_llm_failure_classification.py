"""LLM failure classification (PRD sections 8 and 18) with the real SDK exception types.

The adapters used to map *every* SDK exception to ``rate_limited`` or ``output_malformed``
(both transient), so an invalid Gemini key burned the whole retry budget and a Gemini 429
was shown as "YouTube is rate-limiting requests". These tests pin the corrected taxonomy.
No network: the SDK client is a double that raises real ``google.genai`` / ``openai``
exception objects.
"""

from __future__ import annotations

from typing import Any

import httpx
import openai
import pytest
from google.genai import errors as genai_errors

from src.domain.models.playlist import FailureKind, FailureType, NotesGenerationError
from src.infrastructure.llm.gemini.notes import GeminiNotesGenerator
from src.infrastructure.llm.notes_base import (
    NotesGeneratorConfigError,
    check_finish_reason,
    classify_provider_exception,
)
from src.infrastructure.llm.openai.notes import OpenRouterNotesGenerator

GOOD_NOTES = (
    "## Notes\n\n- loops: for, while and do-while, each with a worked example.\n\n"
    "## Key Points\n\n- pick the loop that matches the iteration shape.\n\n"
    "## Takeaways\n\n- prefer for-each over index loops when possible.\n"
)
FAKE_KEY = "AIzaSyTESTKEY0000000000000000000000000000"


def _settings(**overrides: Any):
    values = {
        "notes_model": "",
        "gemini_model": "gemini-2.5-flash",
        "gemini_input_token_limit": 1_000_000,
        "notes_prompt_name": "notes_v1_1.yaml",
        "gemini_api_key_env": "GEMINI_API_KEY",
        "openrouter_api_key_env": "OPENROUTER_API_KEY",
        "prompts_base_dir": "src/prompts",
        **overrides,
    }
    return lambda key, default=None: values.get(key, default)


def genai_error(code: int, message: str, status: str) -> genai_errors.APIError:
    body = {"error": {"code": code, "message": message, "status": status}}
    cls = genai_errors.ServerError if code >= 500 else genai_errors.ClientError
    return cls(code, body)


def openai_error(cls: type[openai.APIStatusError], code: int, message: str) -> Exception:
    response = httpx.Response(code, request=httpx.Request("POST", "https://openrouter.ai/api/v1"))
    return cls(message, response=response, body=None)


class _Response:
    def __init__(self, text: str | None, finish: Any = "STOP") -> None:
        self.text = text
        self.usage_metadata = None
        self.candidates = [type("Candidate", (), {"finish_reason": finish})()]
        self.prompt_feedback = None


class GeminiDouble:
    def __init__(self, *, error: Exception | None = None, response: Any = None) -> None:
        self.error = error
        self.response = response if response is not None else _Response(GOOD_NOTES)
        self.calls = 0
        outer = self

        class _Models:
            async def generate_content(self, *, model: str, contents: str) -> Any:
                outer.calls += 1
                if outer.error is not None:
                    raise outer.error
                return outer.response

        self.aio = type("Aio", (), {"models": _Models()})()


class OpenAIDouble:
    def __init__(self, *, error: Exception | None = None, finish: str = "stop") -> None:
        self.error = error
        self.finish = finish
        outer = self

        class _Completions:
            async def create(self, *, model: str, messages: list[dict[str, str]]) -> Any:
                if outer.error is not None:
                    raise outer.error
                message = type("Message", (), {"content": GOOD_NOTES})()
                choice = type("Choice", (), {"message": message, "finish_reason": outer.finish})()
                return type("Response", (), {"choices": [choice], "usage": None})()

        self.chat = type("Chat", (), {"completions": _Completions()})()


async def _gemini(client: GeminiDouble) -> Any:
    generator = GeminiNotesGenerator(_settings(), client_factory=lambda: client)
    return await generator.generate_notes(title="Loops", transcript="short transcript")


async def _openrouter(client: OpenAIDouble) -> Any:
    generator = OpenRouterNotesGenerator(_settings(), client_factory=lambda: client)
    return await generator.generate_notes(title="Loops", transcript="short transcript")


# ----------------------------------------------------------------- Gemini


async def test_gemini_invalid_key_is_a_permanent_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    error = genai_error(
        400, f"API key not valid. Please pass a valid API key. {FAKE_KEY}", "INVALID_ARGUMENT"
    )
    with pytest.raises(NotesGeneratorConfigError) as excinfo:
        await _gemini(GeminiDouble(error=error))
    assert "GEMINI_API_KEY" in str(excinfo.value)
    assert FAKE_KEY not in str(excinfo.value)
    assert FAKE_KEY not in excinfo.value.diagnostic  # type: ignore[attr-defined]


@pytest.mark.parametrize(("code", "status"), [(401, "UNAUTHENTICATED"), (403, "PERMISSION_DENIED")])
async def test_gemini_auth_statuses_are_config_errors(code: int, status: str) -> None:
    with pytest.raises(NotesGeneratorConfigError):
        await _gemini(GeminiDouble(error=genai_error(code, "denied", status)))


async def test_gemini_unknown_model_is_a_config_error() -> None:
    with pytest.raises(NotesGeneratorConfigError, match="does not know the model"):
        await _gemini(GeminiDouble(error=genai_error(404, "models/x is not found", "NOT_FOUND")))


async def test_gemini_429_is_rate_limited_and_names_gemini_not_youtube() -> None:
    error = genai_error(
        429, "Resource has been exhausted (e.g. check quota).", "RESOURCE_EXHAUSTED"
    )
    with pytest.raises(NotesGenerationError) as excinfo:
        await _gemini(GeminiDouble(error=error))
    assert excinfo.value.failure_type is FailureType.RATE_LIMITED
    assert excinfo.value.failure_kind is FailureKind.TRANSIENT
    assert "Gemini" in excinfo.value.reason
    assert "YouTube" not in excinfo.value.reason


@pytest.mark.parametrize("code", [500, 503, 504])
async def test_gemini_5xx_is_a_transient_provider_error(code: int) -> None:
    with pytest.raises(NotesGenerationError) as excinfo:
        await _gemini(GeminiDouble(error=genai_error(code, "backend error", "UNAVAILABLE")))
    assert excinfo.value.failure_type is FailureType.PROVIDER_ERROR
    assert excinfo.value.failure_kind is FailureKind.TRANSIENT
    assert "transcript provider" not in excinfo.value.reason


async def test_gemini_network_error_is_transient() -> None:
    error = httpx.ConnectError("[Errno 11001] getaddrinfo failed")
    with pytest.raises(NotesGenerationError) as excinfo:
        await _gemini(GeminiDouble(error=error))
    assert excinfo.value.failure_type is FailureType.NETWORK_ERROR
    assert excinfo.value.failure_kind is FailureKind.TRANSIENT


async def test_gemini_input_over_the_model_limit_is_permanent_too_large() -> None:
    error = genai_error(
        400,
        "The input token count (1200000) exceeds the maximum number of tokens allowed (1048576).",
        "INVALID_ARGUMENT",
    )
    with pytest.raises(NotesGenerationError) as excinfo:
        await _gemini(GeminiDouble(error=error))
    assert excinfo.value.failure_type is FailureType.TRANSCRIPT_TOO_LARGE
    assert excinfo.value.failure_kind is FailureKind.PERMANENT


async def test_gemini_output_cut_off_at_max_tokens_is_rejected() -> None:
    """A truncated answer can be long enough to pass validation; it must not ship."""
    client = GeminiDouble(response=_Response(GOOD_NOTES, finish="MAX_TOKENS"))
    with pytest.raises(NotesGenerationError) as excinfo:
        await _gemini(client)
    assert excinfo.value.failure_type is FailureType.OUTPUT_MALFORMED
    assert "cut off" in excinfo.value.reason


async def test_gemini_safety_block_is_reported_as_a_block() -> None:
    client = GeminiDouble(response=_Response(None, finish="SAFETY"))
    with pytest.raises(NotesGenerationError) as excinfo:
        await _gemini(client)
    assert excinfo.value.failure_type is FailureType.OUTPUT_MALFORMED
    assert excinfo.value.failure_kind is FailureKind.TRANSIENT
    assert "blocked" in excinfo.value.reason


async def test_gemini_prompt_block_without_candidates_is_reported_as_a_block() -> None:
    response = _Response(None)
    response.candidates = []
    response.prompt_feedback = type("Feedback", (), {"block_reason": "PROHIBITED_CONTENT"})()
    with pytest.raises(NotesGenerationError, match="blocked"):
        await _gemini(GeminiDouble(response=response))


async def test_gemini_normal_stop_succeeds() -> None:
    notes = await _gemini(GeminiDouble())
    assert notes.provider == "gemini"
    assert notes.content.startswith("## Notes")


def test_gemini_client_is_built_with_a_request_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """The SDK default is no timeout: one hung call would hold a worker slot forever."""
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    client = GeminiNotesGenerator(_settings())._client_or_default()
    assert client._api_client._http_options.timeout and client._api_client._http_options.timeout > 0


# ------------------------------------------------------------- OpenRouter


@pytest.mark.parametrize(
    ("cls", "code"),
    [(openai.AuthenticationError, 401), (openai.PermissionDeniedError, 403)],
)
async def test_openrouter_auth_errors_are_config_errors(cls: Any, code: int) -> None:
    with pytest.raises(NotesGeneratorConfigError, match="OPENROUTER_API_KEY"):
        await _openrouter(OpenAIDouble(error=openai_error(cls, code, "No auth credentials found")))


async def test_openrouter_out_of_credits_is_a_config_error() -> None:
    error = openai_error(openai.APIStatusError, 402, "Insufficient credits")
    with pytest.raises(NotesGeneratorConfigError, match="credits"):
        await _openrouter(OpenAIDouble(error=error))


async def test_openrouter_429_is_rate_limited() -> None:
    error = openai_error(openai.RateLimitError, 429, "Rate limit exceeded")
    with pytest.raises(NotesGenerationError) as excinfo:
        await _openrouter(OpenAIDouble(error=error))
    assert excinfo.value.failure_type is FailureType.RATE_LIMITED
    assert "OpenRouter" in excinfo.value.reason


async def test_openrouter_connection_error_is_a_network_error() -> None:
    error = openai.APIConnectionError(request=httpx.Request("POST", "https://openrouter.ai"))
    with pytest.raises(NotesGenerationError) as excinfo:
        await _openrouter(OpenAIDouble(error=error))
    assert excinfo.value.failure_type is FailureType.NETWORK_ERROR


async def test_openrouter_context_length_is_permanent_too_large() -> None:
    error = openai_error(
        openai.BadRequestError, 400, "This endpoint's maximum context length is 128000 tokens."
    )
    with pytest.raises(NotesGenerationError) as excinfo:
        await _openrouter(OpenAIDouble(error=error))
    assert excinfo.value.failure_type is FailureType.TRANSCRIPT_TOO_LARGE


async def test_openrouter_length_finish_is_rejected() -> None:
    with pytest.raises(NotesGenerationError, match="cut off"):
        await _openrouter(OpenAIDouble(finish="length"))


# ------------------------------------------------------------------ helpers


def test_classifier_never_echoes_provider_text_in_the_reason() -> None:
    error = classify_provider_exception(
        RuntimeError("weird provider text with transcript words"),
        provider_label="Gemini",
        api_key_env="GEMINI_API_KEY",
        model="m",
    )
    assert isinstance(error, NotesGenerationError)
    assert "transcript words" not in error.reason
    assert error.failure_type is FailureType.PROVIDER_ERROR


@pytest.mark.parametrize("finish", [None, "STOP", "stop", "FINISH_REASON_UNSPECIFIED"])
def test_normal_finish_reasons_pass(finish: Any) -> None:
    check_finish_reason(finish, provider_label="Gemini")


# ------------------------------------------------------------------ metering


async def test_gemini_usage_counts_thinking_tokens_and_prices_the_default_model() -> None:
    from src.infrastructure.llm.cost import CostCalculator

    class Ledger:
        def __init__(self) -> None:
            self.records: list[Any] = []

        async def record_usage(self, record: Any) -> None:
            self.records.append(record)

    response = _Response(GOOD_NOTES)
    response.usage_metadata = type(
        "Usage",
        (),
        {"prompt_token_count": 1000, "candidates_token_count": 200, "thoughts_token_count": 800},
    )()
    ledger = Ledger()
    generator = GeminiNotesGenerator(
        _settings(), usage_ledger=ledger, client_factory=lambda: GeminiDouble(response=response)
    )
    await generator.generate_notes(title="Loops", transcript="short transcript")

    (record,) = ledger.records
    assert record.output_tokens == 1000  # 200 answer + 800 thinking, both billed as output
    assert record.cost_usd > 0
    assert CostCalculator.calculate_cost("gemini", "gemini-2.5-flash-lite", 10**6, 0) == 0.10


def test_a_placeholder_inside_the_title_is_not_substituted() -> None:
    from src.infrastructure.llm.notes_base import render_prompt

    rendered = render_prompt(
        "T={{Title}} X={{Transcript}}", title="Hack {{Transcript}}", transcript="BODY"
    )
    assert rendered == "T=Hack {{Transcript}} X=BODY"
    assert rendered.count("BODY") == 1
