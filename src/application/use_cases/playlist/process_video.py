"""Process a single video through the pipeline (PRD section 15, 18, M2).

Stage lifecycle:
    queued -> fetching_transcript -> transcript_ready -> generating_notes -> pending_review

Enforces:
- Skip-completed: never re-fetches an existing transcript or re-generates an existing summary
  (unless a regenerate was explicitly requested).
- Assert transitions: verifies stage transitions before persisting.
- Transient vs permanent error classification with exponential backoff scheduling.
- Stale-run protection: every write carries the video's ``run_token``; if the video was
  reset, retried or deleted while this task ran, the task stops without writing.

Retry policy (the single owner of retries): **this use case**. The notes generators do
*not* retry on their own; a transient failure (rate limit, network, malformed output) is
persisted as ``failed`` with a ``next_retry_at`` and the worker re-runs the video once that
time arrives, up to ``max_auto_retries`` times (so at most ``1 + max_auto_retries`` LLM
calls per video). Once exhausted the failure stays ``transient`` (the UI shows "Failed -
temporary" with ``retries_exhausted``) but ``next_retry_at`` is NULL: only a manual retry
runs it again, so the worker can never retry forever.

Duplicates (PRD section 20): a row that repeats a video earlier in the same playlist is
not processed again; it receives its own copy of the first occurrence's summary.
"""

from __future__ import annotations

import logging
from typing import Any

from src.domain.exceptions import ConfigurationError, StaleRunError
from src.domain.interfaces.notes_generator import NotesGeneratorPort
from src.domain.interfaces.playlist_repository import PlaylistRepositoryPort
from src.domain.interfaces.transcript_provider import TranscriptProviderPort
from src.domain.models.playlist import (
    FailureKind,
    FailureType,
    PipelineError,
    Stage,
    Video,
)
from src.domain.services.backoff import compute_delay, iso_after
from src.domain.services.stage_machine import assert_transition
from src.shared.redaction import redact_text

logger = logging.getLogger(__name__)

#: Reason shown for a failure that is a bug in Atlas rather than in the video.
INTERNAL_ERROR_REASON = "An unexpected internal error occurred; see the server log for details."


def _ctx(video: Video) -> dict[str, Any]:
    """Structured log context: every pipeline event carries both ids (PRD section 21)."""
    return {
        "playlist_id": video.playlist_id,
        "video_id": video.id,
        "youtube_video_id": video.youtube_video_id,
    }


