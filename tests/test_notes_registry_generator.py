"""The notes generator driven by the model registry: fallback, key rotation, cooldowns, ledgers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.domain.exceptions import NotesGeneratorConfigError
from src.domain.models.playlist import FailureType, NotesGenerationError
from src.infrastructure.llm.factory import build_notes_generator
from src.infrastructure.llm.litellm_notes import LiteLLMNotesGenerator
from src.infrastructure.llm.registry.providers import litellm_model_for
from src.infrastructure.llm.registry.repo import RegistryRepo
from src.infrastructure.llm.registry.service import ModelRegistry

NOTES = "## Notes\n" + "A useful line of notes. " * 6 + "\n\n## Key Points\n- one\n- two\n"
KEY_A, KEY_B, KEY_C = "key-aaaaaaaaaaaa-1", "key-bbbbbbbbbbbb-2", "key-cccccccccccc-3"


class _ApiError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


class _Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def registry(temp_db: Path, clock: _Clock) -> ModelRegistry:
    return ModelRegistry(RegistryRepo(temp_db), clock=clock)


def _add_model(registry: ModelRegistry, provider: str, model_id: str, **extra: Any) -> Any:
    return registry.repo.add_model(
        provider=provider,
        model_id=model_id,
        display_name=model_id,
        litellm_model=litellm_model_for(provider, model_id),
        api_base=extra.pop("api_base", None),
        input_token_limit=extra.pop("input_token_limit", 128_000),
        **extra,
    )


def _add_key(registry: ModelRegistry, provider: str, value: str, label: str | None = None) -> Any:
    return registry.repo.add_key(provider=provider, label=label or value[:8], key_value=value)


def _generator(
    registry: ModelRegistry,
    errors: dict[tuple[str, str], Exception] | None = None,
    ledger: Any = None,
) -> tuple[LiteLLMNotesGenerator, list[tuple[str, str]]]:
    """A registry-driven generator whose provider call fails per (model, key)."""
    calls: list[tuple[str, str]] = []
    errors = errors or {}

    async def completion(*, model: str, api_key: str, **_: Any) -> Any:
        calls.append((model, api_key))
        if (model, api_key) in errors:
            raise errors[(model, api_key)]
        message = SimpleNamespace(content=NOTES)
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=usage
        )

    from src.config.settings import AtlasSettings

    # A single-call prompt file: these tests are about fallback and keys, not prompt parts.
    loader = AtlasSettings(notes_prompt_name="notes_v1_2.yaml").to_loader()
    return (
        LiteLLMNotesGenerator(
            loader, completion=completion, registry=registry, usage_ledger=ledger
        ),
        calls,
    )


async def test_the_first_chain_model_serves_the_call_and_is_recorded(
    registry: ModelRegistry,
) -> None:
    model = _add_model(registry, "gemini", "gemini-a")
    _add_model(registry, "openrouter", "or-b")
    key = _add_key(registry, "gemini", KEY_A)
    generator, calls = _generator(registry)

    notes = await generator.generate_notes(title="T", transcript="hello")

    assert (notes.provider, notes.model) == ("gemini", "gemini-a")
    assert calls == [("gemini/gemini-a", KEY_A)]
    refreshed = registry.repo.get_key(key.id)
    assert refreshed is not None and refreshed.status == "healthy"
    assert registry.repo.window_usage(key.id, str(model.id), 0.0) == (1, 15)
    assert registry.token_usage()["total_tokens"] == 15


async def test_chain_order_follows_priority(registry: ModelRegistry) -> None:
    first = _add_model(registry, "gemini", "gemini-a")
    second = _add_model(registry, "openrouter", "or-b")
    _add_key(registry, "gemini", KEY_A)
    _add_key(registry, "openrouter", KEY_B)
    registry.repo.replace_chain([(second.id, 1, True), (first.id, 2, True)])
    generator, calls = _generator(registry)

    notes = await generator.generate_notes(title="T", transcript="hello")

    assert notes.provider == "openrouter"
    assert [c[0] for c in calls] == ["openrouter/or-b"]
    assert generator.provider == "openrouter"


async def test_a_disabled_model_is_skipped(registry: ModelRegistry) -> None:
    first = _add_model(registry, "gemini", "gemini-a")
    _add_model(registry, "openrouter", "or-b")
    _add_key(registry, "gemini", KEY_A)
    _add_key(registry, "openrouter", KEY_B)
    registry.repo.update_model(first.id, {"enabled": False})
    generator, calls = _generator(registry)

    notes = await generator.generate_notes(title="T", transcript="hello")

    assert notes.provider == "openrouter"
    assert [c[0] for c in calls] == ["openrouter/or-b"]


async def test_a_rate_limited_key_fails_over_and_is_cooled_down(
    registry: ModelRegistry, clock: _Clock
) -> None:
    model = _add_model(registry, "gemini", "gemini-a")
    a = _add_key(registry, "gemini", KEY_A)
    _add_key(registry, "gemini", KEY_B)
    generator, calls = _generator(
        registry, {("gemini/gemini-a", KEY_A): _ApiError(429, "RESOURCE_EXHAUSTED")}
    )

    await generator.generate_notes(title="T", transcript="hello")
    assert calls == [("gemini/gemini-a", KEY_A), ("gemini/gemini-a", KEY_B)]
    cooldown = registry.repo.get_cooldown(a.id, str(model.id))
    assert cooldown is not None and cooldown[0] == clock.now + 120.0

    calls.clear()
    await generator.generate_notes(title="T", transcript="hello")
    assert calls == [("gemini/gemini-a", KEY_B)]  # A is cooling down

    clock.now += 121.0
    keys, blocked = registry.usable_keys(model)
    assert blocked is None and {k.id for k in keys} == {a.id, registry.repo.list_keys()[1].id}


async def test_a_rejected_key_becomes_invalid_and_the_next_key_serves(
    registry: ModelRegistry,
) -> None:
    _add_model(registry, "gemini", "gemini-a")
    a = _add_key(registry, "gemini", KEY_A)
    _add_key(registry, "gemini", KEY_B)
    generator, calls = _generator(
        registry, {("gemini/gemini-a", KEY_A): _ApiError(400, "API key not valid")}
    )

    await generator.generate_notes(title="T", transcript="hello")

    assert calls == [("gemini/gemini-a", KEY_A), ("gemini/gemini-a", KEY_B)]
    refreshed = registry.repo.get_key(a.id)
    assert refreshed is not None
    assert refreshed.status == "invalid" and refreshed.consecutive_failures == 1


async def test_a_key_is_disabled_after_three_rejections(registry: ModelRegistry) -> None:
    model = _add_model(registry, "gemini", "gemini-a")
    a = _add_key(registry, "gemini", KEY_A)
    for _ in range(3):
        registry.record_failure(registry.repo.get_key(a.id), model, "rejected")  # type: ignore[arg-type]
    refreshed = registry.repo.get_key(a.id)
    assert refreshed is not None and not refreshed.enabled


async def test_every_key_rate_limited_surfaces_a_transient_rate_limit(
    registry: ModelRegistry,
) -> None:
    _add_model(registry, "gemini", "gemini-a")
    _add_key(registry, "gemini", KEY_A)
    _add_key(registry, "gemini", KEY_B)
    err = _ApiError(429, "RESOURCE_EXHAUSTED")
    generator, calls = _generator(
        registry, {("gemini/gemini-a", KEY_A): err, ("gemini/gemini-a", KEY_B): err}
    )

    with pytest.raises(NotesGenerationError) as excinfo:
        await generator.generate_notes(title="T", transcript="hello")

    assert excinfo.value.failure_type is FailureType.RATE_LIMITED
    assert len(calls) == 2

    # Everything is cooling down now: no further provider call, still a transient rate limit.
    calls.clear()
    with pytest.raises(NotesGenerationError) as again:
        await generator.generate_notes(title="T", transcript="hello")
    assert again.value.failure_type is FailureType.RATE_LIMITED
    assert calls == []


async def test_a_server_error_does_not_rotate_keys_but_falls_back_to_the_next_model(
    registry: ModelRegistry,
) -> None:
    _add_model(registry, "gemini", "gemini-a")
    _add_model(registry, "openrouter", "or-b")
    _add_key(registry, "gemini", KEY_A)
    _add_key(registry, "gemini", KEY_B)
    _add_key(registry, "openrouter", KEY_C)
    generator, calls = _generator(
        registry, {("gemini/gemini-a", KEY_A): _ApiError(503, "unavailable")}
    )

    notes = await generator.generate_notes(title="T", transcript="hello")

    assert notes.provider == "openrouter"
    assert calls == [("gemini/gemini-a", KEY_A), ("openrouter/or-b", KEY_C)]


async def test_a_model_without_a_usable_key_falls_back_to_the_next(
    registry: ModelRegistry,
) -> None:
    _add_model(registry, "gemini", "gemini-a")
    _add_model(registry, "openrouter", "or-b")
    _add_key(registry, "openrouter", KEY_B)  # no Gemini key at all
    generator, calls = _generator(registry)

    notes = await generator.generate_notes(title="T", transcript="hello")

    assert notes.provider == "openrouter"
    assert calls == [("openrouter/or-b", KEY_B)]


async def test_no_usable_key_anywhere_is_a_config_error_naming_the_keys_page(
    registry: ModelRegistry,
) -> None:
    _add_model(registry, "gemini", "gemini-a")
    generator, calls = _generator(registry)

    with pytest.raises(NotesGeneratorConfigError, match="Keys page"):
        await generator.generate_notes(title="T", transcript="hello")
    assert calls == []


async def test_an_empty_chain_is_a_config_error_naming_the_models_page(
    registry: ModelRegistry,
) -> None:
    generator, _ = _generator(registry)

    with pytest.raises(NotesGeneratorConfigError, match="Models page"):
        await generator.generate_notes(title="T", transcript="hello")
    assert generator.provider == "" and generator.model == ""


async def test_the_factory_never_fails_at_build_time_with_an_empty_registry(
    registry: ModelRegistry,
) -> None:
    from src.config import load_settings

    generator = build_notes_generator(load_settings().to_loader(), registry=registry)

    assert isinstance(generator, LiteLLMNotesGenerator)


async def test_a_rejected_key_message_never_contains_the_key(registry: ModelRegistry) -> None:
    _add_model(registry, "gemini", "gemini-a")
    _add_key(registry, "gemini", KEY_A, label="My key")
    generator, _ = _generator(
        registry,
        {("gemini/gemini-a", KEY_A): _ApiError(401, f"Unauthorized: bad key {KEY_A}")},
    )

    with pytest.raises(NotesGeneratorConfigError) as excinfo:
        await generator.generate_notes(title="T", transcript="hello")

    message = str(excinfo.value) + str(getattr(excinfo.value, "diagnostic", ""))
    assert KEY_A not in message
    assert "My key" in str(excinfo.value) and "Keys page" in str(excinfo.value)


async def test_a_transcript_over_the_model_limit_is_refused_before_any_call(
    registry: ModelRegistry,
) -> None:
    _add_model(registry, "gemini", "gemini-a", input_token_limit=10)
    _add_key(registry, "gemini", KEY_A)
    generator, calls = _generator(registry)

    with pytest.raises(NotesGenerationError) as excinfo:
        await generator.generate_notes(title="T", transcript="x" * 1000)

    assert excinfo.value.failure_type is FailureType.TRANSCRIPT_TOO_LARGE
    assert calls == []


async def test_the_usage_ledger_gets_a_row_for_the_model_that_served(
    registry: ModelRegistry,
) -> None:
    _add_model(registry, "openrouter", "or-b")
    _add_key(registry, "openrouter", KEY_B)
    rows: list[Any] = []

    class Ledger:
        async def record_usage(self, record: Any) -> None:
            rows.append(record)

    generator, _ = _generator(registry, ledger=Ledger())

    await generator.generate_notes(title="T", transcript="hello")

    assert [(r.provider, r.model, r.total_tokens) for r in rows] == [("openrouter", "or-b", 15)]


def _part_generator(registry: ModelRegistry, fail_on: str | None = None) -> tuple[Any, list[str]]:
    """A generator on the shipped three-part prompt; the fake answers per part."""
    from src.config import load_settings

    prompts: list[str] = []
    answers = {
        "create the Summary and the Questions": "Summary\nA short overview of the video for the index.\n\n"
        "Questions and Answers\nBasic\n1. What is it?\n   *Answer:* A thing.\n",
        "create detailed notes": "Notes\n" + "A useful line of notes. " * 4,
        "CUSTOM": "Notes\n" + "A useful line of notes. " * 4 + "\n\nHistory\n- 2010: the first release.",
        "write the history of the topic": "History\n- 2010: the first release of the topic arrived.",
    }

    async def completion(*, messages: list[dict[str, str]], **_: Any) -> Any:
        prompt = messages[0]["content"]
        prompts.append(prompt)
        if fail_on and fail_on in prompt:
            raise _ApiError(500, "boom")
        text = next(v for k, v in answers.items() if k in prompt)
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
        choice = SimpleNamespace(message=SimpleNamespace(content=text), finish_reason="stop")
        return SimpleNamespace(choices=[choice], usage=usage)

    generator = LiteLLMNotesGenerator(
        load_settings().to_loader(), completion=completion, registry=registry
    )
    return generator, prompts


async def test_the_default_prompt_makes_three_calls_and_joins_them_in_order(
    registry: ModelRegistry,
) -> None:
    _add_model(registry, "gemini", "gemini-a")
    _add_key(registry, "gemini", KEY_A)
    generator, prompts = _part_generator(registry)

    notes = await generator.generate_notes(title="T", transcript="hello")

    assert len(prompts) == 3
    content = notes.content
    positions = [content.index(h) for h in ("Summary", "Questions and Answers", "Notes", "History")]
    assert positions == sorted(positions)
    assert notes.prompt_version == "notes_v1.3"
    assert registry.token_usage()["total_tokens"] == 45


async def test_a_failed_part_fails_the_whole_generation(registry: ModelRegistry) -> None:
    _add_model(registry, "gemini", "gemini-a")
    _add_key(registry, "gemini", KEY_A)
    generator, prompts = _part_generator(registry, fail_on="write the history")

    with pytest.raises(NotesGenerationError):
        await generator.generate_notes(title="T", transcript="hello")

    assert len(prompts) == 3


async def test_a_custom_prompt_stays_a_single_call(registry: ModelRegistry) -> None:
    _add_model(registry, "gemini", "gemini-a")
    _add_key(registry, "gemini", KEY_A)
    generator, prompts = _part_generator(registry)
    custom = "CUSTOM {{Title}}: {{Transcript}}"

    notes = await generator.generate_notes(title="T", transcript="x", prompt_template=custom)

    assert len(prompts) == 1
    assert notes.prompt_version == "custom"
