"""Tests for the notes generator: port, guardrails, the LiteLLM adapter and its factory.

No test in this file touches the network or needs a real key. The provider boundary is exactly
one seam -- the ``completion`` constructor argument on :class:`LiteLLMNotesGenerator`, shaped like
``litellm.acompletion`` -- so every provider call is a double. Keys are fake values set in the
environment by an autouse fixture.

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
from src.infrastructure.llm.models import DEFAULT_OPENROUTER_MODEL, resolve_model_specs
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
)
from src.shared.api_keys import reset_pools

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


@pytest.fixture(autouse=True)
def _provider_keys(monkeypatch: pytest.MonkeyPatch):
    """Fake keys for the two default providers; key pools are process-wide, so start clean."""
    monkeypatch.setenv("GEMINI_API_KEY", TEST_GEMINI_KEY)
    monkeypatch.setenv("OPENROUTER_API_KEY", TEST_OPENROUTER_KEY)
    reset_pools()
    yield
    reset_pools()


@pytest.fixture
def gemini_settings() -> FakeLoader:
    return FakeLoader(
        notes_provider="gemini",
        notes_model="",
        gemini_model="gemini-3.5-flash",
        gemini_input_token_limit=1_000_000,
        notes_prompt_name="notes_v1.yaml",
        gemini_api_key_env="GEMINI_API_KEY",
        max_auto_retries=2,
        backoff_base_seconds=1.0,
        backoff_cap_seconds=30.0,
        prompts_base_dir="src/prompts",
    )


@pytest.fixture
def openrouter_settings(gemini_settings: FakeLoader) -> FakeLoader:
    return gemini_settings.override(
        notes_provider="openrouter", openrouter_api_key_env="OPENROUTER_API_KEY"
    )


@pytest.fixture
def chain_settings(gemini_settings: FakeLoader) -> FakeLoader:
    """Gemini first, OpenRouter as the fallback, both listed in ``notes_models``."""
    return gemini_settings.override(
        notes_models=[
            {"model": "gemini/gemini-3.5-flash", "input_token_limit": 1_000_000},
            {"model": "openrouter/openai/gpt-5-mini"},
        ]
    )


def generator_for(
    settings: FakeLoader, completion: FakeCompletion, **kwargs: Any
) -> LiteLLMNotesGenerator:
    return LiteLLMNotesGenerator(settings, completion=completion, **kwargs)

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

    def test_prompt_source_reads_it_and_renders(self, gemini_settings: FakeLoader) -> None:
        source = NotesPromptSource(gemini_settings)

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
        self, gemini_settings: FakeLoader
    ) -> None:
        completion = FakeCompletion()

        result = await generator_for(gemini_settings, completion).generate_notes(
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
        self, gemini_settings: FakeLoader
    ) -> None:
        completion = FakeCompletion()
        generator = generator_for(gemini_settings, completion)

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
        self, gemini_settings: FakeLoader
    ) -> None:
        with pytest.raises(NotesGeneratorConfigError):
            await generator_for(gemini_settings, FakeCompletion()).generate_notes(
                title="t", transcript="x", prompt_template="no placeholders here"
            )

    async def test_notes_model_overrides_gemini_model(self, gemini_settings: FakeLoader) -> None:
        settings = gemini_settings.override(notes_model="gemini-2.5-pro")
        completion = FakeCompletion()

        result = await generator_for(settings, completion).generate_notes(
            title="t", transcript="x"
        )

        assert result.model == "gemini-2.5-pro"
        assert completion.calls[0]["model"] == "gemini/gemini-2.5-pro"

    async def test_a_fenced_response_is_unwrapped_end_to_end(
        self, gemini_settings: FakeLoader
    ) -> None:
        completion = FakeCompletion(f"{FENCE}markdown\n{GOOD_NOTES}{FENCE}")

        result = await generator_for(gemini_settings, completion).generate_notes(
            title="t", transcript="x"
        )

        assert result.content == GOOD_NOTES

    async def test_an_empty_response_is_a_transient_failure_and_is_not_retried_here(
        self, gemini_settings: FakeLoader
    ) -> None:
        """Retry/backoff belongs to ProcessVideoUseCase alone: one call per attempt."""
        completion = FakeCompletion("   ")

        with pytest.raises(NotesGenerationError) as excinfo:
            await generator_for(gemini_settings, completion).generate_notes(
                title="t", transcript="x"
            )

        assert excinfo.value.failure_type is FailureType.OUTPUT_MALFORMED
        assert excinfo.value.failure_kind is FailureKind.TRANSIENT
        assert len(completion.calls) == 1

    async def test_a_truncated_response_is_rejected(self, gemini_settings: FakeLoader) -> None:
        completion = FakeCompletion(finish_reason="length")

        with pytest.raises(NotesGenerationError) as excinfo:
            await generator_for(gemini_settings, completion).generate_notes(
                title="t", transcript="x"
            )

        assert excinfo.value.failure_type is FailureType.OUTPUT_MALFORMED

    async def test_an_oversized_transcript_never_reaches_the_provider(
        self, gemini_settings: FakeLoader
    ) -> None:
        settings = gemini_settings.override(gemini_input_token_limit=10)
        completion = FakeCompletion()

        with pytest.raises(NotesGenerationError) as excinfo:
            await generator_for(settings, completion).generate_notes(
                title="t", transcript="a" * 4000
            )

        assert excinfo.value.failure_type is FailureType.TRANSCRIPT_TOO_LARGE
        assert completion.calls == []

    async def test_a_usage_record_is_emitted(self, gemini_settings: FakeLoader) -> None:
        ledger = RecordingLedger()

        await generator_for(
            gemini_settings, FakeCompletion(), usage_ledger=ledger
        ).generate_notes(title="t", transcript="x")

        assert len(ledger.records) == 1
        record = ledger.records[0]
        assert record.provider == "gemini"
        assert record.operation == "generate_notes"
        assert record.model == "gemini-3.5-flash"
        assert record.input_tokens == 1200
        assert record.output_tokens == 345
        assert record.total_tokens == 1545
        assert record.cost_usd > 0.0  # gemini-3.5-flash is priced in CostCalculator
        assert record.cache_hit is False

    async def test_missing_token_counts_default_to_zero(self, gemini_settings: FakeLoader) -> None:
        ledger = RecordingLedger()

        await generator_for(
            gemini_settings, FakeCompletion(usage=None), usage_ledger=ledger
        ).generate_notes(title="t", transcript="x")

        record = ledger.records[0]
        assert (record.input_tokens, record.output_tokens, record.total_tokens) == (0, 0, 0)

    async def test_no_ledger_configured_is_fine(self, gemini_settings: FakeLoader) -> None:
        result = await generator_for(gemini_settings, FakeCompletion()).generate_notes(
            title="t", transcript="x"
        )
        assert result.content == GOOD_NOTES

    async def test_a_ledger_failure_does_not_fail_generation(
        self, gemini_settings: FakeLoader, openrouter_settings: FakeLoader
    ) -> None:
        for settings in (gemini_settings, openrouter_settings):
            completion = FakeCompletion()
            generator = generator_for(
                settings, completion, usage_ledger=RecordingLedger(explode=True)
            )

            result = await generator.generate_notes(title="t", transcript="x")

            assert result.content == GOOD_NOTES
            assert len(completion.calls) == 1

    async def test_a_missing_key_names_the_variable_not_the_value(
        self, gemini_settings: FakeLoader, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        completion = FakeCompletion()

        with pytest.raises(NotesGeneratorConfigError) as excinfo:
            await generator_for(gemini_settings, completion).generate_notes(
                title="t", transcript="x"
            )

        assert "GEMINI_API_KEY" in str(excinfo.value)
        assert completion.calls == []


class TestOpenRouterLegacySettings:
    async def test_generates_notes_against_the_openrouter_endpoint(
        self, openrouter_settings: FakeLoader
    ) -> None:
        completion = FakeCompletion()

        result = await generator_for(openrouter_settings, completion).generate_notes(
            title="Loops", transcript="for x in y: pass"
        )

        assert result.provider == "openrouter"
        assert result.model == DEFAULT_OPENROUTER_MODEL
        assert result.prompt_version == "notes_v1"
        sent = completion.calls[0]
        assert sent["model"] == f"openrouter/{DEFAULT_OPENROUTER_MODEL}"
        assert sent["api_base"] == "https://openrouter.ai/api/v1"
        assert sent["api_key"] == TEST_OPENROUTER_KEY

    async def test_notes_model_overrides_the_default(
        self, openrouter_settings: FakeLoader
    ) -> None:
        settings = openrouter_settings.override(notes_model="anthropic/claude-sonnet-4")

        result = await generator_for(settings, FakeCompletion()).generate_notes(
            title="t", transcript="x"
        )

        assert result.model == "anthropic/claude-sonnet-4"

    async def test_a_usage_record_is_emitted_with_the_openrouter_cost(
        self, openrouter_settings: FakeLoader
    ) -> None:
        ledger = RecordingLedger()

        await generator_for(
            openrouter_settings, FakeCompletion(usage=(1100, 400)), usage_ledger=ledger
        ).generate_notes(title="t", transcript="x")

        record = ledger.records[0]
        assert record.provider == "openrouter"
        assert record.operation == "generate_notes"
        assert (record.input_tokens, record.output_tokens, record.total_tokens) == (1100, 400, 1500)
        # The default model is priced in CostCalculator, so cost is real.
        assert record.cost_usd > 0.0

    async def test_an_oversized_transcript_is_refused_before_the_call(
        self, openrouter_settings: FakeLoader
    ) -> None:
        settings = openrouter_settings.override(openrouter_input_token_limit=8)
        completion = FakeCompletion()

        with pytest.raises(NotesGenerationError) as excinfo:
            await generator_for(settings, completion).generate_notes(
                title="t", transcript="a" * 4000
            )

        assert excinfo.value.failure_type is FailureType.TRANSCRIPT_TOO_LARGE
        assert completion.calls == []


# --------------------------------------------------------------------------
# 7. models are configuration: custom endpoints, key rotation, fallback
# --------------------------------------------------------------------------


class TestConfiguredModels:
    def test_a_new_provider_is_only_a_config_entry(self) -> None:
        settings = FakeLoader(
            notes_models=[
                {
                    "model": "openai/gpt-5-mini",
                    "api_base": "http://localhost:20128/v1",
                    "api_key_env": "OMNIROUTE_API_KEY",
                    "input_token_limit": 200_000,
                },
                {"model": "groq/llama-3.3-70b-versatile"},
            ]
        )

        first, second = resolve_model_specs(settings)

        assert (first.provider, first.model) == ("openai", "gpt-5-mini")
        assert first.api_base == "http://localhost:20128/v1"
        assert first.api_key_env == "OMNIROUTE_API_KEY"
        assert first.input_token_limit == 200_000
        assert second.api_key_env == "GROQ_API_KEY"  # default derived from the provider name
        assert second.api_base is None

    @pytest.mark.parametrize("model", ["gpt-5-mini", "", "gemini/"])
    def test_an_entry_without_provider_and_model_is_rejected(self, model: str) -> None:
        with pytest.raises(ValueError, match="notes_models"):
            resolve_model_specs(FakeLoader(notes_models=[{"model": model}]))

    async def test_a_custom_endpoint_is_passed_to_the_provider_call(
        self, gemini_settings: FakeLoader, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OMNIROUTE_API_KEY", "omni-test-key")
        settings = gemini_settings.override(
            notes_models=[
                {
                    "model": "openai/gpt-5-mini",
                    "api_base": "http://localhost:20128/v1",
                    "api_key_env": "OMNIROUTE_API_KEY",
                }
            ]
        )
        completion = FakeCompletion()

        result = await generator_for(settings, completion).generate_notes(
            title="t", transcript="x"
        )

        assert result.provider == "openai"
        sent = completion.calls[0]
        assert sent["model"] == "openai/gpt-5-mini"
        assert sent["api_base"] == "http://localhost:20128/v1"
        assert sent["api_key"] == "omni-test-key"

    async def test_keys_of_one_model_rotate_before_falling_back(
        self, chain_settings: FakeLoader, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GEMINI_API_KEY", "gem-key-1,gem-key-2")
        reset_pools()
        completion = FakeCompletion(RuntimeError("429 Too Many Requests"), GOOD_NOTES)

        result = await generator_for(chain_settings, completion).generate_notes(
            title="t", transcript="x"
        )

        assert result.provider == "gemini"
        assert [call["api_key"] for call in completion.calls] == ["gem-key-1", "gem-key-2"]

    async def test_falls_back_to_the_next_model_when_the_first_is_turned_away(
        self, chain_settings: FakeLoader
    ) -> None:
        completion = FakeCompletion(RuntimeError("503 Service Unavailable"), GOOD_NOTES)
        ledger = RecordingLedger()

        result = await generator_for(
            chain_settings, completion, usage_ledger=ledger
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
        self, chain_settings: FakeLoader
    ) -> None:
        completion = FakeCompletion(RuntimeError("401 Unauthorized"), GOOD_NOTES)

        result = await generator_for(chain_settings, completion).generate_notes(
            title="t", transcript="x"
        )

        assert result.provider == "openrouter"

    async def test_a_model_without_a_key_is_skipped(
        self, chain_settings: FakeLoader, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        completion = FakeCompletion()

        result = await generator_for(chain_settings, completion).generate_notes(
            title="t", transcript="x"
        )

        assert result.provider == "openrouter"
        assert len(completion.calls) == 1

    async def test_a_transcript_too_big_for_one_model_goes_to_a_bigger_one(
        self, gemini_settings: FakeLoader
    ) -> None:
        settings = gemini_settings.override(
            notes_models=[
                {"model": "openrouter/openai/gpt-5-mini", "input_token_limit": 10},
                {"model": "gemini/gemini-3.5-flash", "input_token_limit": 1_000_000},
            ]
        )
        completion = FakeCompletion()

        result = await generator_for(settings, completion).generate_notes(
            title="t", transcript="a" * 4000
        )

        assert result.provider == "gemini"
        assert len(completion.calls) == 1

    async def test_every_model_failing_raises_the_transient_error_so_the_pipeline_retries(
        self, chain_settings: FakeLoader
    ) -> None:
        completion = FakeCompletion(RuntimeError("401 Unauthorized"), RuntimeError("429 Too Many"))

        with pytest.raises(NotesGenerationError) as excinfo:
            await generator_for(chain_settings, completion).generate_notes(
                title="t", transcript="x"
            )

        assert excinfo.value.failure_type is FailureType.RATE_LIMITED
        assert excinfo.value.failure_kind is FailureKind.TRANSIENT

    async def test_every_model_rejecting_its_key_is_a_permanent_config_error(
        self, chain_settings: FakeLoader
    ) -> None:
        completion = FakeCompletion(RuntimeError("401 Unauthorized"))

        with pytest.raises(NotesGeneratorConfigError):
            await generator_for(chain_settings, completion).generate_notes(
                title="t", transcript="x"
            )

        assert len(completion.calls) == 2

    async def test_a_bad_response_does_not_trigger_a_fallback(
        self, chain_settings: FakeLoader
    ) -> None:
        """Only a model that is turned away falls back; its answer is judged as is."""
        completion = FakeCompletion("   ")

        with pytest.raises(NotesGenerationError) as excinfo:
            await generator_for(chain_settings, completion).generate_notes(
                title="t", transcript="x"
            )

        assert excinfo.value.failure_type is FailureType.OUTPUT_MALFORMED
        assert len(completion.calls) == 1

    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            ({"notes_models": [{"model": "nope"}]}, "notes_models"),
            ({"notes_provider": "llama.cpp"}, "llama.cpp"),
        ],
    )
    def test_unusable_configuration_raises_a_clear_error(
        self, gemini_settings: FakeLoader, overrides: dict[str, Any], message: str
    ) -> None:
        with pytest.raises(ValueError, match=message):
            build_notes_generator(gemini_settings.override(**overrides))


# --------------------------------------------------------------------------
# 8. the factory
# --------------------------------------------------------------------------


class TestBuildNotesGenerator:
    def test_defaults_to_gemini(self, gemini_settings: FakeLoader) -> None:
        generator = build_notes_generator(gemini_settings)

        assert isinstance(generator, LiteLLMNotesGenerator)
        assert generator.provider == "gemini"

    def test_openrouter_is_selected_by_legacy_settings(
        self, openrouter_settings: FakeLoader
    ) -> None:
        assert build_notes_generator(openrouter_settings).provider == "openrouter"

    def test_provider_matching_is_forgiving_about_case_and_padding(
        self, openrouter_settings: FakeLoader
    ) -> None:
        settings = openrouter_settings.override(notes_provider=" OpenRouter ")
        assert build_notes_generator(settings).provider == "openrouter"

    def test_notes_models_win_over_the_legacy_provider(self, chain_settings: FakeLoader) -> None:
        generator = build_notes_generator(chain_settings.override(notes_provider="openrouter"))

        assert generator.provider == "gemini"
        assert [spec.litellm_model for spec in generator.specs] == [
            "gemini/gemini-3.5-flash",
            "openrouter/openai/gpt-5-mini",
        ]

    def test_the_real_settings_loader_is_accepted(self) -> None:
        loader = AtlasSettings(notes_provider="gemini").to_loader()
        assert build_notes_generator(loader).provider == "gemini"

    def test_the_real_settings_loader_reads_notes_models(self) -> None:
        loader = AtlasSettings(
            notes_models=[{"model": "openai/gpt-5-mini", "api_base": "http://localhost:20128/v1"}]
        ).to_loader()

        (spec,) = build_notes_generator(loader).specs

        assert spec.api_base == "http://localhost:20128/v1"
        assert spec.api_key_env == "OPENAI_API_KEY"


# --------------------------------------------------------------------------
# 10. secret hygiene
# --------------------------------------------------------------------------

FAKE_KEY = "AIzaSyTESTKEY0000000000000000000000000000"
FAKE_OPENROUTER_KEY = "sk-or-v1-0000000000000000000000000000TEST"


class TestSecretHygiene:
    async def test_the_gemini_key_never_reaches_an_exception_a_log_or_the_ledger(
        self,
        gemini_settings: FakeLoader,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
        # Worst case: the provider echoes the credential back inside its error.
        completion = FakeCompletion(RuntimeError(f"400 invalid key {FAKE_KEY}"))
        ledger = RecordingLedger()
        generator = generator_for(gemini_settings, completion, usage_ledger=ledger)

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
        openrouter_settings: FakeLoader,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_OPENROUTER_KEY)
        completion = FakeCompletion(RuntimeError(f"401 Unauthorized: {FAKE_OPENROUTER_KEY}"))
        ledger = RecordingLedger()
        generator = generator_for(openrouter_settings, completion, usage_ledger=ledger)

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
        chain_settings: FakeLoader,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
        monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_OPENROUTER_KEY)
        transcript = "a very secret transcript line about closures"
        # First model fails, so the fallback path is logged as well.
        completion = FakeCompletion(RuntimeError("503 Service Unavailable"), GOOD_NOTES)

        with caplog.at_level(logging.DEBUG):
            await generator_for(
                chain_settings, completion, usage_ledger=RecordingLedger()
            ).generate_notes(title="t", transcript=transcript)

        assert caplog.text, "expected the adapter to log something at all"
        assert FAKE_KEY not in caplog.text
        assert FAKE_OPENROUTER_KEY not in caplog.text
        assert transcript not in caplog.text

    def test_the_adapter_never_stores_the_key_on_itself(
        self, gemini_settings: FakeLoader, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
        generator = generator_for(gemini_settings, FakeCompletion())

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


def test_notes_generator_module_is_importable_without_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    assert MIN_NOTES_CHARS > 0
    assert Path(notes_base.__file__).name == "notes_base.py"
    assert len(GOOD_NOTES) > MIN_NOTES_CHARS