class ProcessVideoUseCase:
    """Processes a video from queued to pending_review."""

    def __init__(
        self,
        repo: PlaylistRepositoryPort,
        transcript_provider: TranscriptProviderPort,
        notes_generator: NotesGeneratorPort,
        max_auto_retries: int = 3,
        backoff_base_seconds: float = 5.0,
        backoff_cap_seconds: float = 60.0,
        rate_limit_max_auto_retries: int | None = None,
        rate_limit_backoff_base_seconds: float | None = None,
        rate_limit_backoff_cap_seconds: float | None = None,
    ) -> None:
        self.repo = repo
        self.transcript_provider = transcript_provider
        self.notes_generator = notes_generator
        self.max_auto_retries = max_auto_retries
        self.backoff_base_seconds = backoff_base_seconds
        self.backoff_cap_seconds = backoff_cap_seconds
        # A throttled YouTube transcript fetch recovers over minutes, not seconds; None keeps
        # the regular policy.
        self.rate_limit_max_auto_retries = (
            max_auto_retries if rate_limit_max_auto_retries is None else rate_limit_max_auto_retries
        )
        self.rate_limit_backoff_base_seconds = (
            backoff_base_seconds
            if rate_limit_backoff_base_seconds is None
            else rate_limit_backoff_base_seconds
        )
        self.rate_limit_backoff_cap_seconds = (
            backoff_cap_seconds
            if rate_limit_backoff_cap_seconds is None
            else rate_limit_backoff_cap_seconds
        )

    async def execute(self, video_pk: int) -> Video | None:
        """Run the video pipeline for a single video.

        Args:
            video_pk: Primary key ID of the video row.

        Returns:
            The updated Video entity, or None if the video was not found.
        """
        video = await self.repo.get_video(video_pk)
        if video is None:
            logger.warning("Video pk %d not found in database", video_pk)
            return None

        ctx = _ctx(video)
        if video.removed_upstream:
            logger.info("Video is flagged as removed upstream; skipping", extra=ctx)
            return video

        if video.stage == Stage.PUBLISHED or (
            video.stage in (Stage.PENDING_REVIEW, Stage.APPROVED) and not video.regen_requested
        ):
            return video

        token = video.run_token
        try:
            if video.stage == Stage.FAILED:
                assert_transition(video.stage, Stage.QUEUED, is_retry=True)
                await self.repo.update_stage(video_pk, Stage.QUEUED, token=token)
                video = await self.repo.get_video(video_pk)
                if video is None:
                    return None

            if video.duplicate_of is not None and not video.regen_requested:
                return await self._reuse_first_occurrence(video, token)

            await self._run(video, token)
            return await self.repo.get_video(video_pk)

        except StaleRunError:
            logger.info("Video was reset or deleted while running; discarding result", extra=ctx)
            return await self.repo.get_video(video_pk)
        except PipelineError as exc:
            logger.warning(
                "Pipeline error: %s", exc, extra={**ctx, "failure_type": exc.failure_type.value}
            )
            await self._handle_error(video_pk, exc, token)
            return await self.repo.get_video(video_pk)
        except ConfigurationError as exc:
            # Atlas itself is misconfigured (missing key / prompt): retrying cannot help.
            reason = redact_text(str(exc))
            logger.error("Configuration error: %s", reason, extra=ctx)
            await self._handle_error(
                video_pk, PipelineError(FailureType.CONFIG_ERROR, reason), token
            )
            return await self.repo.get_video(video_pk)
        except Exception:
            # A programming or database error. Log the traceback (redacted by the log
            # formatter) and fail permanently with a generic reason: it is not a
            # transcript problem, so it must not be classified as one.
            logger.exception("Unexpected error processing video", extra=ctx)
            await self._handle_error(
                video_pk, PipelineError(FailureType.INTERNAL_ERROR, INTERNAL_ERROR_REASON), token
            )
            return await self.repo.get_video(video_pk)

    async def _reuse_first_occurrence(self, video: Video, token: int) -> Video | None:
        """Give a duplicate row its own copy of the first occurrence's summary."""
        source_video_pk = video.duplicate_of
        if source_video_pk is None:
            return video

        assert video.id is not None  # Persisted videos always have a primary key.
        copied = await self.repo.copy_summary(source_video_pk, video.id, token=token)
        if copied is None:
            first = await self.repo.get_video(source_video_pk)
            if first is not None and first.stage == Stage.FAILED and first.next_retry_at is None:
                # The first occurrence failed for good: show that failure here too rather
                # than leaving this row queued forever. A manual retry of this row re-checks.
                await self.repo.update_stage(
                    video.id,
                    Stage.FAILED,
                    failure_type=first.failure_type or FailureType.INTERNAL_ERROR,
                    failure_kind=first.failure_kind or FailureKind.PERMANENT,
                    failure_reason=(
                        f"Same video as #{first.position}, which failed: "
                        f"{first.failure_reason or 'see that video'}"
                    ),
                    next_retry_at=None,
                    token=token,
                )
                logger.info("First occurrence failed; duplicate marked failed", extra=_ctx(video))
                return await self.repo.get_video(video.id)
            # The first occurrence has no summary yet; stay queued until it does.
            logger.info("First occurrence has no summary yet; waiting", extra=_ctx(video))
            return video
        # Reuse short-circuits the normal path, so it moves straight to review.
        await self.repo.update_stage(video.id, Stage.PENDING_REVIEW, token=token)
        logger.info("Reused summary of duplicate video", extra=_ctx(video))
        return await self.repo.get_video(video.id)

    async def _run(self, video: Video, token: int) -> None:
        """Transcript phase then notes phase, persisting each stage around its side effect."""
        ctx = _ctx(video)
        # Persisted videos must have a primary key.
        video_pk = video.id
        if video_pk is None:
            raise ValueError("Cannot process a video without a primary key")
        regenerate = video.regen_requested
        # A duplicate has no transcript of its own: it reads its first occurrence's.
        source_pk = video.duplicate_of or video_pk
        stage = video.stage

        # 1. Transcript phase
        transcript = await self.repo.get_transcript(source_pk)
        if transcript is not None:
            transcript_text = transcript.content
            logger.info("Existing transcript found; skipping fetch", extra=ctx)
            if stage == Stage.QUEUED:
                await self.repo.update_stage(video_pk, Stage.TRANSCRIPT_READY, token=token)
                stage = Stage.TRANSCRIPT_READY
        else:
            if stage == Stage.TRANSCRIPT_READY:
                # The transcript was cleaned up while the video waited: fetch it again.
                await self.repo.update_stage(video_pk, Stage.QUEUED, token=token)
                stage = Stage.QUEUED
            assert_transition(stage, Stage.FETCHING_TRANSCRIPT)
            await self.repo.update_stage(video_pk, Stage.FETCHING_TRANSCRIPT, token=token)
            logger.info("Fetching transcript", extra=ctx)

            result = await self.transcript_provider.fetch_transcript(video.youtube_video_id)
            await self.repo.upsert_transcript(
                video_pk=source_pk,
                content=result.cleaned_text,
                language=result.language,
                content_hash=result.content_hash,
                byte_size=result.byte_size,
                artifact_path=result.artifact_path or None,
                token=token if source_pk == video_pk else None,
            )
            # The transcript phase is done: notes generation gets its own full retry
            # budget instead of inheriting what YouTube throttling used up.
            await self.repo.update_stage(
                video_pk, Stage.TRANSCRIPT_READY, token=token, reset_retries=True
            )
            stage = Stage.TRANSCRIPT_READY
            transcript_text = result.cleaned_text

        # 2. Notes generation phase
        existing_summary = await self.repo.get_summary(video_pk)
        if existing_summary is not None and not regenerate:
            logger.info("Existing summary found; skipping generation", extra=ctx)
            await self.repo.update_stage(video_pk, Stage.PENDING_REVIEW, token=token)
            return

        assert_transition(stage, Stage.GENERATING_NOTES)
        await self.repo.update_stage(video_pk, Stage.GENERATING_NOTES, token=token)
        logger.info("Generating notes", extra={**ctx, "regenerate": regenerate})

        notes = await self.notes_generator.generate_notes(
            title=video.title,
            transcript=transcript_text,
        )
        if regenerate:
            # Clears approval/publish state and the regenerate flag; keeps drive_doc_id.
            await self.repo.regenerate(
                video_pk,
                notes.content,
                provider=notes.provider,
                model=notes.model,
                prompt_version=notes.prompt_version,
                token=token,
            )
            return
        await self.repo.upsert_summary(
            video_pk=video_pk,
            content=notes.content,
            provider=notes.provider,
            model=notes.model,
            prompt_version=notes.prompt_version,
            token=token,
        )
        await self.repo.update_stage(video_pk, Stage.PENDING_REVIEW, token=token)

    async def _handle_error(self, video_pk: int, error: PipelineError, token: int) -> None:
        """Persist a classified failure; schedule an automatic retry only when allowed."""
        video = await self.repo.get_video(video_pk)
        if video is None:
            return
        ctx = _ctx(video)
        failure_kind = error.failure_kind
        failure_type = error.failure_type
        reason = error.reason

        try:
            throttled = (
                failure_type == FailureType.RATE_LIMITED
                and video.stage == Stage.FETCHING_TRANSCRIPT
            )
            max_retries = self.rate_limit_max_auto_retries if throttled else self.max_auto_retries
            if failure_kind == FailureKind.TRANSIENT and video.retry_count < max_retries:
                next_attempt = video.retry_count + 1
                delay = compute_delay(
                    next_attempt,
                    self.rate_limit_backoff_base_seconds if throttled else self.backoff_base_seconds,
                    self.rate_limit_backoff_cap_seconds if throttled else self.backoff_cap_seconds,
                )
                next_retry_at = iso_after(delay)
                await self.repo.update_stage(
                    video_pk,
                    Stage.FAILED,
                    failure_type=failure_type,
                    failure_kind=failure_kind,
                    failure_reason=reason,
                    next_retry_at=next_retry_at,
                    token=token,
                )
                await self.repo.increment_retry(video_pk, next_retry_at=next_retry_at, token=token)
                logger.info(
                    "Scheduled automatic retry %d/%d in %.1fs",
                    next_attempt,
                    max_retries,
                    delay,
                    extra={**ctx, "failure_type": failure_type.value},
                )
                return

            # Permanent, or transient with the automatic retry budget used up. Exhausted
            # transient failures keep failure_kind=transient but have no next_retry_at,
            # so they are manual-retry only -- and the reason must say so.
            if failure_kind == FailureKind.TRANSIENT:
                reason = f"{reason} Automatic retries are used up; press Retry to try again."
            await self.repo.update_stage(
                video_pk,
                Stage.FAILED,
                failure_type=failure_type,
                failure_kind=failure_kind,
                failure_reason=reason,
                next_retry_at=None,
                token=token,
            )
            logger.warning(
                "Video moved to FAILED (kind=%s, type=%s, retries=%d%s): %s",
                failure_kind.value,
                failure_type.value,
                video.retry_count,
                ", automatic retries exhausted" if failure_kind == FailureKind.TRANSIENT else "",
                reason,
                extra={**ctx, "failure_type": failure_type.value},
            )
        except StaleRunError:
            logger.info("Video was reset while failing; failure not recorded", extra=ctx)
