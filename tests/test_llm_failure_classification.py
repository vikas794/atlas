"""LLM failure classification (PRD sections 8 and 18) with the real LiteLLM exception types.

Every provider goes through LiteLLM, which maps provider errors onto OpenAI-style exception
classes. These tests pin how those map onto Atlas' failure taxonomy: a bad key must fail
permanently instead of burning the retry budget, a 429 must say which provider limited us.
No network: the completion call is a double that raises real ``litellm`` exception objects.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import litellm
import pytest

from src.domain.models.playlist import FailureKind, FailureType, NotesGenerationError
from src.infrastructure.llm.litellm_notes import LiteLLMNotesGenerator
from src.infrastructure.llm.notes_base import (
    NotesGeneratorConfigError,
    check_finish_reason,
    classify_provider_exception,
)
from src.infrastructure.llm.registry.providers import litellm_model_for
from src.infrastructure.llm.registry.repo import RegistryRepo
from src.infrastructure.llm.registry.service import ModelRegistry

GOOD_NOTES = (
    "## Notes\n\n- loops: for, while and do-while, each with a worked example.\n\n"
    "## Key Points\n\n- pick the loop that matches the iteration shape.\n\n"
    "## Takeaways\n\n- prefer for-each over index loops when possible.\n"
)
FAKE_KEY = "AIzaSyTESTKEY0000000000000000000000000000"


_MODELS = {
    "gemini": ("gemini-3.5-flash", 1_000_000),
    "openrouter": ("openai/gpt-5-mini", 128_000),
}


def _settings(**overrides: Any):
    values = {"notes_prompt_name": "notes_v1_1.yaml", "prompts_base_dir": "src/prompts", **overrides}
    return lambda key, default=None: values.get(key, default)


def _registry(provider: str) -> ModelRegistry:
    """A registry (in this test's throwaway database) with one model and one key for ``provider``."""
    registry = ModelRegistry(RegistryRepo(os.environ["ATLAS_DATABASE_PATH"]))
    model_id, limit = _MODELS[provider]
    registry.repo.add_model(
        provider=provider,
        model_id=model_id,
        display_name=model_id,
        litellm_model=litellm_model_for(provider, model_id),
        api_base=None,
        input_token_limit=limit,
    )
    registry.repo.add_key(provider=provider, label="test key", key_value=FAKE_KEY)
    return registry


def llm_error(cls: type[Exception], code: int, message: str, provider: str = "gemini") -> Exception:
    """A real ``litellm`` exception, as ``litellm.acompletion`` would raise it."""
    if cls is litellm.APIError:  # the generic class carries a status code instead of a response
        return cls(status_code=code, message=message, llm_provider=provider, model="m")
    response = httpx.Response(code, request=httpx.Request("POST", "https://provider.example"))
    return cls(message=message, model="m", llm_provider=provider, response=response)  # type: ignore[call-arg]


class Completion:
    """``litellm.acompletion`` double: raises ``error`` or answers with ``text``."""

    def __init__(
        self, *, error: Exception | None = None, text: str | None = GOOD_NOTES, finish: str = "stop"
    ) -> None:
        self.error = error
        self.text = text
        self.finish = finish
        self.calls = 0

    async def __call__(self, **params: Any) -> Any:
        self.calls += 1
        if self.error is not None:
            raise self.error
        message = type("Message", (), {"content": self.text})()
        choice = type("Choice", (), {"message": message, "finish_reason": self.finish})()
        return type("Response", (), {"choices": [choice], "usage": None})()


async def _run(completion: Completion, provider: str = "gemini") -> Any:
    generator = LiteLLMNotesGenerator(
        _settings(), completion=completion, registry=_registry(provider)
    )
    return await generator.generate_notes(title="Loops", transcript="short transcript")


async def _openrouter(completion: Completion) -> Any:
    return await _run(completion, provider="openrouter")


# ----------------------------------------------------------------- Gemini


async def test_gemini_invalid_key_is_a_permanent_config_error() -> None:
    error = llm_error(
        litellm.BadRequestError,
        400,
        f"API key not valid. Please pass a valid API key. {FAKE_KEY}",
    )
    with pytest.raises(NotesGeneratorConfigError) as excinfo:
        await _run(Completion(error=error))
    assert "Keys page" in str(excinfo.value)
    assert FAKE_KEY not in str(excinfo.value)
    assert FAKE_KEY not in excinfo.value.diagnostic  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("cls", "code"), [(litellm.AuthenticationError, 401), (litellm.PermissionDeniedError, 403)]
)
async def test_gemini_auth_statuses_are_config_errors(cls: type[Exception], code: int) -> None:
    with pytest.raises(NotesGeneratorConfigError):
        await _run(Completion(error=llm_error(cls, code, "denied")))


async def test_gemini_unknown_model_is_a_config_error() -> None:
    error = llm_error(litellm.NotFoundError, 404, "models/x is not found")
    with pytest.raises(NotesGeneratorConfigError, match="does not know the model"):
        await _run(Completion(error=error))


