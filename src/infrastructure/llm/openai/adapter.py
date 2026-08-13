"""OpenAI-backed implementations of the domain LLM ports.

Three adapters are provided, each implementing one domain ``Port``:

* :class:`OpenAISummarizerAdapter` — :class:`SummarizerPort`
* :class:`OpenAIInsightsProvider` — :class:`InsightsProviderPort`
* :class:`OpenAIAssignmentAdapter` — :class:`AssignmentGeneratorPort`

All three share an underlying :class:`OpenAIRetryableProvider` for
transport + retry semantics and emit a :class:`UsageRecord` for every
call (success or failure) so the usage ledger has a complete view.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.domain.interfaces.assignment_generator import (
    AssignmentGeneratorPort,
    AssignmentResult,
)
from src.domain.interfaces.insights_provider import InsightsProviderPort, InsightsResult
from src.domain.interfaces.summarizer import (
    SummarizerPort,
    SummaryContext,
    SummaryResult,
)
from src.domain.interfaces.usage_ledger import UsageRecord
from src.infrastructure.llm.base import (
    LLMProviderError,
    LLMResponse,
    RetryPolicy,
    SettingsLoader,
    TokenUsage,
    UsageLedgerSink,
)
from src.infrastructure.llm.cost import calculate_cost
from src.infrastructure.llm.openai.retry import OpenAIRetryableProvider
from src.utils import get_config, get_prompt_path, sha256_text

logger = logging.getLogger(__name__)

_DEFAULT_RETRY_POLICY = RetryPolicy(
    max_retries=3,
    initial_delay=1.0,
    max_delay=30.0,
    exponential_base=2.0,
    jitter=True,
)


def _default_retry_policy_from_config(settings: SettingsLoader) -> RetryPolicy:
    """Resolve a :class:`RetryPolicy` from the application config.

    Falls back to a sane default when individual fields are missing,
    so partial config drift does not break callers.
    """
    try:
        max_retries = int(settings("api.openai.max_retries", 3))
    except (TypeError, ValueError):
        max_retries = 3
    return RetryPolicy(
        max_retries=max_retries,
        initial_delay=1.0,
        max_delay=30.0,
        exponential_base=2.0,
        jitter=True,
    )


def _load_prompt_template(prompt_name: str | None) -> str:
    """Load a prompt YAML file and return its ``prompt`` field.

    Centralizing prompt loading keeps adapters from each re-implementing
    the path resolution + YAML parse dance.
    """
    import yaml

    path = get_prompt_path(prompt_name)
    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if "prompt" not in data:
        raise KeyError(f"Prompt file {path!r} missing required 'prompt' key")
    return data["prompt"]


def _load_assignment_prompts(prompt_name: str | None) -> dict[str, str]:
    """Load both ``system_prompt`` and ``user_prompt_template`` fields."""
    import yaml

    path = get_prompt_path(prompt_name or "assignment_generator.yaml")
    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    for key in ("system_prompt", "user_prompt_template"):
        if key not in data:
            raise KeyError(f"Prompt file {path!r} missing required {key!r} key")
    return {"system_prompt": data["system_prompt"], "user_prompt_template": data["user_prompt_template"]}


def _resolve_api_credentials(settings: SettingsLoader) -> tuple[str | None, str | None]:
    """Resolve API key + base URL from config and environment.

    The OpenRouter base URL is the project's default; callers can
    override via ``api.openai.base_url`` in config or by passing
    explicit values to the constructor.
    """
    api_key_env = settings("environment.openrouter_api_key_env", "OPENROUTER_API_KEY")
    api_key = os.getenv(str(api_key_env)) if api_key_env else None
    base_url = settings("api.openai.base_url", "https://openrouter.ai/api/v1")
    return api_key, str(base_url) if base_url else None


def _zero_usage() -> TokenUsage:
    return TokenUsage(input_tokens=0, output_tokens=0, total_tokens=0)


def _build_usage_record(
    *,
    provider: str,
    operation: str,
    model: str,
    response: LLMResponse | None,
    success: bool,
    error_category: str | None,
    retry_count: int,
    cache_hit: bool = False,
    run_id: str | None = None,
    video_id: str | None = None,
) -> UsageRecord:
    """Build a :class:`UsageRecord` from a call attempt.

    On failure ``token_usage`` is zeroed (we have no tokens to bill for
    when the request never completed) and ``cost_usd`` is calculated
    via the CostCalculator based on actual token usage.
    """
    usage = response.token_usage if response is not None and response.token_usage else _zero_usage()
    cost_usd = calculate_cost(
        provider=provider,
        model=model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
    )
    return UsageRecord(
        timestamp=datetime.now(datetime.UTC),
        provider=provider,
        operation=operation,
        model=model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
        cost_usd=cost_usd,
        cache_hit=cache_hit,
        run_id=run_id,
        video_id=video_id,
    )


# ---------------------------------------------------------------------------
# Summarizer adapter
# ---------------------------------------------------------------------------


@dataclass
class _SummarizerInternals:
    """Bag of state shared by the summarizer's batch implementation."""

    provider: OpenAIRetryableProvider
    settings: SettingsLoader
    prompt_name: str
    model: str
    timeout: int


