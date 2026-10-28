"""Shared logic for every notes-generation adapter.

Everything in here is provider-agnostic and has three jobs (retries are *not* one of
them: ``ProcessVideoUseCase`` is the single owner of retry and backoff):

1. **Prompt handling** -- :func:`render_prompt` substitutes the two placeholders
   in the versioned prompt file, and :class:`NotesPromptSource` loads that file.
2. **Guardrails** -- :func:`check_input_size` refuses an oversized transcript
   *before* paying for the call, and :func:`validate_notes` rejects output the
   pipeline cannot use (PRD section 18).
3. **Failure classification** -- :func:`classify_provider_exception` and
   :func:`check_finish_reason` turn SDK errors and abnormal stops into the PRD
   section 8 taxonomy, so a bad key fails permanently instead of being retried.

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
from typing import Any

import yaml

from src.config.settings import repo_root
from src.domain.exceptions import NotesGeneratorConfigError
from src.domain.models.playlist import (
    FailureType,
    NotesGenerationError,
)
from src.domain.services.failure_classifier import safe_diagnostic
from src.infrastructure.llm.base import SettingsLoader
from src.shared.redaction import redact_text

logger = logging.getLogger(__name__)


#: ``api_key_env`` prefix marking a key that comes from the model registry, not from ``.env``.
REGISTRY_KEY_PREFIX = "registry:"

#: The only two placeholders the prompt file is allowed to carry.
TITLE_PLACEHOLDER = "{{Title}}"
TRANSCRIPT_PLACEHOLDER = "{{Transcript}}"
_PLACEHOLDER_PATTERN = re.compile(
    "|".join(re.escape(p) for p in (TITLE_PLACEHOLDER, TRANSCRIPT_PLACEHOLDER))
)

#: Shortest output that could plausibly be notes. The prompt asks for a Content
#: section and an Instruction section with several bullets each, so anything
#: under this is a truncation, a refusal or an error string wearing a trench coat.
MIN_NOTES_CHARS = 80

#: The content sections the prompts ask for (PRD section 18): ``notes_v1_3`` writes
#: Questions and Answers / Notes / History, earlier prompts Notes / Key Points /
#: Important Details / Takeaways. A response must contain at least
#: ``MIN_SECTIONS_FOUND`` of them, each at the start of a line optionally wrapped in
#: heading/bold/list markers.
EXPECTED_SECTIONS = (
    "Questions and Answers",
    "Notes",
    "History",
    "Key Points",
    "Important Details",
    "Takeaways",
)
MIN_SECTIONS_FOUND = 2
_SECTION_LINE = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]*)?(?:[-*][ \t]+)?(?:\*\*|__)?[ \t]*(?:\d+[.)][ \t]*)?"
    r"(?P<name>Questions and Answers|Key Points|Important Details|Takeaways|Notes|History)\b",
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

    # One pass over the template only: chained str.replace would also substitute a
    # placeholder that appears *inside* the (untrusted) title, e.g. a YouTube title
    # containing "{{Transcript}}" would paste the whole transcript into the prompt twice.
    values = {TITLE_PLACEHOLDER: title, TRANSCRIPT_PLACEHOLDER: transcript}
    return _PLACEHOLDER_PATTERN.sub(lambda match: values[match.group(0)], template)


#: Per-request timeout when ``notes_request_timeout_seconds`` is unset. Both SDKs
#: would otherwise wait far too long (Gemini: forever), holding a worker slot and
#: leaving the video in ``generating_notes``; long transcripts legitimately take minutes.
DEFAULT_REQUEST_TIMEOUT_SECONDS = 300.0


def request_timeout_seconds(settings: SettingsLoader) -> float:
    """The configured per-call LLM timeout, in seconds."""
    raw = settings("notes_request_timeout_seconds", DEFAULT_REQUEST_TIMEOUT_SECONDS)
    return float(raw or DEFAULT_REQUEST_TIMEOUT_SECONDS)


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
    check is deliberately loose -- at least :data:`MIN_SECTIONS_FOUND` of
    :data:`EXPECTED_SECTIONS` must appear as a heading or bold
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
            "(Questions and Answers, Notes, History).",
        )

    return cleaned


#: Provider text meaning "your credential is wrong", whatever HTTP status carried it.
#: Gemini answers an invalid key with HTTP 400 ``API_KEY_INVALID``, not 401.
_AUTH_TEXT = re.compile(
    r"api[ _]?key[ _]?(?:not[ _]valid|invalid|expired)|invalid[ _]api[ _]?key"
    r"|\bunauthori[sz]ed\b|\bunauthenticated\b|permission[ _]denied|\bforbidden\b"
    r"|incorrect api key|no auth credentials",
    re.IGNORECASE,
)
#: Provider text meaning "the request (prompt) is larger than the model accepts".
_TOO_LARGE_TEXT = re.compile(
    r"exceeds the maximum number of tokens|input token count|maximum context length"
    r"|context[ _]length[ _]exceeded|context window|too many tokens|prompt is too long",
    re.IGNORECASE,
)
#: Provider text meaning "a content filter refused this request".
_BLOCKED_TEXT = re.compile(r"\bflagged\b|moderation|\bsafety\b|content[ _]filter", re.IGNORECASE)
_RATE_TEXT = re.compile(
    r"\b429\b|too many requests|rate[ _-]?limit|resource[ _]exhausted|quota", re.IGNORECASE
)
_NETWORK_TEXT = re.compile(
    r"timed? ?out|timeout|connection|connect error|name resolution|getaddrinfo"
    r"|network|temporarily unreachable|\bssl\b|remote ?protocol",
    re.IGNORECASE,
)
#: Exception class names (anywhere in the MRO) that are network trouble, not provider answers.
_NETWORK_EXCEPTION_NAMES = frozenset(
    {
        "TimeoutError",
        "TimeoutException",
        "ConnectError",
        "ConnectTimeout",
        "ReadTimeout",
        "NetworkError",
        "RemoteProtocolError",
        "APIConnectionError",
        "APITimeoutError",
        "ConnectionError",
        "Timeout",  # litellm.Timeout
    }
)


def _status_code(exc: BaseException) -> int | None:
    """HTTP status carried by an SDK exception, if any.

    ``google.genai.errors.APIError`` exposes ``code``; ``openai.APIStatusError`` exposes
    ``status_code``. Anything else (a network error, a plain ``RuntimeError``) has none.
    """
    for attribute in ("status_code", "code"):
        value = getattr(exc, attribute, None)
        if isinstance(value, int) and 100 <= value <= 599:
            return value
    return None


def classify_provider_exception(
    exc: BaseException,
    *,
    provider_label: str,
    api_key_env: str,
    model: str,
    secrets: tuple[str | None, ...] = (),
) -> Exception:
    """Map an LLM SDK exception onto Atlas' failure taxonomy (PRD sections 8 and 18).

    Returns (never raises) the exception the adapter must raise:

    * :class:`NotesGeneratorConfigError` -- a rejected/unauthorised key (401/403, or
      Gemini's HTTP 400 ``API_KEY_INVALID``), an unknown model (404) or exhausted
      credits (402). No retry can fix these, so the pipeline records a permanent
      ``config_error`` instead of burning the retry budget.
    * ``TRANSCRIPT_TOO_LARGE`` (permanent) -- the provider says the prompt does not fit.
    * ``RATE_LIMITED`` / ``NETWORK_ERROR`` / ``PROVIDER_ERROR`` (transient) -- 429,
      connection trouble and 5xx (or anything unrecognised), retried with backoff.
    * ``OUTPUT_MALFORMED`` (transient, PRD 18) -- a content filter refused the request.

    The user-facing reason names the provider and status but never contains provider
    text; the redacted first line of that text travels only as ``diagnostic``.
    """
    raw = str(exc) or type(exc).__name__
    diagnostic = safe_diagnostic(redact_text(raw, *secrets))
    status = _status_code(exc)
    shown = f" (HTTP {status})" if status else ""
    mro_names = {cls.__name__ for cls in type(exc).__mro__}

    def failure(failure_type: FailureType, reason: str) -> NotesGenerationError:
        return NotesGenerationError(failure_type, reason, diagnostic=diagnostic)

    def config_error(message: str, key_problem: str | None = None) -> NotesGeneratorConfigError:
        error = NotesGeneratorConfigError(message)
        error.diagnostic = diagnostic  # type: ignore[attr-defined]  # log-only, redacted
        error.key_problem = key_problem  # type: ignore[attr-defined]  # see _key_problem_of
        return error

    # A key from the model registry is named "registry:<label>"; an env var name is a .env entry.
    from_registry = api_key_env.startswith(REGISTRY_KEY_PREFIX)
    key_name = (
        f"the key '{api_key_env[len(REGISTRY_KEY_PREFIX):]}' (Keys page)"
        if from_registry
        else api_key_env
    )

    if (status in (401, 403) and not _BLOCKED_TEXT.search(raw)) or _AUTH_TEXT.search(raw):
        where = "" if from_registry else " in .env"
        return config_error(
            f"{provider_label} rejected the API key{shown}. Check that {key_name}{where} "
            "holds a valid key with access to this model.",
            key_problem="rejected",
        )
    if status == 402:
        return config_error(
            f"{provider_label} refused the request for lack of credits{shown}; top up the "
            f"account behind {key_name}.",
            key_problem="credits",
        )
    if status == 404:
        return config_error(
            f"{provider_label} does not know the model {model!r}{shown}. "
            "Check the model id on the Models page."
        )
    if "ContextWindowExceededError" in mro_names or _TOO_LARGE_TEXT.search(raw):
        return failure(
            FailureType.TRANSCRIPT_TOO_LARGE,
            f"The transcript is too large for {model}: {provider_label} rejected the "
            "prompt as over its input limit.",
        )
    if status == 429 or (status is None and _RATE_TEXT.search(raw)):
        return failure(
            FailureType.RATE_LIMITED,
            f"{provider_label} is rate-limiting requests{shown}.",
        )
    # By exception type first: LiteLLM gives its connection errors a 500 status code, which
    # would otherwise read as a provider outage.
    if mro_names & _NETWORK_EXCEPTION_NAMES or (status is None and _NETWORK_TEXT.search(raw)):
        return failure(
            FailureType.NETWORK_ERROR,
            f"A network error interrupted the request to {provider_label}.",
        )
    if status is not None and status >= 500:
        return failure(
            FailureType.PROVIDER_ERROR,
            f"{provider_label} returned a server error{shown}.",
        )
    if "ContentPolicyViolationError" in mro_names or (
        status is not None and _BLOCKED_TEXT.search(raw)
    ):
        return failure(
            FailureType.OUTPUT_MALFORMED,
            f"{provider_label} refused to generate notes for this transcript (content filter).",
        )
    return failure(
        FailureType.PROVIDER_ERROR,
        f"{provider_label} returned an error{shown}.",
    )


def error_category_of(error: BaseException) -> str:
    """Short analytics label for a classified failure: key problem, failure type or ``error``."""
    problem = _key_problem_of(error)
    if problem:
        return problem
    if isinstance(error, NotesGenerationError):
        return str(error.failure_type.value)
    return "config_error" if isinstance(error, NotesGeneratorConfigError) else "error"


def _key_problem_of(error: BaseException) -> str | None:
    """Whether a classified error is about the API key in use, not about the request.

    ``"rejected"`` / ``"credits"`` (permanent for that key) or ``"rate_limited"`` (temporary);
    None for anything another key could not fix (unknown model, oversized prompt, 5xx ...).
    """
    declared = getattr(error, "key_problem", None)
    if declared:
        return str(declared)
    if isinstance(error, NotesGenerationError) and error.failure_type is FailureType.RATE_LIMITED:
        return "rate_limited"
    return None


def check_finish_reason(finish_reason: object, *, provider_label: str) -> None:
    """Refuse a response the model did not finish normally (PRD section 18).

    A response cut off at the output limit can still be long enough to pass
    :func:`validate_notes`, so it is checked explicitly instead of being shipped
    half-written. A safety/content-filter stop is reported as such. Both are
    ``OUTPUT_MALFORMED`` (transient, within the retry limit).

    Accepts Gemini's ``FinishReason`` enum (``MAX_TOKENS``, ``SAFETY`` ...) and
    OpenAI-style strings (``length``, ``content_filter``); ``None`` means unknown.
    """
    if finish_reason is None:
        return
    value = str(getattr(finish_reason, "value", finish_reason) or "").strip().upper()
    if value in {"MAX_TOKENS", "LENGTH"}:
        raise NotesGenerationError(
            FailureType.OUTPUT_MALFORMED,
            f"{provider_label} stopped at its output limit, so the notes were cut off.",
            diagnostic=f"finish_reason={value}",
        )
    if value in {
        "SAFETY",
        "RECITATION",
        "BLOCKLIST",
        "PROHIBITED_CONTENT",
        "SPII",
        "IMAGE_SAFETY",
        "CONTENT_FILTER",
    }:
        raise NotesGenerationError(
            FailureType.OUTPUT_MALFORMED,
            f"{provider_label} blocked the response ({value.lower()}), so no notes came back.",
            diagnostic=f"finish_reason={value}",
        )


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
        return str(self._settings("notes_prompt_name", "notes_v1_2.yaml"))

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
    "REGISTRY_KEY_PREFIX",
    "TITLE_PLACEHOLDER",
    "TRANSCRIPT_PLACEHOLDER",
    "NotesGeneratorConfigError",
    "check_finish_reason",
    "classify_provider_exception",
    "NotesPromptSource",
    "check_input_size",
    "estimate_tokens",
    "redact_secrets",
    "render_prompt",
    "validate_notes",
]