async def test_gemini_429_is_rate_limited_and_names_gemini_not_youtube() -> None:
    error = llm_error(litellm.RateLimitError, 429, "Resource has been exhausted (check quota).")
    with pytest.raises(NotesGenerationError) as excinfo:
        await _run(Completion(error=error))
    assert excinfo.value.failure_type is FailureType.RATE_LIMITED
    assert excinfo.value.failure_kind is FailureKind.TRANSIENT
    assert "Gemini" in excinfo.value.reason
    assert "YouTube" not in excinfo.value.reason


@pytest.mark.parametrize(
    ("cls", "code"),
    [
        (litellm.InternalServerError, 500),
        (litellm.ServiceUnavailableError, 503),
        (litellm.BadGatewayError, 502),
    ],
)
async def test_gemini_5xx_is_a_transient_provider_error(cls: type[Exception], code: int) -> None:
    with pytest.raises(NotesGenerationError) as excinfo:
        await _run(Completion(error=llm_error(cls, code, "backend error")))
    assert excinfo.value.failure_type is FailureType.PROVIDER_ERROR
    assert excinfo.value.failure_kind is FailureKind.TRANSIENT
    assert "transcript provider" not in excinfo.value.reason


async def test_a_raw_network_error_is_transient() -> None:
    error = httpx.ConnectError("[Errno 11001] getaddrinfo failed")
    with pytest.raises(NotesGenerationError) as excinfo:
        await _run(Completion(error=error))
    assert excinfo.value.failure_type is FailureType.NETWORK_ERROR
    assert excinfo.value.failure_kind is FailureKind.TRANSIENT


async def test_litellm_connection_error_is_a_network_error_despite_its_500_status() -> None:
    error = litellm.APIConnectionError(
        message="Connection refused",
        model="m",
        llm_provider="gemini",
        request=httpx.Request("POST", "https://provider.example"),
    )
    with pytest.raises(NotesGenerationError) as excinfo:
        await _run(Completion(error=error))
    assert excinfo.value.failure_type is FailureType.NETWORK_ERROR


async def test_litellm_timeout_is_a_network_error() -> None:
    error = litellm.Timeout(message="Request timed out", model="m", llm_provider="gemini")
    with pytest.raises(NotesGenerationError) as excinfo:
        await _run(Completion(error=error))
    assert excinfo.value.failure_type is FailureType.NETWORK_ERROR
    assert excinfo.value.failure_kind is FailureKind.TRANSIENT


async def test_gemini_input_over_the_model_limit_is_permanent_too_large() -> None:
    error = llm_error(
        litellm.BadRequestError,
        400,
        "The input token count (1200000) exceeds the maximum number of tokens allowed (1048576).",
    )
    with pytest.raises(NotesGenerationError) as excinfo:
        await _run(Completion(error=error))
    assert excinfo.value.failure_type is FailureType.TRANSCRIPT_TOO_LARGE
    assert excinfo.value.failure_kind is FailureKind.PERMANENT


async def test_litellm_context_window_error_is_permanent_too_large() -> None:
    error = llm_error(litellm.ContextWindowExceededError, 400, "prompt does not fit")
    with pytest.raises(NotesGenerationError) as excinfo:
        await _run(Completion(error=error))
    assert excinfo.value.failure_type is FailureType.TRANSCRIPT_TOO_LARGE


async def test_litellm_content_policy_violation_is_a_transient_block() -> None:
    error = llm_error(litellm.ContentPolicyViolationError, 400, "blocked by policy")
    with pytest.raises(NotesGenerationError) as excinfo:
        await _run(Completion(error=error))
    assert excinfo.value.failure_type is FailureType.OUTPUT_MALFORMED
    assert excinfo.value.failure_kind is FailureKind.TRANSIENT


async def test_output_cut_off_at_the_token_limit_is_rejected() -> None:
    """A truncated answer can be long enough to pass validation; it must not ship."""
    with pytest.raises(NotesGenerationError) as excinfo:
        await _run(Completion(finish="length"))
    assert excinfo.value.failure_type is FailureType.OUTPUT_MALFORMED
    assert "cut off" in excinfo.value.reason


async def test_a_safety_block_is_reported_as_a_block() -> None:
    with pytest.raises(NotesGenerationError) as excinfo:
        await _run(Completion(text=None, finish="content_filter"))
    assert excinfo.value.failure_type is FailureType.OUTPUT_MALFORMED
    assert excinfo.value.failure_kind is FailureKind.TRANSIENT
    assert "blocked" in excinfo.value.reason


async def test_normal_stop_succeeds() -> None:
    notes = await _run(Completion())
    assert notes.provider == "gemini"
    assert notes.content.startswith("## Notes")


async def test_the_call_carries_a_request_timeout() -> None:
    """The default is no timeout: one hung call would hold a worker slot forever."""
    seen: dict[str, Any] = {}

    async def completion(**params: Any) -> Any:
        seen.update(params)
        return await Completion()(**params)

    await _run(completion)  # type: ignore[arg-type]
    assert seen["timeout"] > 0