class OpenAISummarizerAdapter(SummarizerPort):
    """Adapter that satisfies :class:`SummarizerPort` using OpenAI."""

    def __init__(
        self,
        *,
        settings: SettingsLoader | None = None,
        usage_ledger: UsageLedgerSink | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        retry_policy: RetryPolicy | None = None,
        prompt_name: str | None = None,
        model: str | None = None,
        timeout: int | None = None,
        run_id: str | None = None,
    ) -> None:
        self._settings = settings or get_config
        api_key_resolved, base_url_resolved = _resolve_api_credentials(self._settings)
        self._run_id = run_id
        self._prompt_name = prompt_name
        self._model = model or str(
            self._settings("api.openai.model", "openai/gpt-5-mini")
        )
        self._timeout = int(
            timeout if timeout is not None else self._settings("api.openai.timeout", 180)
        )
        policy = retry_policy or _default_retry_policy_from_config(self._settings)
        self._provider = OpenAIRetryableProvider(
            settings=self._settings,
            api_key=api_key or api_key_resolved,
            base_url=base_url or base_url_resolved,
            retry_policy=policy,
            usage_ledger=usage_ledger,
        )

    async def summarize(self, context: SummaryContext) -> SummaryResult:
        """Generate a summary for one transcript."""
        system_prompt = _load_prompt_template(self._prompt_name)
        user_message = self._build_user_message(context)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
        return await self._summarize_one(
            context=context,
            messages=messages,
        )

    async def summarize_batch(
        self, contexts: list[SummaryContext]
    ) -> list[SummaryResult]:
        """Generate summaries for many transcripts.

        The OpenAI SDK call itself is synchronous; concurrency is left
        to the caller (typically a :class:`ThreadPoolExecutor`) so we
        do not pull in an async runtime dependency here.
        """
        import asyncio

        return await asyncio.gather(*(self.summarize(ctx) for ctx in contexts))

    async def _summarize_one(
        self,
        *,
        context: SummaryContext,
        messages: list[dict],
    ) -> SummaryResult:
        attempt_index = 0
        retry_count = 0
        error_category: str | None = None
        response: LLMResponse | None = None
        try:
            response = self._provider.complete(
                messages=messages,
                model=context.model or self._model,
                timeout=self._timeout,
            )
            attempt_index += 1
            data = self._parse_summary_response(response.content)
            content_hash = sha256_text(response.content)
            byte_size = len(response.content.encode("utf-8"))
            return SummaryResult(
                video_id=context.video_id,
                summary_data=data,
                content_hash=content_hash,
                artifact_path="",
                byte_size=byte_size,
                prompt_version=context.prompt_version,
                model=response.model,
            )
        except LLMProviderError as exc:
            retry_count = self._retry_count_for_last_call(self._provider, exc)
            error_category = "provider_error"
            raise
        except json.JSONDecodeError as exc:
            error_category = "json_parse_error"
            raise LLMProviderError(f"summary parse failure: {exc}") from exc
        finally:
            self._provider._emit_usage(
                _build_usage_record(
                    provider="openai",
                    operation="summarize",
                    model=(response.model if response else (context.model or self._model)),
                    response=response,
                    success=error_category is None,
                    error_category=error_category,
                    retry_count=retry_count,
                    run_id=self._run_id,
                    video_id=context.video_id,
                )
            )

    def _build_user_message(self, context: SummaryContext) -> str:
        return (
            f"**YouTube Video Title:** {context.title or 'Not provided'}\n\n"
            f"**Channel:** {context.channel or 'Not provided'}\n\n"
            f"**Full Transcript:**\n{context.transcript_text}"
        )

    @staticmethod
    def _parse_summary_response(content: str) -> dict[str, Any]:
        """Parse the LLM JSON response, tolerating markdown fences."""
        stripped = content.strip()
        if stripped.startswith("```"):
            stripped = stripped.strip("`")
            if stripped.startswith("json"):
                stripped = stripped[4:]
            stripped = stripped.strip()
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            from src.utils import load_json_with_recovery

            return load_json_with_recovery(content)

    @staticmethod
    def _retry_count_for_last_call(provider: OpenAIRetryableProvider, exc: Exception) -> int:
        """Best-effort retry counter for ledger emission.

        The retry counter is private state inside the provider; we
        surface it through the exception chain so the ledger sees the
        actual number of attempts made before the failure.
        """
        return int(getattr(exc, "retry_count", 0) or 0)


