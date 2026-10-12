"""Per-video pipeline stage machine (PRD section 15).

The happy path is strictly ordered:

    queued -> fetching_transcript -> transcript_ready -> generating_notes
           -> pending_review -> approved -> published

Any *active* stage (``fetching_transcript``, ``transcript_ready``,
``generating_notes``) may move to ``failed``. ``failed`` returns to ``queued``
only through an explicit retry — never as part of the normal path. Every other
transition is rejected, which keeps the pipeline safe to stop, crash and resume.

Two-stage persistence is the caller's job, not this module's: write the stage
*before* the external call and again *after* it finishes (PRD section 15).
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from src.domain.exceptions import DomainError
from src.domain.models.playlist import Stage


class InvalidTransitionError(DomainError):
    """Raised when a caller asks for a stage move the machine forbids."""

    def __init__(self, current: Stage, target: Stage) -> None:
        super().__init__(
            f"Invalid stage transition: {getattr(current, 'value', current)} -> "
            f"{getattr(target, 'value', target)}"
        )
        self.current = current
        self.target = target


#: The normal (non-retry) edges. ``failed`` is intentionally empty: it only
#: moves to ``queued`` through :func:`assert_transition` with ``is_retry=True``.
ALLOWED_TRANSITIONS: Mapping[Stage, frozenset[Stage]] = MappingProxyType(
    {
        Stage.QUEUED: frozenset({Stage.FETCHING_TRANSCRIPT}),
        Stage.FETCHING_TRANSCRIPT: frozenset({Stage.TRANSCRIPT_READY, Stage.FAILED}),
        Stage.TRANSCRIPT_READY: frozenset({Stage.GENERATING_NOTES, Stage.FAILED}),
        Stage.GENERATING_NOTES: frozenset({Stage.PENDING_REVIEW, Stage.FAILED}),
        Stage.PENDING_REVIEW: frozenset({Stage.APPROVED}),
        Stage.APPROVED: frozenset({Stage.PUBLISHED}),
        Stage.PUBLISHED: frozenset(),
        Stage.FAILED: frozenset(),
    }
)

#: The single retry-only edge, kept out of :data:`ALLOWED_TRANSITIONS`.
_RETRY_TRANSITIONS: Mapping[Stage, frozenset[Stage]] = MappingProxyType(
    {Stage.FAILED: frozenset({Stage.QUEUED})}
)

#: Stages that may fail; PRD section 15 says "any active stage can move to failed".
_FAILING_STAGES: frozenset[Stage] = frozenset(
    {Stage.FETCHING_TRANSCRIPT, Stage.TRANSCRIPT_READY, Stage.GENERATING_NOTES}
)

#: Crash recovery: an interrupted in-flight stage re-queues the video. This
#: includes ``transcript_ready``: nothing is running for it any more after a
#: restart, and re-queueing is safe because ``ProcessVideoUseCase`` skips the
#: transcript fetch when a stored transcript exists and continues at notes.
_UNSTABLE_STAGES: Mapping[Stage, Stage] = MappingProxyType(
    {
        Stage.FETCHING_TRANSCRIPT: Stage.QUEUED,
        Stage.TRANSCRIPT_READY: Stage.QUEUED,
        Stage.GENERATING_NOTES: Stage.QUEUED,
    }
)

#: Stages from which a manual "regenerate notes" may start (PRD section 15). The
#: video is re-queued with a regenerate flag; the worker then reuses the stored
#: transcript, or re-fetches it if it was cleaned up (PRD FR11).
REGENERATE_FROM: frozenset[Stage] = frozenset(
    {Stage.PENDING_REVIEW, Stage.APPROVED, Stage.PUBLISHED, Stage.FAILED}
)


def can_transition(current: Stage, target: Stage, *, is_retry: bool = False) -> bool:
    """Whether ``current -> target`` is a legal move.

    Same-stage moves are always allowed: re-persisting the current stage is an
    idempotent write, not a transition.
    """
    current = Stage(current)
    target = Stage(target)
    if current == target:
        return True
    if target in ALLOWED_TRANSITIONS[current]:
        return True
    if is_retry and target in _RETRY_TRANSITIONS.get(current, frozenset()):
        return True
    return False


def assert_transition(current: Stage, target: Stage, *, is_retry: bool = False) -> None:
    """Raise :class:`InvalidTransitionError` unless the move is legal."""
    if not can_transition(current, target, is_retry=is_retry):
        raise InvalidTransitionError(current, target)


def last_stable_stage(stage: Stage) -> Stage:
    """Stage a crashed video can safely resume from.

    ``fetching_transcript``, ``transcript_ready`` and ``generating_notes`` are
    re-queued (the stored transcript, if any, is reused); every other stage maps
    to itself.
    """
    stage = Stage(stage)
    return _UNSTABLE_STAGES.get(stage, stage)


def can_fail(stage: Stage) -> bool:
    """Whether ``stage`` is an active stage that may move to ``failed``."""
    return Stage(stage) in _FAILING_STAGES


def can_regenerate(stage: Stage) -> bool:
    """Whether a manual regenerate may be requested while the video is in ``stage``."""
    return Stage(stage) in REGENERATE_FROM