# ------------------------------------------------------------- OpenRouter


@pytest.mark.parametrize(
    ("cls", "code"),
    [(litellm.AuthenticationError, 401), (litellm.PermissionDeniedError, 403)],
)
async def test_openrouter_auth_errors_are_config_errors(cls: type[Exception], code: int) -> None:
    error = llm_error(cls, code, "No auth credentials found", "openrouter")
    with pytest.raises(NotesGeneratorConfigError, match="Keys page"):
        await _openrouter(Completion(error=error))


async def test_openrouter_out_of_credits_is_a_config_error() -> None:
    error = llm_error(litellm.APIError, 402, "Insufficient credits", "openrouter")
    with pytest.raises(NotesGeneratorConfigError, match="credits"):
        await _openrouter(Completion(error=error))


async def test_openrouter_429_is_rate_limited() -> None:
    error = llm_error(litellm.RateLimitError, 429, "Rate limit exceeded", "openrouter")
    with pytest.raises(NotesGenerationError) as excinfo:
        await _openrouter(Completion(error=error))
    assert excinfo.value.failure_type is FailureType.RATE_LIMITED
    assert "OpenRouter" in excinfo.value.reason


async def test_openrouter_context_length_is_permanent_too_large() -> None:
    error = llm_error(
        litellm.BadRequestError,
        400,
        "This endpoint's maximum context length is 128000 tokens.",
        "openrouter",
    )
    with pytest.raises(NotesGenerationError) as excinfo:
        await _openrouter(Completion(error=error))
    assert excinfo.value.failure_type is FailureType.TRANSCRIPT_TOO_LARGE


async def test_openrouter_length_finish_is_rejected() -> None:
    with pytest.raises(NotesGenerationError, match="cut off"):
        await _openrouter(Completion(finish="length"))


# ------------------------------------------------------------------ helpers


def test_classifier_never_echoes_provider_text_in_the_reason() -> None:
    error = classify_provider_exception(
        RuntimeError("weird provider text with transcript words"),
        provider_label="Gemini",
        api_key_env="registry:test key",
        model="m",
    )
    assert isinstance(error, NotesGenerationError)
    assert "transcript words" not in error.reason
    assert error.failure_type is FailureType.PROVIDER_ERROR


@pytest.mark.parametrize("finish", [None, "STOP", "stop", "FINISH_REASON_UNSPECIFIED"])
def test_normal_finish_reasons_pass(finish: Any) -> None:
    check_finish_reason(finish, provider_label="Gemini")


# ------------------------------------------------------------------ metering


async def test_usage_is_recorded_with_provider_tokens_and_priced() -> None:
    from src.infrastructure.llm.pricing import call_cost

    class Ledger:
        def __init__(self) -> None:
            self.records: list[Any] = []

        async def record_usage(self, record: Any) -> None:
            self.records.append(record)

    async def completion(**params: Any) -> Any:
        message = type("Message", (), {"content": GOOD_NOTES})()
        choice = type("Choice", (), {"message": message, "finish_reason": "stop"})()
        # LiteLLM already folds Gemini thinking tokens into completion_tokens
        # (200 answer + 800 thinking), so they are billed as output.
        usage = type("Usage", (), {"prompt_tokens": 1000, "completion_tokens": 1000})()
        return type("Response", (), {"choices": [choice], "usage": usage})()

    ledger = Ledger()
    generator = LiteLLMNotesGenerator(
        _settings(), usage_ledger=ledger, completion=completion, registry=_registry("gemini")
    )
    await generator.generate_notes(title="Loops", transcript="short transcript")

    (record,) = ledger.records
    assert record.output_tokens == 1000
    assert record.cost_usd > 0
    assert call_cost("gemini/gemini-2.5-flash", 10**6, 0) == 0.30
    assert call_cost("openrouter/openai/gpt-4o", 10**6, 0) == 2.50  # prefix stripped
    assert call_cost("openai/z-ai/glm-5.2", 10**6, 0) > 0  # reseller id priced by the model's name
    assert call_cost("openai/totally-unknown-model", 10**6, 0) == 0.0  # unpriced, never guessed


def test_a_placeholder_inside_the_title_is_not_substituted() -> None:
    from src.infrastructure.llm.notes_base import render_prompt

    rendered = render_prompt(
        "T={{Title}} X={{Transcript}}", title="Hack {{Transcript}}", transcript="BODY"
    )
    assert rendered == "T=Hack {{Transcript}} X=BODY"
    assert rendered.count("BODY") == 1


def test_request_timeout_comes_from_settings_with_a_safe_default() -> None:
    from src.infrastructure.llm.notes_base import (
        DEFAULT_REQUEST_TIMEOUT_SECONDS,
        request_timeout_seconds,
    )

    assert request_timeout_seconds(lambda key, default=None: 42) == 42.0
    assert request_timeout_seconds(lambda key, default=None: default) == (
        DEFAULT_REQUEST_TIMEOUT_SECONDS
    )
