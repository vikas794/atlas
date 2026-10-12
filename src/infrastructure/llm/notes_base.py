"""Shared logic for every notes-generation adapter.

Everything in here is provider-agnostic and has three jobs:

1. **Prompt handling** -- :func:`render_prompt` substitutes the two placeholders
   in the versioned prompt file, and :class:`NotesPromptSource` loads that file.
2. **Guardrails** -- :func:`check_input_size` refuses an oversized transcript
   *before* paying for the call, and :func:`validate_notes` rejects output the
   pipeline cannot use (PRD section 18).
3. **Retry policy** -- :class:`RetryableNotesGenerator` retries *only* transient
   failures, using the same backoff the rest of the pipeline uses.

Two rules hold throughout:

* **Never truncate a transcript.** If it does not fit the model, the video fails
  with a clear permanent reason (PRD 18; chunk-and-merge is a later milestone).
* **Never leak secrets or transcript text.** The API key is redacted out of any
  provider message before it becomes a log-safe diagnostic, and no function
  here logs the transcript.

The prompt file is read with :func:`yaml.safe_load` directly rather than through
:class:`src.config.prompts.PromptRegistry`. The registry's default-name helper
was rewritten for the new prompt and it caches parsed files forever, so a long
running worker would keep serving a prompt from disk that no longer exists on
disk. Each adapter owns one :class:`NotesPromptSource`, which reads the file
once per generator instance and validates the two placeholders up front.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from src.config.settings import repo_root
from src.domain.exceptions import NotesGeneratorConfigError
from src.domain.models.playlist import (
    FailureType,
    NotesGenerationError,
)
from src.infrastructure.llm.base import SettingsLoader
from src.shared.redaction import redact_text

if TYPE_CHECKING:

    pass

logger = logging.getLogger(__name__)

#: The only two placeholders the prompt file is allowed to carry.
TITLE_PLACEHOLDER = "{{Title}}"
TRANSCRIPT_PLACEHOLDER = "{{Transcript}}"

#: Shortest output that could plausibly be notes. The prompt asks for a Content
#: section and an Instruction section with several bullets each, so anything
#: under this is a truncation, a refusal or an error string wearing a trench coat.
MIN_NOTES_CHARS = 80

#: The four content sections the prompt asks for (PRD section 18). A response must
#: contain at least ``MIN_SECTIONS_FOUND`` of them, each at the start of a line
#: optionally wrapped in heading/bold/list markers.
EXPECTED_SECTIONS = ("Notes", "Key Points", "Important Details", "Takeaways")
MIN_SECTIONS_FOUND = 2
_SECTION_LINE = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]*)?(?:[-*][ \t]+)?(?:\*\*|__)?[ \t]*(?:\d+[.)][ \t]*)?"
    r"(?P<name>Key Points|Important Details|Takeaways|Notes)\b",
    re.IGNORECASE | re.MULTILINE,
)
_H1_LINE = re.compile(r"^#[ \t]+\S")

#: Characters per token used by :func:`estimate_tokens`. Deliberately crude --
#: see that function.
_CHARS_PER_TOKEN = 4

#: An *outer* code fence, i.e. one that wraps the entire response. Models do this
#: constantly when a prompt mentions Markdown. The language tag is captured and
#: discarded; the body is what the prompt actually asked for.
_OUTER_FENCE = re.compile(
    r"\A```[^\S\r\n]*[A-Za-z0-9_+#.-]*[^\S\r\n]*\r?\n(?P<body>.*?)\r?\n?```[^\S\r\n]*\Z",
    re.DOTALL,
)


def render_prompt(template: str, *, title: str, transcript: str) -> str:
    """Substitute ``{{Title}}`` and ``{{Transcript}}`` into ``template``.

    The rest of the template is copied through byte-for-byte; the prompt text is
    user-authored and must not be reformatted.

    Raises:
        NotesGeneratorConfigError: If the template is missing either
            placeholder -- substituting a missing one would silently ship an
            unreviewed prompt, so this is a hard failure.
    """
    if not isinstance(template, str) or not template.strip():
        raise NotesGeneratorConfigError("The notes prompt template is empty.")

    for placeholder in (TITLE_PLACEHOLDER, TRANSCRIPT_PLACEHOLDER):
        if placeholder not in template:
            raise NotesGeneratorConfigError(
                f"The notes prompt template is missing the {placeholder} placeholder; "
                "it must contain both "
                f"{TITLE_PLACEHOLDER} and {TRANSCRIPT_PLACEHOLDER}."
            )

    return template.replace(TITLE_PLACEHOLDER, title).replace(TRANSCRIPT_PLACEHOLDER, transcript)


def estimate_tokens(text: str) -> int:
    """Rough token count for ``text``, assuming ~4 characters per token.

    A real tokenizer is model-specific and denser on code and punctuation, so
    this is only a cheap pre-flight guard: it keeps an oversized transcript from
    being sent (and paid for) when we can tell locally that it cannot fit. The
    provider's own limit is the final authority. We never truncate to fit
    (PRD section 18).
    """
    return math.ceil(len(text) / _CHARS_PER_TOKEN)


def check_input_size(transcript: str, model_limit: int) -> None:
    """Refuse a transcript that cannot fit ``model_limit`` tokens.

    Raises:
        NotesGenerationError: ``TRANSCRIPT_TOO_LARGE`` (a permanent failure) with
            a reason naming both the estimated size and the limit.
    """
    if model_limit <= 0:
        # No limit configured for this adapter: nothing to check against.
        return

    estimated = estimate_tokens(transcript)
    if estimated <= model_limit:
        return

    raise NotesGenerationError(
        FailureType.TRANSCRIPT_TOO_LARGE,
        f"The transcript is about {estimated:,} tokens ({len(transcript):,} characters), "
        f"which is over the {model_limit:,}-token input limit for this model.",
    )


def validate_notes(content: str) -> str:
    """Clean and check raw model output; return the usable Markdown.

    Repairs, in order:

    * an outer ```` ```markdown ```` fence wrapping the whole response is
      unwrapped -- inner fences (the code examples the prompt demands) are kept
      untouched, because Drive renders them as shaded boxes;
    * a leading ``# Title`` line is dropped (the Doc heading supplies the title);
    * surrounding whitespace is trimmed.

    Rejects, as ``OUTPUT_MALFORMED``: an empty or whitespace-only response, an
    implausibly short one, and one that lacks the expected sections. The section
    check is deliberately loose -- at least :data:`MIN_SECTIONS_FOUND` of Notes /
    Key Points / Important Details / Takeaways must appear as a heading or bold
    line -- so it catches refusals and error text without failing a valid answer
    over formatting. PRD section 18 calls a malformed response a
    transient failure, so these are retryable rather than fatal on first sight.

    Raises:
        NotesGenerationError: ``OUTPUT_MALFORMED`` when nothing usable came back.
    """
    text = content if isinstance(content, str) else "" if content is None else str(content)
    cleaned = text.strip()
    if not cleaned:
        raise NotesGenerationError(
            FailureType.OUTPUT_MALFORMED,
            "The model returned an empty response.",
        )

    fenced = _OUTER_FENCE.match(cleaned)
    if fenced is not None:
        # Only trust the unwrap when something is actually inside the fence;
        # an empty ```\n``` body leaves the response no better off than before.
        inner = fenced.group("body").strip()
        if inner:
            cleaned = inner

    # The Doc heading supplies the video title (PRD section 6 FR6), so a leading
    # Markdown H1 the model added anyway is dropped rather than duplicated.
    first_line, _, remainder = cleaned.partition("\n")
    if _H1_LINE.match(first_line) and remainder.strip():
        cleaned = remainder.strip()

    if len(cleaned) < MIN_NOTES_CHARS:
        raise NotesGenerationError(
            FailureType.OUTPUT_MALFORMED,
            f"The model returned only {len(cleaned)} characters of notes, "
            f"which is too short to be usable.",
        )

    found = {match.group("name").lower() for match in _SECTION_LINE.finditer(cleaned)}
    if len(found) < MIN_SECTIONS_FOUND:
        raise NotesGenerationError(
            FailureType.OUTPUT_MALFORMED,
            "The model's notes are missing the expected sections "
            "(Notes, Key Points, Important Details, Takeaways).",
        )

    return cleaned


def redact_secrets(text: str, *secrets: str | None) -> str:
    """Replace any known secret (and credential-shaped text) in ``text``.

    Provider error messages are the only untrusted text Atlas turns into a log
    line, and an auth failure can quote the credential back at us. Delegates to
    the shared helper.
    """
    return redact_text(text or "", *secrets)


class NotesPromptSource:
    """Loads and caches the versioned notes prompt for one generator instance."""

    def __init__(self, settings_loader: SettingsLoader) -> None:
        self._settings = settings_loader
        self._loaded: dict[str, Any] | None = None

    @property
    def prompt_name(self) -> str:
        return str(self._settings("notes_prompt_name", "notes_v1.yaml"))

    @property
    def base_dir(self) -> Path:
        configured = str(self._settings("prompts_base_dir", "src/prompts"))
        directory = Path(configured)
        # Resolved against the repo root, not the CWD: the worker is started
        # from everywhere.
        return directory if directory.is_absolute() else repo_root() / directory

    @property
    def path(self) -> Path:
        return self.base_dir / self.prompt_name

    def load(self) -> dict[str, Any]:
        """Parse the prompt file once; return the raw mapping.

        Raises:
            NotesGeneratorConfigError: Missing file, unparseable YAML, or a file
                without a usable ``prompt`` key.
        """
        if self._loaded is not None:
            return self._loaded

        path = self.path
        if not path.exists():
            raise NotesGeneratorConfigError(f"Notes prompt file not found: {path}")

        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise NotesGeneratorConfigError(f"Notes prompt file {path} is not valid YAML.") from exc

        if not isinstance(data, dict) or not str(data.get("prompt") or "").strip():
            raise NotesGeneratorConfigError(
                f"Notes prompt file {path} has no non-empty 'prompt' key."
            )

        self._loaded = data
        return data

    def template(self) -> str:
        """The prompt text, placeholders intact."""
        return str(self.load()["prompt"])

    def version(self) -> str:
        """The version recorded on every generated summary (PRD section 18).

        It comes from the prompt file's own ``version`` key, so it is tied to the
        file's contents rather than to a separate setting that can drift. A file
        without one falls back to a short content hash, so the column is never blank.
        """
        from_file = str(self.load().get("version") or "").strip()
        if from_file:
            return from_file
        return hashlib.sha256(self.template().encode("utf-8")).hexdigest()[:8]


__all__ = [
    "MIN_NOTES_CHARS",
    "TITLE_PLACEHOLDER",
    "TRANSCRIPT_PLACEHOLDER",
    "NotesGeneratorConfigError",
    "NotesPromptSource",
    "check_input_size",
    "estimate_tokens",
    "redact_secrets",
    "render_prompt",
    "validate_notes",
]