# ---------------------------------------------------------------------------
# Insights provider adapter
# ---------------------------------------------------------------------------


class OpenAIInsightsProvider(InsightsProviderPort):
    """Adapter that satisfies :class:`InsightsProviderPort` using OpenAI."""

    _INSIGHTS_PROMPT = (
        "You are an expert technical educator and content analyst. "
        "Analyze YouTube tutorial summaries to help engineers choose the best video for their learning goals.\n\n"
        "Respond ONLY with a JSON object using this schema:\n"
        "{\n"
        "  \"learning_outcome\": str,\n"
        "  \"difficulty_level\": \"Beginner|Intermediate|Advanced\",\n"
        "  \"teaching_style\": \"Code-along|Explanation-heavy|Project-based|Theory-focused|Mixed\",\n"
        "  \"practical_value\": \"High|Medium|Low\",\n"
        "  \"content_depth\": \"Surface-level|Moderate|Deep-dive\",\n"
        "  \"target_audience\": str,\n"
        "  \"key_differentiators\": str,\n"
        "  \"time_investment_worth\": \"Yes|Maybe|No\",\n"
        "  \"prerequisites\": str,\n"
        "  \"follow_up_recommendations\": str\n"
        "}"
    )

    def __init__(
        self,
        *,
        settings: SettingsLoader | None = None,
        usage_ledger: UsageLedgerSink | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        retry_policy: RetryPolicy | None = None,
        model: str | None = None,
        timeout: int | None = None,
        run_id: str | None = None,
    ) -> None:
        self._settings = settings or get_config
        api_key_resolved, base_url_resolved = _resolve_api_credentials(self._settings)
        self._run_id = run_id
        self._model = model or str(
            self._settings("api.openai.model", "openai/gpt-5-mini")
        )
        self._timeout = int(
            timeout if timeout is not None else self._settings("api.openai.timeout", 180)
        )
        policy = retry_policy or _default_retry_policy_from_config(self._settings)
        self._provider = OpenAIRetryableProvider(
            settings=self._settings,
            api_key=api_key or api_key_resolved,
            base_url=base_url or base_url_resolved,
            retry_policy=policy,
            usage_ledger=usage_ledger,
        )

    async def generate_insights(
        self,
        video_metadata: dict,
        summary_data: dict,
        prompt_version: str = "v1",
        model: str = "gpt-4o-mini",
    ) -> InsightsResult:
        """Generate insights for a single video."""
        user_message = self._build_user_message(video_metadata, summary_data)
        messages = [
            {"role": "system", "content": self._INSIGHTS_PROMPT},
            {"role": "user", "content": user_message},
        ]
        chosen_model = model or self._model
        video_id = str(video_metadata.get("video_id") or video_metadata.get("id") or "unknown")
        attempt_model = chosen_model
        response: LLMResponse | None = None
        error_category: str | None = None
        try:
            response = self._provider.complete(
                messages=messages,
                model=chosen_model,
                timeout=self._timeout,
            )
            attempt_model = response.model
            parsed = self._try_parse_insights(response.content)
            if parsed is None:
                parsed = self._get_fallback_insights(failed=True)
            content_hash = sha256_text(response.content)
            artifact_path = ""
            return InsightsResult(
                video_id=video_id,
                learning_outcome=str(parsed.get("learning_outcome", "")),
                difficulty_level=str(parsed.get("difficulty_level", "Unknown")),
                teaching_style=str(parsed.get("teaching_style", "Unknown")),
                practical_value=str(parsed.get("practical_value", "Unknown")),
                content_depth=str(parsed.get("content_depth", "Unknown")),
                target_audience=str(parsed.get("target_audience", "General")),
                key_differentiators=str(parsed.get("key_differentiators", "N/A")),
                time_investment_worth=str(parsed.get("time_investment_worth", "Maybe")),
                prerequisites=str(parsed.get("prerequisites", "None specified")),
                follow_up_recommendations=str(
                    parsed.get("follow_up_recommendations", "")
                ),
                content_hash=content_hash,
                artifact_path=artifact_path,
                prompt_version=prompt_version,
                model=response.model,
            )
        except LLMProviderError:
            error_category = "provider_error"
            parsed = self._get_fallback_insights(failed=True)
            return InsightsResult(
                video_id=video_id,
                **parsed,
                content_hash="",
                artifact_path="",
                prompt_version=prompt_version,
                model=chosen_model,
            )
        finally:
            self._provider._emit_usage(
                _build_usage_record(
                    provider="openai",
                    operation="insights",
                    model=attempt_model,
                    response=response,
                    success=error_category is None,
                    error_category=error_category,
                    retry_count=0,
                    run_id=self._run_id,
                    video_id=video_id,
                )
            )

    async def generate_insights_batch(
        self,
        video_metadata_list: list[dict],
        summary_data_list: list[dict],
        prompt_version: str = "v1",
        model: str = "gpt-4o-mini",
    ) -> list[InsightsResult]:
        import asyncio

        if len(video_metadata_list) != len(summary_data_list):
            raise ValueError("video_metadata_list and summary_data_list must be parallel")
        return await asyncio.gather(
            *(
                self.generate_insights(meta, summary, prompt_version, model)
                for meta, summary in zip(video_metadata_list, summary_data_list, strict=False)
            )
        )

    def _build_user_message(self, video_meta: dict, summary: dict) -> str:
        return (
            "VIDEO METADATA:\n"
            f"Title: {video_meta.get('title', 'N/A')}\n"
            f"Channel: {video_meta.get('channel', 'N/A')}\n"
            f"Description: {str(video_meta.get('description', 'N/A'))[:500]}\n"
            f"Published: {video_meta.get('published_at', 'N/A')}\n\n"
            "SUMMARY DATA:\n"
            f"Overview: {summary.get('high_level_overview', 'N/A')}\n"
            f"Technical Breakdown: {json.dumps(summary.get('technical_breakdown', []), indent=2)}\n"
            f"Insights: {summary.get('insights', [])}\n"
            f"Applications: {summary.get('applications', [])}\n"
            f"Limitations: {summary.get('limitations', [])}"
        )

    @staticmethod
    def _try_parse_insights(content: str) -> dict[str, Any] | None:
        stripped = content.strip()
        if stripped.startswith("```"):
            stripped = stripped.strip("`")
            if stripped.startswith("json"):
                stripped = stripped[4:]
            stripped = stripped.strip()
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            try:
                from src.utils import load_json_with_recovery

                return load_json_with_recovery(content)
            except (json.JSONDecodeError, ValueError):
                return None

    @staticmethod
    def _get_fallback_insights(failed: bool = False) -> dict[str, str]:
        return {
            "learning_outcome": "Analysis failed" if failed else "Analysis unavailable",
            "difficulty_level": "Unknown",
            "teaching_style": "Unknown",
            "practical_value": "Unknown",
            "content_depth": "Unknown",
            "target_audience": "General",
            "key_differentiators": "N/A",
            "time_investment_worth": "Maybe",
            "prerequisites": "None specified",
            "follow_up_recommendations": "Continue learning in this domain",
        }


