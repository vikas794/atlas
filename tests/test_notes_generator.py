"""Tests for the notes generator: port, guardrails, retries, both adapters.

No test in this file touches the network or needs a real key. The SDK boundary
is exactly one seam -- the ``client_factory`` constructor argument on each
adapter -- so every provider call is a double:

* Gemini: ``client.aio.models.generate_content(model=..., contents=...)``
* OpenRouter: ``client.chat.completions.create(model=..., messages=...)``

Settings come from :class:`FakeLoader`, a dict-backed object with the same
``__call__`` shape as ``AtlasSettings.to_loader()``; that keeps assertions
independent of the developer's shell and ``.env``.
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
from src.infrastructure.llm.gemini.notes import GeminiNotesGenerator
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
from src.infrastructure.llm.openai.notes import (
    DEFAULT_OPENROUTER_MODEL,
    OPENROUTER_BASE_URL,
    OpenRouterNotesGenerator,
)

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


class FakeModels:
    """``client.aio.models`` for the Gemini double."""

    def __init__(self, owner: FakeGeminiClient) -> None:
        self._owner = owner

    async def generate_content(self, *, model: str, contents: str) -> Any:
        return await self._owner.respond(model=model, contents=contents)


class FakeGeminiClient:
    """Gemini SDK double: records the call, replays a canned response."""

    def __init__(self, text: str | None = None, *, error: Exception | None = None) -> None:
        self.text = GOOD_NOTES if text is None else text
        self.error = error
        self.calls: list[dict[str, Any]] = []
        self.aio = type("Aio", (), {"models": FakeModels(self)})()

    async def respond(self, *, model: str, contents: str) -> Any:
        self.calls.append({"model": model, "contents": contents})
        if self.error is not None:
            raise self.error
        usage = type(
            "Usage",
            (),
            {"prompt_token_count": 1200, "candidates_token_count": 345, "total_token_count": 1545},
        )()
        return type("Response", (), {"text": self.text, "usage_metadata": usage})()


class FakeCompletions:
    """``client.chat.completions`` for the OpenRouter double."""

    def __init__(self, owner: FakeOpenAIClient) -> None:
        self._owner = owner

    async def create(self, *, model: str, messages: list[dict[str, str]]) -> Any:
        return await self._owner.respond(model=model, messages=messages)


class FakeOpenAIClient:
    """OpenAI SDK double: records the call, replays a canned response."""

    def __init__(self, content: str | None = None, *, error: Exception | None = None) -> None:
        self.content = GOOD_NOTES if content is None else content
        self.error = error
        self.calls: list[dict[str, Any]] = []
        self.chat = type("Chat", (), {"completions": FakeCompletions(self)})()

    async def respond(self, *, model: str, messages: list[dict[str, str]]) -> Any:
        self.calls.append({"model": model, "messages": messages})
        if self.error is not None:
            raise self.error
        usage = type(
            "Usage", (), {"prompt_tokens": 1100, "completion_tokens": 400, "total_tokens": 1500}
        )()
        message = type("Message", (), {"content": self.content})()
        choice = type("Choice", (), {"message": message})()
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
def gemini_settings() -> FakeLoader:
    return FakeLoader(
        notes_provider="gemini",
        notes_model="",
        gemini_model="gemini-2.5-flash",
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
# 5 + 6. no retries inside the adapters
# --------------------------------------------------------------------------

class TestGeminiAdapter:
    async def test_generates_notes_and_returns_the_prompt_version(
        self, gemini_settings: FakeLoader
    ) -> None:
        client = FakeGeminiClient()
        generator = GeminiNotesGenerator(
            gemini_settings, client_factory=lambda: client
        )

        result = await generator.generate_notes(title="Loops", transcript="for x in y: pass")

        assert result.provider == "gemini"
        assert result.model == "gemini-2.5-flash"
        assert result.prompt_version == "notes_v1"
        assert result.content == GOOD_NOTES

        sent = client.calls[0]
        assert sent["model"] == "gemini-2.5-flash"
        assert "Loops" in sent["contents"]
        assert "for x in y: pass" in sent["contents"]

    async def test_notes_model_overrides_gemini_model(self, gemini_settings: FakeLoader) -> None:
        settings = gemini_settings.override(notes_model="gemini-2.5-pro")
        client = FakeGeminiClient()

        result = await GeminiNotesGenerator(settings, client_factory=lambda: client).generate_notes(
            title="t", transcript="x"
        )

        assert result.model == "gemini-2.5-pro"
        assert client.calls[0]["model"] == "gemini-2.5-pro"

    async def test_a_fenced_response_is_unwrapped_end_to_end(
        self, gemini_settings: FakeLoader
    ) -> None:
        fenced = f"{FENCE}markdown\n{GOOD_NOTES}{FENCE}"
        client = FakeGeminiClient(text=fenced)

        result = await GeminiNotesGenerator(
            gemini_settings, client_factory=lambda: client
        ).generate_notes(title="t", transcript="x")

        assert result.content == GOOD_NOTES

    async def test_an_empty_response_is_a_transient_failure_and_is_not_retried_here(
        self, gemini_settings: FakeLoader
    ) -> None:
        """Retry/backoff belongs to ProcessVideoUseCase alone: one call per attempt."""
        client = FakeGeminiClient(text="   ")
        generator = GeminiNotesGenerator(gemini_settings, client_factory=lambda: client)

        with pytest.raises(NotesGenerationError) as excinfo:
            await generator.generate_notes(title="t", transcript="x")

        assert excinfo.value.failure_type is FailureType.OUTPUT_MALFORMED
        assert excinfo.value.failure_kind is FailureKind.TRANSIENT
        assert len(client.calls) == 1

    async def test_an_oversized_transcript_never_reaches_the_provider(
        self, gemini_settings: FakeLoader
    ) -> None:
        settings = gemini_settings.override(gemini_input_token_limit=10)
        client = FakeGeminiClient()

        with pytest.raises(NotesGenerationError) as excinfo:
            await GeminiNotesGenerator(settings, client_factory=lambda: client).generate_notes(
                title="t", transcript="a" * 4000
            )

        assert excinfo.value.failure_type is FailureType.TRANSCRIPT_TOO_LARGE
        assert client.calls == []

    async def test_a_usage_record_is_emitted(self, gemini_settings: FakeLoader) -> None:
        ledger = RecordingLedger()
        client = FakeGeminiClient()

        await GeminiNotesGenerator(
            gemini_settings, usage_ledger=ledger, client_factory=lambda: client
        ).generate_notes(title="t", transcript="x")

        assert len(ledger.records) == 1
        record = ledger.records[0]
        assert record.provider == "gemini"
        assert record.operation == "generate_notes"
        assert record.model == "gemini-2.5-flash"
        assert record.input_tokens == 1200
        assert record.output_tokens == 345
        assert record.total_tokens == 1545
        assert record.cost_usd >= 0.0
        assert record.cache_hit is False

    async def test_missing_token_counts_default_to_zero(
        self, gemini_settings: FakeLoader
    ) -> None:
        ledger = RecordingLedger()
        client = FakeGeminiClient()
        client.respond = _respond_without_usage(client)

        await GeminiNotesGenerator(
            gemini_settings, usage_ledger=ledger, client_factory=lambda: client
        ).generate_notes(title="t", transcript="x")

        record = ledger.records[0]
        assert (record.input_tokens, record.output_tokens, record.total_tokens) == (0, 0, 0)

    async def test_no_ledger_configured_is_fine(self, gemini_settings: FakeLoader) -> None:
        client = FakeGeminiClient()
        result = await GeminiNotesGenerator(
            gemini_settings, client_factory=lambda: client
        ).generate_notes(title="t", transcript="x")
        assert result.content == GOOD_NOTES

    async def test_a_missing_key_names_the_variable_not_the_value(
        self, gemini_settings: FakeLoader, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        with pytest.raises(NotesGeneratorConfigError) as excinfo:
            await GeminiNotesGenerator(gemini_settings).generate_notes(title="t", transcript="x")

        assert "GEMINI_API_KEY" in str(excinfo.value)


class TestOpenRouterAdapter:
    async def test_generates_notes_against_the_openrouter_endpoint(
        self, openrouter_settings: FakeLoader, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-not-a-real-key")
        client = FakeOpenAIClient()

        result = await OpenRouterNotesGenerator(
            openrouter_settings, client_factory=lambda: client
        ).generate_notes(title="Loops", transcript="for x in y: pass")

        assert result.provider == "openrouter"
        assert result.model == DEFAULT_OPENROUTER_MODEL
        assert result.prompt_version == "notes_v1"
        assert result.content == GOOD_NOTES

        sent = client.calls[0]
        assert sent["model"] == DEFAULT_OPENROUTER_MODEL
        assert sent["messages"][0]["role"] == "user"
        assert "for x in y: pass" in sent["messages"][0]["content"]

    async def test_base_url_is_the_openrouter_endpoint(self) -> None:
        assert OPENROUTER_BASE_URL == "https://openrouter.ai/api/v1"

    async def test_notes_model_overrides_the_default(
        self, openrouter_settings: FakeLoader, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-not-a-real-key")
        settings = openrouter_settings.override(notes_model="anthropic/claude-sonnet-4")
        client = FakeOpenAIClient()

        result = await OpenRouterNotesGenerator(
            settings, client_factory=lambda: client
        ).generate_notes(title="t", transcript="x")

        assert result.model == "anthropic/claude-sonnet-4"

    async def test_a_usage_record_is_emitted_with_the_openrouter_cost(
        self, openrouter_settings: FakeLoader, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-not-a-real-key")
        ledger = RecordingLedger()
        client = FakeOpenAIClient()

        await OpenRouterNotesGenerator(
            openrouter_settings, usage_ledger=ledger, client_factory=lambda: client
        ).generate_notes(title="t", transcript="x")

        record = ledger.records[0]
        assert record.provider == "openrouter"
        assert record.operation == "generate_notes"
        assert record.input_tokens == 1100
        assert record.output_tokens == 400
        assert record.total_tokens == 1500
        # The default model is priced in CostCalculator, so cost is real.
        assert record.cost_usd > 0.0

    async def test_a_ledger_failure_does_not_fail_generation(
        self,
        gemini_settings: FakeLoader,
        openrouter_settings: FakeLoader,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")

        cases = (
            (GeminiNotesGenerator, gemini_settings, FakeGeminiClient()),
            (OpenRouterNotesGenerator, openrouter_settings, FakeOpenAIClient()),
        )

        for generator_cls, settings, client in cases:
            generator = generator_cls(
                settings,
                usage_ledger=RecordingLedger(explode=True),
                client_factory=lambda made=client: made,
            )

            result = await generator.generate_notes(title="t", transcript="x")

            assert result.content == GOOD_NOTES
            assert len(client.calls) == 1

    async def test_an_oversized_transcript_is_refused_before_the_call(
        self, openrouter_settings: FakeLoader, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")
        settings = openrouter_settings.override(openrouter_input_token_limit=8)
        client = FakeOpenAIClient()

        with pytest.raises(NotesGenerationError) as excinfo:
            await OpenRouterNotesGenerator(settings, client_factory=lambda: client).generate_notes(
                title="t", transcript="a" * 4000
            )

        assert excinfo.value.failure_type is FailureType.TRANSCRIPT_TOO_LARGE
        assert client.calls == []


# --------------------------------------------------------------------------
# 8. the factory
# --------------------------------------------------------------------------


class TestBuildNotesGenerator:
    def test_defaults_to_gemini(self, gemini_settings: FakeLoader) -> None:
        generator = build_notes_generator(gemini_settings)

        assert isinstance(generator, GeminiNotesGenerator)
        assert generator.provider == "gemini"

    def test_openrouter_is_selected_by_settings(self, openrouter_settings: FakeLoader) -> None:
        generator = build_notes_generator(openrouter_settings)

        assert isinstance(generator, OpenRouterNotesGenerator)
        assert generator.provider == "openrouter"

    def test_provider_matching_is_forgiving_about_case_and_padding(
        self, openrouter_settings: FakeLoader
    ) -> None:
        settings = openrouter_settings.override(notes_provider=" OpenRouter ")
        assert isinstance(build_notes_generator(settings), OpenRouterNotesGenerator)

    def test_an_unknown_provider_raises_a_clear_error(self, gemini_settings: FakeLoader) -> None:
        settings = gemini_settings.override(notes_provider="llama.cpp")

        with pytest.raises(ValueError) as excinfo:
            build_notes_generator(settings)

        message = str(excinfo.value)
        assert "llama.cpp" in message
        assert "gemini" in message and "openrouter" in message

    def test_the_real_settings_loader_is_accepted(self) -> None:
        loader = AtlasSettings(notes_provider="gemini").to_loader()
        assert isinstance(build_notes_generator(loader), GeminiNotesGenerator)

    def test_no_provider_sdk_is_imported_by_the_factory(self) -> None:
        # Constructing an adapter must not import google.genai or openai: the
        # factory is called at wiring time, long before any key is needed.
        generator = build_notes_generator(FakeLoader(notes_provider="openrouter"))
        assert generator.provider == "openrouter"


def _respond_without_usage(client: FakeGeminiClient):
    async def respond(*, model: str, contents: str) -> Any:
        client.calls.append({"model": model, "contents": contents})
        if client.error is not None:
            raise client.error
        return type("Response", (), {"text": client.text, "usage_metadata": None})()

    return respond


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
        client = FakeGeminiClient(error=RuntimeError(f"400 invalid key {FAKE_KEY}"))
        ledger = RecordingLedger()
        generator = GeminiNotesGenerator(
            gemini_settings, usage_ledger=ledger, client_factory=lambda: client
        )

        with caplog.at_level(logging.DEBUG), pytest.raises(NotesGenerationError) as excinfo:
            await generator.generate_notes(title="t", transcript="a secret transcript line")

        error = excinfo.value
        assert FAKE_KEY not in str(error)
        assert FAKE_KEY not in (error.diagnostic or "")
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
        client = FakeOpenAIClient(error=RuntimeError(f"401 Unauthorized: {FAKE_OPENROUTER_KEY}"))
        ledger = RecordingLedger()
        generator = OpenRouterNotesGenerator(
            openrouter_settings, usage_ledger=ledger, client_factory=lambda: client
        )

        with caplog.at_level(logging.DEBUG), pytest.raises(NotesGenerationError) as excinfo:
            await generator.generate_notes(title="t", transcript="a secret transcript line")

        error = excinfo.value
        assert FAKE_OPENROUTER_KEY not in str(error)
        assert FAKE_OPENROUTER_KEY not in (error.diagnostic or "")
        assert FAKE_OPENROUTER_KEY not in caplog.text
        for record in ledger.records:
            assert FAKE_OPENROUTER_KEY not in repr(asdict(record))

    async def test_a_successful_call_logs_neither_key_nor_transcript(
        self,
        gemini_settings: FakeLoader,
        openrouter_settings: FakeLoader,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
        monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_OPENROUTER_KEY)
        transcript = "a very secret transcript line about closures"

        with caplog.at_level(logging.DEBUG):
            await GeminiNotesGenerator(
                gemini_settings, usage_ledger=RecordingLedger(), client_factory=FakeGeminiClient
            ).generate_notes(title="t", transcript=transcript)
            await OpenRouterNotesGenerator(
                openrouter_settings, usage_ledger=RecordingLedger(), client_factory=FakeOpenAIClient
            ).generate_notes(title="t", transcript=transcript)

        assert caplog.text, "expected the adapters to log something at all"
        assert FAKE_KEY not in caplog.text
        assert FAKE_OPENROUTER_KEY not in caplog.text
        assert transcript not in caplog.text

    def test_an_adapter_never_stores_the_key_on_itself(
        self, gemini_settings: FakeLoader, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
        generator = GeminiNotesGenerator(gemini_settings, client_factory=FakeGeminiClient)

        assert FAKE_KEY not in repr(vars(generator))

    def test_importing_the_llm_package_does_not_import_a_provider_sdk(self) -> None:
        """`import src.infrastructure.llm` must stay free of provider SDKs.

        Run in a clean interpreter: the test process will have imported the SDK
        for other tests, which would hide a module-level import regression.
        """
        script = (
            "import sys; import src.infrastructure.llm as m; "
            "leaked = [n for n in ('google.genai', 'openai') if n in sys.modules]; "
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
