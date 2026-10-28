"""Tests for the notes generator: port, guardrails, the registry-driven LiteLLM adapter and factory.

No test in this file touches the network or needs a real key. The provider boundary is exactly
one seam -- the ``completion`` constructor argument on :class:`LiteLLMNotesGenerator`, shaped like
``litellm.acompletion`` -- so every provider call is a double. Models and keys are rows in a
throwaway model registry (a temporary database), never environment variables.

Settings come from :class:`FakeLoader`, a dict-backed object with the same ``__call__`` shape as
``AtlasSettings.to_loader()``; that keeps assertions independent of the developer's shell and
``.env``.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.config.settings import AtlasSettings, repo_root
from src.domain.interfaces.usage_ledger import UsageRecord
from src.domain.models.playlist import (
    FailureKind,
    FailureType,
    NotesGenerationError,
)
from src.infrastructure.llm import notes_base
from src.infrastructure.llm.factory import build_notes_generator
from src.infrastructure.llm.litellm_notes import LiteLLMNotesGenerator
from src.infrastructure.llm.models import DEFAULT_OPENROUTER_MODEL
from src.infrastructure.llm.notes_base import (
    MIN_NOTES_CHARS,
    TITLE_PLACEHOLDER,
    TRANSCRIPT_PLACEHOLDER,
    NotesGeneratorConfigError,
    NotesPromptSource,
    check_input_size,
    estimate_tokens,
    render_prompt,
    validate_notes,
    validate_part,
)
from src.infrastructure.llm.registry.providers import litellm_model_for
from src.infrastructure.llm.registry.repo import RegistryRepo
from src.infrastructure.llm.registry.service import ModelRegistry

FENCE = "`" * 3

#: Long enough to clear ``MIN_NOTES_CHARS`` so a length assertion is testing
#: the length rule rather than accidentally the unwrap rule.
NOTES_BODY = (
    "## Notes\n\n"
    "- the transcript explains three loop forms and when each one wins.\n\n"
    "## Key Points\n\n"
    "- **Key Points** - for, while and do-while each have a place.\n\n"
    "## Important Details\n\n"
    "- includes a worked example per loop.\n"
)
GOOD_NOTES = NOTES_BODY + f"\n{FENCE}python\nfor item in items:\n    print(item)\n{FENCE}"

TEST_GEMINI_KEY = "test-gemini-key-not-real"
TEST_OPENROUTER_KEY = "sk-or-test-not-a-real-key"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"


# --------------------------------------------------------------------------
# doubles
# --------------------------------------------------------------------------


class FakeLoader:
    """Dict-backed stand-in for the ``SettingsLoader`` protocol."""

    def __init__(self, **values: Any) -> None:
        self._values = values

    def __call__(self, key_path: str, default: Any = None) -> Any:
        return self._values.get(key_path, default)

    def override(self, **values: Any) -> FakeLoader:
        """Copy of this loader with some keys replaced."""
        return FakeLoader(**{**self._values, **values})


class FakeCompletion:
    """``litellm.acompletion`` double: records every call, replays canned outcomes.

    ``outcomes`` is consumed one entry per call (the last repeats): an exception is raised, a
    string is returned as the model's reply.
    """

    def __init__(
        self,
        *outcomes: str | Exception,
        usage: tuple[int, int] | None = (1200, 345),
        finish_reason: str | None = "stop",
    ) -> None:
        self.outcomes = list(outcomes) or [GOOD_NOTES]
        self.usage = usage
        self.finish_reason = finish_reason
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **params: Any) -> Any:
        self.calls.append(params)
        outcome = self.outcomes[min(len(self.calls) - 1, len(self.outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        usage = None
        if self.usage is not None:
            prompt, completion = self.usage
            usage = type(
                "Usage",
                (),
                {
                    "prompt_tokens": prompt,
                    "completion_tokens": completion,
                    "total_tokens": prompt + completion,
                },
            )()
        message = type("Message", (), {"content": outcome})()
        choice = type("Choice", (), {"message": message, "finish_reason": self.finish_reason})()
        return type("Response", (), {"choices": [choice], "usage": usage})()


class RecordingLedger:
    """Usage ledger double. ``explode`` makes every write fail."""

    def __init__(self, *, explode: bool = False) -> None:
        self.explode = explode
        self.records: list[UsageRecord] = []

    async def record_usage(self, record: UsageRecord) -> None:
        if self.explode:
            raise RuntimeError("ledger backend is down")
        self.records.append(record)


@pytest.fixture
def settings() -> FakeLoader:
    return FakeLoader(
        notes_prompt_name="notes_v1.yaml",
        max_auto_retries=2,
        backoff_base_seconds=1.0,
        backoff_cap_seconds=30.0,
        prompts_base_dir="src/prompts",
    )


@pytest.fixture
def registry(temp_db: Path) -> ModelRegistry:
    """An empty registry in a throwaway database (chain order = the order models are added)."""
    return ModelRegistry(RegistryRepo(temp_db))


def add_model(registry: ModelRegistry, provider: str, model_id: str, **extra: Any) -> Any:
    return registry.repo.add_model(
        provider=provider,
        model_id=model_id,
        display_name=model_id,
        litellm_model=litellm_model_for(provider, model_id),
        api_base=extra.pop("api_base", None),
        input_token_limit=extra.pop("input_token_limit", 1_000_000),
        **extra,
    )


def add_key(registry: ModelRegistry, provider: str, value: str) -> Any:
    return registry.repo.add_key(provider=provider, label=f"{provider} key", key_value=value)


def add_gemini(registry: ModelRegistry, **extra: Any) -> Any:
    model = add_model(registry, "gemini", "gemini-3.5-flash", **extra)
    add_key(registry, "gemini", TEST_GEMINI_KEY)
    return model


def add_openrouter(registry: ModelRegistry, **extra: Any) -> Any:
    extra.setdefault("input_token_limit", 128_000)
    model = add_model(
        registry, "openrouter", DEFAULT_OPENROUTER_MODEL, api_base=OPENROUTER_BASE, **extra
    )
    add_key(registry, "openrouter", TEST_OPENROUTER_KEY)
    return model


@pytest.fixture
def gemini_registry(registry: ModelRegistry) -> ModelRegistry:
    add_gemini(registry)
    return registry


@pytest.fixture
def openrouter_registry(registry: ModelRegistry) -> ModelRegistry:
    add_openrouter(registry)
    return registry


@pytest.fixture
def chain_registry(registry: ModelRegistry) -> ModelRegistry:
    """Gemini first, OpenRouter as the fallback, each with its own key."""
    add_gemini(registry)
    add_openrouter(registry)
    return registry


def generator_for(
    settings: FakeLoader, registry: ModelRegistry, completion: FakeCompletion, **kwargs: Any
) -> LiteLLMNotesGenerator:
    return LiteLLMNotesGenerator(settings, completion=completion, registry=registry, **kwargs)


# --------------------------------------------------------------------------
# 1. render_prompt
# --------------------------------------------------------------------------


class TestRenderPrompt:
    def test_substitutes_both_placeholders_and_leaves_the_rest_untouched(self) -> None:
        template = (
            "Header\n"
            '**Title**: "' + TITLE_PLACEHOLDER + '"\n'
            '**Transcript**: "' + TRANSCRIPT_PLACEHOLDER + '"\n'
            "Footer\n"
        )
        rendered = render_prompt(template, title="Loops", transcript="hello world")

        assert "Header" in rendered
        assert "Footer" in rendered
        assert '**Title**: "Loops"' in rendered
        assert '**Transcript**: "hello world"' in rendered
        assert TITLE_PLACEHOLDER not in rendered
        assert TRANSCRIPT_PLACEHOLDER not in rendered

    def test_repeats_every_occurrence_not_just_the_first(self) -> None:
        template = f"{TITLE_PLACEHOLDER} / {TITLE_PLACEHOLDER} / {TRANSCRIPT_PLACEHOLDER}"
        assert render_prompt(template, title="A", transcript="B") == "A / A / B"

    @pytest.mark.parametrize("missing", [TITLE_PLACEHOLDER, TRANSCRIPT_PLACEHOLDER])
    def test_missing_placeholder_is_a_hard_failure(self, missing: str) -> None:
        template = TITLE_PLACEHOLDER + TRANSCRIPT_PLACEHOLDER
        template = template.replace(missing, "")

        with pytest.raises(NotesGeneratorConfigError) as excinfo:
            render_prompt(template, title="A", transcript="B")

        # The message names what is missing so the operator can fix the file.
        assert missing in str(excinfo.value)

    def test_empty_template_is_rejected(self) -> None:
        with pytest.raises(NotesGeneratorConfigError):
            render_prompt("   ", title="A", transcript="B")


# --------------------------------------------------------------------------
# 2. the real prompt file is intact
# --------------------------------------------------------------------------


class TestRealPromptFile:
    """Guards the user-authored prompt against corruption or an edit that drops
    a placeholder. Every generation reads this file, so a silent edit here is a
    silent change to the product."""

    def test_prompt_file_loads_with_both_placeholders(self) -> None:
        path = repo_root() / "src" / "prompts" / "notes_v1.yaml"
        assert path.exists(), f"the prompt file is missing: {path}"

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)

        prompt = data.get("prompt")
        assert isinstance(prompt, str) and prompt.strip(), "notes_v1.yaml has an empty 'prompt'"
        assert TITLE_PLACEHOLDER in prompt, "notes_v1.yaml lost the {{Title}} placeholder"
        assert TRANSCRIPT_PLACEHOLDER in prompt, "notes_v1.yaml lost the {{Transcript}} placeholder"
        assert data.get("version") == "notes_v1"

    def test_prompt_source_reads_it_and_renders(self, settings: FakeLoader) -> None:
        source = NotesPromptSource(settings)

        assert source.path.name == "notes_v1.yaml"
        assert source.version() == "notes_v1"

        rendered = render_prompt(source.template(), title="T", transcript="X")
        assert TITLE_PLACEHOLDER not in rendered
        assert TRANSCRIPT_PLACEHOLDER not in rendered
        assert "T" in rendered and "X" in rendered

    def test_prompt_source_reports_a_missing_file_clearly(self) -> None:
        source = NotesPromptSource(FakeLoader(prompts_base_dir="src/prompts", notes_prompt_name="no.yaml"))

        with pytest.raises(NotesGeneratorConfigError) as excinfo:
            source.template()
        assert "no.yaml" in str(excinfo.value)


# --------------------------------------------------------------------------
# 3. validate_notes
# --------------------------------------------------------------------------


class TestValidateNotes:
    @pytest.mark.parametrize("blank", ["", "   ", "\n\n\t  \n"])
    def test_empty_or_whitespace_is_malformed(self, blank: str) -> None:
        with pytest.raises(NotesGenerationError) as excinfo:
            validate_notes(blank)
        assert excinfo.value.failure_type is FailureType.OUTPUT_MALFORMED
        # PRD 18: a malformed response is retried, so it must be transient.
        assert excinfo.value.failure_kind is FailureKind.TRANSIENT

    def test_v1_3_layout_is_accepted(self) -> None:
        body = (
            "Summary\nA short overview of the video and what it teaches the viewer.\n\n"
            "Questions and Answers\nBasic\n1. What is it?\n   *Answer:* A thing.\n\n"
            "Notes\n- Point one explained in plain words.\n\n"
            "History\n- 2010: first release of the thing."
        )
        assert validate_notes(body) == body

    def test_v1_3_sections_alone_are_enough(self) -> None:
        body = "Questions and Answers\n1. Why?\n   *Answer:* Because of reasons.\n\nHistory\n- Began in 1999 with the first public version."
        assert validate_notes(body) == body

    def test_implausibly_short_output_is_malformed(self) -> None:
        with pytest.raises(NotesGenerationError) as excinfo:
            validate_notes("too short")
        assert excinfo.value.failure_type is FailureType.OUTPUT_MALFORMED

    def test_outer_markdown_fence_is_stripped_and_the_inner_one_kept(self) -> None:
        fenced = f"{FENCE}markdown\n{NOTES_BODY}\n{FENCE}python\nprint(1)\n{FENCE}\n{FENCE}"

        cleaned = validate_notes(fenced)

        assert cleaned.startswith("## Notes")
        assert not cleaned.startswith(FENCE)
        assert cleaned.endswith(FENCE)
        assert f"{FENCE}python\nprint(1)\n{FENCE}" in cleaned

    def test_outer_fence_without_a_language_tag_is_stripped(self) -> None:
        cleaned = validate_notes(f"{FENCE}\n{NOTES_BODY}{FENCE}")
        assert cleaned == NOTES_BODY.strip()

    def test_a_fence_inside_the_content_is_not_treated_as_a_wrapper(self) -> None:
        content = NOTES_BODY + f"\n{FENCE}python\nprint(1)\n{FENCE}"
        assert validate_notes(content) == content

    def test_normal_content_passes_through_unchanged(self) -> None:
        assert validate_notes(GOOD_NOTES) == GOOD_NOTES
        assert validate_notes(GOOD_NOTES.strip()) == GOOD_NOTES.strip()

    def test_a_leading_title_heading_is_dropped_because_the_doc_heading_supplies_it(self) -> None:
        cleaned = validate_notes("# Control Flow\n\n" + NOTES_BODY)
        assert cleaned == NOTES_BODY.strip()

    def test_output_without_the_expected_sections_is_malformed(self) -> None:
        refusal = "I am sorry, but I cannot summarise this video because the transcript " * 3
        with pytest.raises(NotesGenerationError) as excinfo:
            validate_notes(refusal)
        assert excinfo.value.failure_type is FailureType.OUTPUT_MALFORMED
        assert excinfo.value.failure_kind is FailureKind.TRANSIENT

    @pytest.mark.parametrize(
        "decorated",
        ["## Notes\n\n## Key Points", "**Notes**\n\n**Takeaways**", "1. Key Points\n\n2. Takeaways"],
    )
    def test_section_detection_is_loose_about_markers(self, decorated: str) -> None:
        body = decorated + "\n\n" + "x" * MIN_NOTES_CHARS
        assert validate_notes(body).endswith("x" * MIN_NOTES_CHARS)

    def test_surrounding_whitespace_is_trimmed(self) -> None:
        assert validate_notes(f"\n\n  {NOTES_BODY.strip()}  \n\n") == NOTES_BODY.strip()


# --------------------------------------------------------------------------
# 4. check_input_size
# --------------------------------------------------------------------------


class TestCheckInputSize:
    def test_under_the_limit_passes(self) -> None:
        check_input_size("a" * 400, 1_000_000)

    def test_exactly_at_the_limit_passes(self) -> None:
        transcript = "a" * 40  # 10 estimated tokens
        assert estimate_tokens(transcript) == 10
        check_input_size(transcript, 10)

    def test_over_the_limit_is_permanent_and_names_both_sizes(self) -> None:
        transcript = "a" * 4000  # ~1,000 estimated tokens

        with pytest.raises(NotesGenerationError) as excinfo:
            check_input_size(transcript, 512)

        error = excinfo.value
        assert error.failure_type is FailureType.TRANSCRIPT_TOO_LARGE
        assert error.failure_kind is FailureKind.PERMANENT
        message = str(error)
        assert "1,000" in message, message
        assert "512" in message, message

    def test_nothing_is_truncated_and_the_reason_leaks_no_transcript(self) -> None:
        secret_phrase = "the quick brown fox jumps over the lazy dog"
        transcript = f"{secret_phrase} " * 500

        with pytest.raises(NotesGenerationError) as excinfo:
            check_input_size(transcript, 512)

        assert len(transcript) == len(secret_phrase + " ") * 500
        assert secret_phrase not in str(excinfo.value)
        assert secret_phrase not in (excinfo.value.diagnostic or "")

    def test_a_non_positive_limit_disables_the_check(self) -> None:
        check_input_size("a" * 4000, 0)
        check_input_size("a" * 4000, -1)



# --------------------------------------------------------------------------
# 5 + 6. one call per model, no retries inside the adapter
# --------------------------------------------------------------------------


class TestSingleModel:
    async def test_generates_notes_and_returns_the_prompt_version(
        self, settings: FakeLoader, gemini_registry: ModelRegistry
    ) -> None:
        completion = FakeCompletion()

        result = await generator_for(settings, gemini_registry, completion).generate_notes(
            title="Loops", transcript="for x in y: pass"
        )

        assert result.provider == "gemini"
        assert result.model == "gemini-3.5-flash"
        assert result.prompt_version == "notes_v1"
        assert result.content == GOOD_NOTES

        sent = completion.calls[0]
        assert sent["model"] == "gemini/gemini-3.5-flash"
        assert sent["api_key"] == TEST_GEMINI_KEY
        assert sent["num_retries"] == 0  # ProcessVideoUseCase owns retries
        assert sent["messages"][0]["role"] == "user"
        assert "Loops" in sent["messages"][0]["content"]
        assert "for x in y: pass" in sent["messages"][0]["content"]
        assert "api_base" not in sent

    async def test_a_one_off_prompt_replaces_the_prompt_file_and_is_tagged_custom(
        self, settings: FakeLoader, gemini_registry: ModelRegistry
    ) -> None:
        completion = FakeCompletion()
        generator = generator_for(settings, gemini_registry, completion)

        custom = await generator.generate_notes(
            title="Loops",
            transcript="for x in y: pass",
            prompt_template="Be brief. {{Title}} / {{Transcript}}",
        )
        default = await generator.generate_notes(title="Loops", transcript="for x in y: pass")

        assert completion.calls[0]["messages"][0]["content"] == "Be brief. Loops / for x in y: pass"
        assert custom.prompt_version == "custom"
        assert default.prompt_version == "notes_v1"  # the next call is back on the prompt file
        assert "Be brief." not in completion.calls[1]["messages"][0]["content"]

    async def test_a_one_off_prompt_without_placeholders_is_a_config_error(
        self, settings: FakeLoader, gemini_registry: ModelRegistry
    ) -> None:
        with pytest.raises(NotesGeneratorConfigError):
            await generator_for(settings, gemini_registry, FakeCompletion()).generate_notes(
                title="t", transcript="x", prompt_template="no placeholders here"
            )

    async def test_a_fenced_response_is_unwrapped_end_to_end(
        self, settings: FakeLoader, gemini_registry: ModelRegistry
    ) -> None:
        completion = FakeCompletion(f"{FENCE}markdown\n{GOOD_NOTES}{FENCE}")

        result = await generator_for(settings, gemini_registry, completion).generate_notes(
            title="t", transcript="x"
        )

        assert result.content == GOOD_NOTES

    async def test_an_empty_response_is_a_transient_failure_and_is_not_retried_here(
        self, settings: FakeLoader, gemini_registry: ModelRegistry
    ) -> None:
        """Retry/backoff belongs to ProcessVideoUseCase alone: one call per attempt."""
        completion = FakeCompletion("   ")

        with pytest.raises(NotesGenerationError) as excinfo:
            await generator_for(settings, gemini_registry, completion).generate_notes(
                title="t", transcript="x"
            )

        assert excinfo.value.failure_type is FailureType.OUTPUT_MALFORMED
        assert excinfo.value.failure_kind is FailureKind.TRANSIENT
        assert len(completion.calls) == 1

    async def test_a_truncated_response_is_rejected(
        self, settings: FakeLoader, gemini_registry: ModelRegistry
    ) -> None:
        completion = FakeCompletion(finish_reason="length")

        with pytest.raises(NotesGenerationError) as excinfo:
            await generator_for(settings, gemini_registry, completion).generate_notes(
                title="t", transcript="x"
            )

        assert excinfo.value.failure_type is FailureType.OUTPUT_MALFORMED

    async def test_an_oversized_transcript_never_reaches_the_provider(
        self, settings: FakeLoader, registry: ModelRegistry
    ) -> None:
        add_gemini(registry, input_token_limit=10)
        completion = FakeCompletion()

        with pytest.raises(NotesGenerationError) as excinfo:
            await generator_for(settings, registry, completion).generate_notes(
                title="t", transcript="a" * 4000
            )

        assert excinfo.value.failure_type is FailureType.TRANSCRIPT_TOO_LARGE
        assert completion.calls == []

    async def test_a_usage_record_is_emitted(
        self, settings: FakeLoader, gemini_registry: ModelRegistry
    ) -> None:
        ledger = RecordingLedger()

        await generator_for(
            settings, gemini_registry, FakeCompletion(), usage_ledger=ledger
        ).generate_notes(title="t", transcript="x")

        assert len(ledger.records) == 1
        record = ledger.records[0]
        assert record.provider == "gemini"
        assert record.operation == "generate_notes"
        assert record.model == "gemini-3.5-flash"
        assert record.input_tokens == 1200
        assert record.output_tokens == 345
        assert record.total_tokens == 1545
        assert record.cost_usd > 0.0  # gemini-3.5-flash is priced by LiteLLM
        assert record.cache_hit is False

    async def test_missing_token_counts_default_to_zero(
        self, settings: FakeLoader, gemini_registry: ModelRegistry
    ) -> None:
        ledger = RecordingLedger()

        await generator_for(
            settings, gemini_registry, FakeCompletion(usage=None), usage_ledger=ledger
        ).generate_notes(title="t", transcript="x")

        record = ledger.records[0]
        assert (record.input_tokens, record.output_tokens, record.total_tokens) == (0, 0, 0)

    async def test_no_ledger_configured_is_fine(
        self, settings: FakeLoader, gemini_registry: ModelRegistry
    ) -> None:
        result = await generator_for(settings, gemini_registry, FakeCompletion()).generate_notes(
            title="t", transcript="x"
        )
        assert result.content == GOOD_NOTES

    async def test_a_ledger_failure_does_not_fail_generation(
        self,
        settings: FakeLoader,
        gemini_registry: ModelRegistry,
        openrouter_registry: ModelRegistry,
    ) -> None:
        for registry in (gemini_registry, openrouter_registry):
            completion = FakeCompletion()
            generator = generator_for(
                settings, registry, completion, usage_ledger=RecordingLedger(explode=True)
            )

            result = await generator.generate_notes(title="t", transcript="x")

            assert result.content == GOOD_NOTES
            assert len(completion.calls) == 1

    async def test_a_missing_key_points_to_the_keys_page_and_makes_no_call(
        self, settings: FakeLoader, registry: ModelRegistry
    ) -> None:
        add_model(registry, "gemini", "gemini-3.5-flash")  # no key at all
        completion = FakeCompletion()

        with pytest.raises(NotesGeneratorConfigError) as excinfo:
            await generator_for(settings, registry, completion).generate_notes(
                title="t", transcript="x"
            )

        assert "Keys page" in str(excinfo.value)
        assert completion.calls == []

    async def test_an_empty_chain_points_to_the_models_page(
        self, settings: FakeLoader, registry: ModelRegistry
    ) -> None:
        with pytest.raises(NotesGeneratorConfigError, match="Models page"):
            await generator_for(settings, registry, FakeCompletion()).generate_notes(
                title="t", transcript="x"
            )


class TestOpenRouterModel:
    async def test_generates_notes_against_the_openrouter_endpoint(
        self, settings: FakeLoader, openrouter_registry: ModelRegistry
    ) -> None:
        completion = FakeCompletion()

        result = await generator_for(settings, openrouter_registry, completion).generate_notes(
            title="Loops", transcript="for x in y: pass"
        )

        assert result.provider == "openrouter"
        assert result.model == DEFAULT_OPENROUTER_MODEL
        assert result.prompt_version == "notes_v1"
        sent = completion.calls[0]
        assert sent["model"] == f"openrouter/{DEFAULT_OPENROUTER_MODEL}"
        assert sent["api_base"] == OPENROUTER_BASE
        assert sent["api_key"] == TEST_OPENROUTER_KEY

    async def test_a_usage_record_is_emitted_with_the_openrouter_cost(
        self, settings: FakeLoader, openrouter_registry: ModelRegistry
    ) -> None:
        ledger = RecordingLedger()

        await generator_for(
            settings, openrouter_registry, FakeCompletion(usage=(1100, 400)), usage_ledger=ledger
        ).generate_notes(title="t", transcript="x")

        record = ledger.records[0]
        assert record.provider == "openrouter"
        assert record.operation == "generate_notes"
        assert (record.input_tokens, record.output_tokens, record.total_tokens) == (1100, 400, 1500)
        # LiteLLM prices the default model, so cost is real.
        assert record.cost_usd > 0.0

    async def test_an_oversized_transcript_is_refused_before_the_call(
        self, settings: FakeLoader, registry: ModelRegistry
    ) -> None:
        add_openrouter(registry, input_token_limit=8)
        completion = FakeCompletion()

        with pytest.raises(NotesGenerationError) as excinfo:
            await generator_for(settings, registry, completion).generate_notes(
                title="t", transcript="a" * 4000
            )

        assert excinfo.value.failure_type is FailureType.TRANSCRIPT_TOO_LARGE
        assert completion.calls == []


# --------------------------------------------------------------------------
# 7. models are data: custom endpoints, key rotation, fallback
# --------------------------------------------------------------------------


class TestConfiguredModels:
    async def test_a_custom_endpoint_is_passed_to_the_provider_call(
        self, settings: FakeLoader, registry: ModelRegistry
    ) -> None:
        add_model(registry, "freellmapi", "gpt-5-mini", api_base="http://localhost:3001/v1")
        add_key(registry, "freellmapi", "omni-test-key")
        completion = FakeCompletion()

        result = await generator_for(settings, registry, completion).generate_notes(
            title="t", transcript="x"
        )

        assert (result.provider, result.model) == ("freellmapi", "gpt-5-mini")
        sent = completion.calls[0]
        assert sent["model"] == "openai/gpt-5-mini"  # an OpenAI-compatible gateway
        assert sent["api_base"] == "http://localhost:3001/v1"
        assert sent["api_key"] == "omni-test-key"

    async def test_a_per_model_timeout_overrides_the_global_one(
        self, settings: FakeLoader, registry: ModelRegistry
    ) -> None:
        add_model(registry, "nvidia", "slow-model", request_timeout_seconds=3600.0)
        add_key(registry, "nvidia", "nv-key")
        completion = FakeCompletion()

        await generator_for(settings, registry, completion).generate_notes(
            title="t", transcript="x"
        )

        assert completion.calls[0]["timeout"] == 3600.0

    async def test_keys_of_one_model_rotate_before_falling_back(
        self, settings: FakeLoader, chain_registry: ModelRegistry
    ) -> None:
        add_key(chain_registry, "gemini", "gem-key-2")
        completion = FakeCompletion(RuntimeError("429 Too Many Requests"), GOOD_NOTES)

        result = await generator_for(settings, chain_registry, completion).generate_notes(
            title="t", transcript="x"
        )

        assert result.provider == "gemini"
        assert [call["api_key"] for call in completion.calls] == [TEST_GEMINI_KEY, "gem-key-2"]

    async def test_falls_back_to_the_next_model_when_the_first_is_turned_away(
        self, settings: FakeLoader, chain_registry: ModelRegistry
    ) -> None:
        completion = FakeCompletion(RuntimeError("503 Service Unavailable"), GOOD_NOTES)
        ledger = RecordingLedger()

        result = await generator_for(
            settings, chain_registry, completion, usage_ledger=ledger
        ).generate_notes(title="t", transcript="x")

        assert (result.provider, result.model) == ("openrouter", "openai/gpt-5-mini")
        assert [call["model"] for call in completion.calls] == [
            "gemini/gemini-3.5-flash",
            "openrouter/openai/gpt-5-mini",
        ]
        # The ledger records the model that actually answered.
        assert [(r.provider, r.model) for r in ledger.records] == [
            ("openrouter", "openai/gpt-5-mini")
        ]

    async def test_a_rejected_key_on_the_first_model_still_falls_back(
        self, settings: FakeLoader, chain_registry: ModelRegistry
    ) -> None:
        completion = FakeCompletion(RuntimeError("401 Unauthorized"), GOOD_NOTES)

        result = await generator_for(settings, chain_registry, completion).generate_notes(
            title="t", transcript="x"
        )

        assert result.provider == "openrouter"

    async def test_a_model_without_a_key_is_skipped(
        self, settings: FakeLoader, registry: ModelRegistry
    ) -> None:
        add_model(registry, "gemini", "gemini-3.5-flash")  # no Gemini key
        add_openrouter(registry)
        completion = FakeCompletion()

        result = await generator_for(settings, registry, completion).generate_notes(
            title="t", transcript="x"
        )

        assert result.provider == "openrouter"
        assert len(completion.calls) == 1

    async def test_a_transcript_too_big_for_one_model_goes_to_a_bigger_one(
        self, settings: FakeLoader, registry: ModelRegistry
    ) -> None:
        add_openrouter(registry, input_token_limit=10)
        add_gemini(registry, input_token_limit=1_000_000)
        completion = FakeCompletion()

        result = await generator_for(settings, registry, completion).generate_notes(
            title="t", transcript="a" * 4000
        )

        assert result.provider == "gemini"
        assert len(completion.calls) == 1

    async def test_every_model_failing_raises_the_transient_error_so_the_pipeline_retries(
        self, settings: FakeLoader, chain_registry: ModelRegistry
    ) -> None:
        completion = FakeCompletion(RuntimeError("401 Unauthorized"), RuntimeError("429 Too Many"))

        with pytest.raises(NotesGenerationError) as excinfo:
            await generator_for(settings, chain_registry, completion).generate_notes(
                title="t", transcript="x"
            )

        assert excinfo.value.failure_type is FailureType.RATE_LIMITED
        assert excinfo.value.failure_kind is FailureKind.TRANSIENT

    async def test_every_model_rejecting_its_key_is_a_permanent_config_error(
        self, settings: FakeLoader, chain_registry: ModelRegistry
    ) -> None:
        completion = FakeCompletion(RuntimeError("401 Unauthorized"))

        with pytest.raises(NotesGeneratorConfigError):
            await generator_for(settings, chain_registry, completion).generate_notes(
                title="t", transcript="x"
            )

        assert len(completion.calls) == 2

    async def test_a_bad_response_does_not_trigger_a_fallback(
        self, settings: FakeLoader, chain_registry: ModelRegistry
    ) -> None:
        """Only a model that is turned away falls back; its answer is judged as is."""
        completion = FakeCompletion("   ")

        with pytest.raises(NotesGenerationError) as excinfo:
            await generator_for(settings, chain_registry, completion).generate_notes(
                title="t", transcript="x"
            )

        assert excinfo.value.failure_type is FailureType.OUTPUT_MALFORMED
        assert len(completion.calls) == 1


# --------------------------------------------------------------------------
# 8. the factory
# --------------------------------------------------------------------------


class TestBuildNotesGenerator:
    def test_builds_a_generator_on_the_registry(
        self, settings: FakeLoader, gemini_registry: ModelRegistry
    ) -> None:
        generator = build_notes_generator(settings, registry=gemini_registry)

        assert isinstance(generator, LiteLLMNotesGenerator)
        assert generator.provider == "gemini"

    def test_the_primary_model_is_the_first_of_the_chain(
        self, settings: FakeLoader, chain_registry: ModelRegistry
    ) -> None:
        generator = build_notes_generator(settings, registry=chain_registry)

        assert generator.provider == "gemini"
        assert [spec.litellm_model for spec in generator.specs] == [
            "gemini/gemini-3.5-flash",
            "openrouter/openai/gpt-5-mini",
        ]

    def test_the_chain_is_read_live_not_at_build_time(
        self, settings: FakeLoader, chain_registry: ModelRegistry
    ) -> None:
        generator = build_notes_generator(settings, registry=chain_registry)
        first, second = chain_registry.repo.list_models()

        chain_registry.repo.replace_chain([(second.id, 1, True), (first.id, 2, True)])

        assert generator.provider == "openrouter"

    def test_an_empty_registry_builds_and_has_no_primary_model(
        self, settings: FakeLoader, registry: ModelRegistry
    ) -> None:
        generator = build_notes_generator(settings, registry=registry)

        assert (generator.provider, generator.model, generator.specs) == ("", "", ())

    def test_the_real_settings_loader_is_accepted(self, registry: ModelRegistry) -> None:
        assert build_notes_generator(AtlasSettings().to_loader(), registry=registry).provider == ""

    def test_a_registry_is_required(self, settings: FakeLoader) -> None:
        with pytest.raises(TypeError):
            build_notes_generator(settings)  # type: ignore[call-arg]


# --------------------------------------------------------------------------
# 10. secret hygiene
# --------------------------------------------------------------------------

FAKE_KEY = "AIzaSyTESTKEY0000000000000000000000000000"
FAKE_OPENROUTER_KEY = "sk-or-v1-0000000000000000000000000000TEST"


class TestSecretHygiene:
    async def test_the_gemini_key_never_reaches_an_exception_a_log_or_the_ledger(
        self,
        settings: FakeLoader,
        registry: ModelRegistry,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        add_model(registry, "gemini", "gemini-3.5-flash")
        add_key(registry, "gemini", FAKE_KEY)
        # Worst case: the provider echoes the credential back inside its error.
        completion = FakeCompletion(RuntimeError(f"400 invalid key {FAKE_KEY}"))
        ledger = RecordingLedger()
        generator = generator_for(settings, registry, completion, usage_ledger=ledger)

        with caplog.at_level(logging.DEBUG), pytest.raises(NotesGenerationError) as excinfo:
            await generator.generate_notes(title="t", transcript="a secret transcript line")

        error = excinfo.value
        assert FAKE_KEY not in str(error)
        assert FAKE_KEY not in (getattr(error, "diagnostic", None) or "")
        assert FAKE_KEY not in caplog.text
        for record in ledger.records:
            assert FAKE_KEY not in repr(asdict(record))

    async def test_the_openrouter_key_never_reaches_an_exception_a_log_or_the_ledger(
        self,
        settings: FakeLoader,
        registry: ModelRegistry,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        add_model(registry, "openrouter", "openai/gpt-5-mini", api_base=OPENROUTER_BASE)
        add_key(registry, "openrouter", FAKE_OPENROUTER_KEY)
        completion = FakeCompletion(RuntimeError(f"401 Unauthorized: {FAKE_OPENROUTER_KEY}"))
        ledger = RecordingLedger()
        generator = generator_for(settings, registry, completion, usage_ledger=ledger)

        # "Unauthorized" is a rejected key: a permanent config error, not a retryable one.
        with caplog.at_level(logging.DEBUG), pytest.raises(NotesGeneratorConfigError) as excinfo:
            await generator.generate_notes(title="t", transcript="a secret transcript line")

        error = excinfo.value
        assert FAKE_OPENROUTER_KEY not in str(error)
        assert FAKE_OPENROUTER_KEY not in (getattr(error, "diagnostic", None) or "")
        assert FAKE_OPENROUTER_KEY not in caplog.text
        for record in ledger.records:
            assert FAKE_OPENROUTER_KEY not in repr(asdict(record))

    async def test_a_successful_call_logs_neither_key_nor_transcript(
        self,
        settings: FakeLoader,
        registry: ModelRegistry,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        add_model(registry, "gemini", "gemini-3.5-flash")
        add_key(registry, "gemini", FAKE_KEY)
        add_model(registry, "openrouter", "openai/gpt-5-mini", api_base=OPENROUTER_BASE)
        add_key(registry, "openrouter", FAKE_OPENROUTER_KEY)
        transcript = "a very secret transcript line about closures"
        # First model fails, so the fallback path is logged as well.
        completion = FakeCompletion(RuntimeError("503 Service Unavailable"), GOOD_NOTES)

        with caplog.at_level(logging.DEBUG):
            await generator_for(
                settings, registry, completion, usage_ledger=RecordingLedger()
            ).generate_notes(title="t", transcript=transcript)

        assert caplog.text, "expected the adapter to log something at all"
        assert FAKE_KEY not in caplog.text
        assert FAKE_OPENROUTER_KEY not in caplog.text
        assert transcript not in caplog.text

    def test_the_adapter_never_stores_a_key_on_itself(
        self, settings: FakeLoader, registry: ModelRegistry
    ) -> None:
        add_model(registry, "gemini", "gemini-3.5-flash")
        add_key(registry, "gemini", FAKE_KEY)
        generator = generator_for(settings, registry, FakeCompletion())

        assert FAKE_KEY not in repr(vars(generator))

    def test_importing_the_llm_package_does_not_import_a_provider_sdk(self) -> None:
        """`import src.infrastructure.llm` must stay free of provider SDKs.

        Run in a clean interpreter: the test process will have imported LiteLLM
        for other tests, which would hide a module-level import regression.
        """
        script = (
            "import sys; import src.infrastructure.llm as m; "
            "from src.infrastructure.llm.factory import build_notes_generator; "
            "leaked = [n for n in ('litellm', 'google.genai', 'openai') if n in sys.modules]; "
            "assert not leaked, leaked; "
            "assert m.build_notes_generator and m.NotesGeneratorPort and m.GeneratedNotes"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=repo_root(),
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr


def test_notes_generator_module_is_importable_without_a_key() -> None:
    assert MIN_NOTES_CHARS > 0
    assert Path(notes_base.__file__).name == "notes_base.py"
    assert len(GOOD_NOTES) > MIN_NOTES_CHARS


class TestValidatePart:
    def test_accepts_a_part_with_its_heading(self) -> None:
        body = "History\n- 2010: the first release of the topic arrived."
        assert validate_part(body, "History") == body

    def test_rejects_a_part_without_its_heading(self) -> None:
        with pytest.raises(NotesGenerationError) as excinfo:
            validate_part("Notes\n" + "Some notes line here. " * 4, "History")
        assert excinfo.value.failure_type is FailureType.OUTPUT_MALFORMED

    def test_rejects_a_too_short_part(self) -> None:
        with pytest.raises(NotesGenerationError):
            validate_part("History\n- x", "History")
