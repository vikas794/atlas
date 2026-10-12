"""Stage-machine rules from PRD section 15."""

from __future__ import annotations

import pytest

from src.domain.models.playlist import Stage
from src.domain.services.stage_machine import (
    ALLOWED_TRANSITIONS,
    InvalidTransitionError,
    assert_transition,
    can_fail,
    can_transition,
    last_stable_stage,
)

HAPPY_PATH = [
    (Stage.QUEUED, Stage.FETCHING_TRANSCRIPT),
    (Stage.FETCHING_TRANSCRIPT, Stage.TRANSCRIPT_READY),
    (Stage.TRANSCRIPT_READY, Stage.GENERATING_NOTES),
    (Stage.GENERATING_NOTES, Stage.PENDING_REVIEW),
    (Stage.PENDING_REVIEW, Stage.APPROVED),
    (Stage.APPROVED, Stage.PUBLISHED),
]

ACTIVE_STAGES = [Stage.FETCHING_TRANSCRIPT, Stage.TRANSCRIPT_READY, Stage.GENERATING_NOTES]

#: Explicitly illegal moves: skipping stages, going backwards, leaving terminal
#: states, and failing from a stage that is not active.
ILLEGAL = [
    (Stage.QUEUED, Stage.APPROVED),
    (Stage.QUEUED, Stage.TRANSCRIPT_READY),
    (Stage.QUEUED, Stage.PUBLISHED),
    (Stage.QUEUED, Stage.PENDING_REVIEW),
    (Stage.PUBLISHED, Stage.QUEUED),
    (Stage.PUBLISHED, Stage.FAILED),
    (Stage.PUBLISHED, Stage.APPROVED),
    (Stage.APPROVED, Stage.FETCHING_TRANSCRIPT),
    (Stage.APPROVED, Stage.QUEUED),
    (Stage.APPROVED, Stage.GENERATING_NOTES),
    (Stage.PENDING_REVIEW, Stage.GENERATING_NOTES),
    (Stage.PENDING_REVIEW, Stage.PUBLISHED),
    (Stage.TRANSCRIPT_READY, Stage.PUBLISHED),
    (Stage.QUEUED, Stage.FAILED),
    (Stage.PENDING_REVIEW, Stage.FAILED),
    (Stage.APPROVED, Stage.FAILED),
    (Stage.FAILED, Stage.FETCHING_TRANSCRIPT),
    (Stage.FAILED, Stage.PUBLISHED),
]


@pytest.mark.parametrize(("current", "target"), HAPPY_PATH)
def test_happy_path_transitions_are_allowed(current: Stage, target: Stage) -> None:
    assert can_transition(current, target) is True
    assert target in ALLOWED_TRANSITIONS[current]
    assert_transition(current, target)  # does not raise


@pytest.mark.parametrize("stage", ACTIVE_STAGES)
def test_active_stages_can_fail(stage: Stage) -> None:
    assert can_transition(stage, Stage.FAILED) is True
    assert can_fail(stage) is True
    assert_transition(stage, Stage.FAILED)


@pytest.mark.parametrize("stage", [Stage.QUEUED, Stage.PENDING_REVIEW, Stage.APPROVED])
def test_non_active_stages_cannot_fail(stage: Stage) -> None:
    assert can_fail(stage) is False
    assert can_transition(stage, Stage.FAILED) is False


@pytest.mark.parametrize(("current", "target"), ILLEGAL)
def test_illegal_transitions_are_rejected(current: Stage, target: Stage) -> None:
    assert can_transition(current, target) is False
    with pytest.raises(InvalidTransitionError):
        assert_transition(current, target)


def test_failed_to_queued_requires_explicit_retry() -> None:
    assert can_transition(Stage.FAILED, Stage.QUEUED) is False
    with pytest.raises(InvalidTransitionError):
        assert_transition(Stage.FAILED, Stage.QUEUED)

    assert can_transition(Stage.FAILED, Stage.QUEUED, is_retry=True) is True
    assert_transition(Stage.FAILED, Stage.QUEUED, is_retry=True)


def test_retry_flag_does_not_unlock_other_edges() -> None:
    assert can_transition(Stage.PUBLISHED, Stage.QUEUED, is_retry=True) is False
    assert can_transition(Stage.APPROVED, Stage.FETCHING_TRANSCRIPT, is_retry=True) is False
    assert can_transition(Stage.QUEUED, Stage.PUBLISHED, is_retry=True) is False


@pytest.mark.parametrize("stage", list(Stage))
def test_same_stage_is_an_idempotent_no_op(stage: Stage) -> None:
    assert can_transition(stage, stage) is True
    assert_transition(stage, stage)


def test_every_stage_is_covered_by_the_transition_table() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(Stage)


def test_published_is_terminal() -> None:
    assert ALLOWED_TRANSITIONS[Stage.PUBLISHED] == frozenset()
    assert Stage.PUBLISHED.is_terminal is True


def test_invalid_transition_error_carries_both_stages() -> None:
    with pytest.raises(InvalidTransitionError) as excinfo:
        assert_transition(Stage.QUEUED, Stage.APPROVED)
    error = excinfo.value
    assert error.current is Stage.QUEUED
    assert error.target is Stage.APPROVED
    assert "queued" in str(error)
    assert "approved" in str(error)


def test_failed_is_not_part_of_the_normal_transition_table() -> None:
    assert ALLOWED_TRANSITIONS[Stage.FAILED] == frozenset()
    assert Stage.QUEUED not in ALLOWED_TRANSITIONS[Stage.FAILED]


@pytest.mark.parametrize(
    ("crashed", "expected"),
    [
        (Stage.FETCHING_TRANSCRIPT, Stage.QUEUED),
        (Stage.GENERATING_NOTES, Stage.QUEUED),
        (Stage.TRANSCRIPT_READY, Stage.QUEUED),
        (Stage.QUEUED, Stage.QUEUED),
        (Stage.PENDING_REVIEW, Stage.PENDING_REVIEW),
        (Stage.APPROVED, Stage.APPROVED),
        (Stage.PUBLISHED, Stage.PUBLISHED),
        (Stage.FAILED, Stage.FAILED),
    ],
)
def test_last_stable_stage_for_crash_recovery(crashed: Stage, expected: Stage) -> None:
    assert last_stable_stage(crashed) is expected


def test_last_stable_stage_either_keeps_the_stage_or_requeues_it() -> None:
    """Recovery never invents a stage: it either stays put or drops back to queued."""
    for stage in Stage:
        recovered = last_stable_stage(stage)
        assert recovered in {stage, Stage.QUEUED}
        if recovered is not stage:
            assert can_transition(recovered, Stage.FETCHING_TRANSCRIPT) is True