# ---------------------------------------------------------------------------
# Assignment generator adapter
# ---------------------------------------------------------------------------


class OpenAIAssignmentAdapter(AssignmentGeneratorPort):
    """Adapter that satisfies :class:`AssignmentGeneratorPort` using OpenAI."""

    def __init__(
        self,
        *,
        settings: SettingsLoader | None = None,
        usage_ledger: UsageLedgerSink | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        retry_policy: RetryPolicy | None = None,
        prompt_name: str | None = None,
        model: str | None = None,
        timeout: int | None = None,
        run_id: str | None = None,
    ) -> None:
        self._settings = settings or get_config
        api_key_resolved, base_url_resolved = _resolve_api_credentials(self._settings)
        self._run_id = run_id
        self._prompt_name = prompt_name
        self._model = model or str(
            self._settings("api.openai.model", "openai/gpt-5-mini")
        )
        self._timeout = int(
            timeout if timeout is not None else self._settings("api.openai.timeout", 180)
        )
        policy = retry_policy or _default_retry_policy_from_config(self._settings)
        self._provider = OpenAIRetryableProvider(
            settings=self._settings,
            api_key=api_key or api_key_resolved,
            base_url=base_url or base_url_resolved,
            retry_policy=policy,
            usage_ledger=usage_ledger,
        )
        self._prompts: dict[str, str] | None = None

    def _ensure_prompts(self) -> dict[str, str]:
        if self._prompts is None:
            self._prompts = _load_assignment_prompts(self._prompt_name)
        return self._prompts

    async def generate_assignment(
        self,
        video_id: str,
        title: str,
        channel: str,
        summary_data: dict,
        prompt_version: str = "v1",
        model: str = "gpt-4o-mini",
    ) -> AssignmentResult:
        """Generate a single assignment markdown from a summary."""
        prompts = self._ensure_prompts()
        technical_text = self._format_technical_breakdown(summary_data)
        user_prompt = prompts["user_prompt_template"].format(
            title=title,
            channel=channel,
            difficulty="Intermediate",
            summary=summary_data.get("high_level_overview", ""),
            technical_breakdown=technical_text,
            insights="\n".join(f"- {i}" for i in summary_data.get("insights", [])),
            applications="\n".join(f"- {a}" for a in summary_data.get("applications", [])),
        )
        messages = [
            {"role": "system", "content": prompts["system_prompt"]},
            {"role": "user", "content": user_prompt},
        ]
        chosen_model = model or self._model
        attempt_model = chosen_model
        response: LLMResponse | None = None
        error_category: str | None = None
        try:
            response = self._provider.complete(
                messages=messages,
                model=chosen_model,
                timeout=self._timeout,
            )
            attempt_model = response.model
            markdown = response.content.strip()
            content_hash = sha256_text(markdown)
            byte_size = len(markdown.encode("utf-8"))
            metadata = {
                "video_id": video_id,
                "video_title": title,
                "channel": channel,
                "model_used": response.model,
                "generated_at": datetime.now(datetime.UTC).isoformat(),
                "latency_ms": response.latency_ms,
            }
            display_metadata = {k: str(v) for k, v in metadata.items() if v}
            return AssignmentResult(
                video_id=video_id,
                markdown=markdown,
                sections=[],
                checklist=[],
                metadata=metadata,
                display_metadata=display_metadata,
                content_hash=content_hash,
                artifact_path="",
                byte_size=byte_size,
                prompt_version=prompt_version,
                model=response.model,
            )
        except LLMProviderError:
            error_category = "provider_error"
            raise
        finally:
            self._provider._emit_usage(
                _build_usage_record(
                    provider="openai",
                    operation="assignment",
                    model=attempt_model,
                    response=response,
                    success=error_category is None,
                    error_category=error_category,
                    retry_count=0,
                    run_id=self._run_id,
                    video_id=video_id,
                )
            )

    async def generate_assignments_batch(
        self,
        videos: list[dict],
        summaries: list[dict],
        prompt_version: str = "v1",
        model: str = "gpt-4o-mini",
    ) -> list[AssignmentResult]:
        import asyncio

        if len(videos) != len(summaries):
            raise ValueError("videos and summaries must be parallel")
        return await asyncio.gather(
            *(
                self.generate_assignment(
                    video_id=str(video.get("video_id") or video.get("id") or "unknown"),
                    title=video.get("title", "Unknown Title"),
                    channel=video.get("channel", "Unknown Channel"),
                    summary_data=summary,
                    prompt_version=prompt_version,
                    model=model,
                )
                for video, summary in zip(videos, summaries, strict=False)
            )
        )

    @staticmethod
    def _format_technical_breakdown(summary: dict) -> str:
        items = summary.get("technical_breakdown", []) or []
        lines: list[str] = []
        for item in items:
            item_type = item.get("type", "")
            if item_type == "tool":
                lines.append(f"- **Tool: {item.get('name', '')}** - {item.get('purpose', '')}")
            elif item_type == "process":
                lines.append(f"- **Step {item.get('step_number', '')}:** {item.get('description', '')}")
            elif item_type == "architecture":
                lines.append(f"- **Architecture:** {item.get('description', '')}")
        return "\n".join(lines)


__all__ = [
    "OpenAISummarizerAdapter",
    "OpenAIInsightsProvider",
    "OpenAIAssignmentAdapter",
]
