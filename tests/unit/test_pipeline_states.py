"""PART XIV P27 transition rules (pure): what proves it is the rule table itself.

These tests prove the vocabulary the workflow and the store share. They prove nothing
about a running workflow; the Gate R3 integration tests do that.
"""

from __future__ import annotations

import itertools

import pytest
from plm_platform.pipeline.states import (
    LIFECYCLE,
    PAID_STEP_CAPABILITIES,
    STEP_ORIGIN_PHASE,
    TERMINAL_STATES,
    IllegalTransitionError,
    PipelineState,
    Step,
    check_transition,
    step_activity_name,
)

S = PipelineState


def test_the_states_are_part_xiv_plus_held_failed_cancelled() -> None:
    assert [s.value for s in LIFECYCLE] == [
        "IDEA", "RESEARCHED", "POLICY_CHECKED", "GENERATING", "GENERATED", "QA", "PASSED",
        "ASSEMBLING", "PACKAGED", "UPLOADING", "PROCESSING", "PUBLISHED", "ANALYZING",
        "LEARNING",
    ]
    assert set(PipelineState) == set(LIFECYCLE) | {S.REJECTED, S.HELD, S.FAILED, S.CANCELLED}


def test_the_happy_path_is_allowed_step_by_step() -> None:
    check_transition(None, S.IDEA)
    for current, target in itertools.pairwise(LIFECYCLE):
        check_transition(current, target)


def test_a_run_starts_only_in_idea() -> None:
    with pytest.raises(IllegalTransitionError):
        check_transition(None, S.RESEARCHED)


@pytest.mark.parametrize(("current", "target"), [
    (S.IDEA, S.GENERATING),          # skipping research and the policy check
    (S.POLICY_CHECKED, S.GENERATED),  # generation result without GENERATING
    (S.GENERATED, S.PASSED),          # QA skipped
    (S.QA, S.ASSEMBLING),             # a pass must be recorded
    (S.PACKAGED, S.PUBLISHED),        # upload skipped
    (S.PUBLISHED, S.LEARNING),        # the outcome horizon skipped
    (S.LEARNING, S.IDEA),
])
def test_skips_and_reversals_are_refused(current: PipelineState, target: PipelineState) -> None:
    with pytest.raises(IllegalTransitionError):
        check_transition(current, target)


def test_any_lifecycle_state_may_hold_fail_cancel_or_record_a_step_in_place() -> None:
    for state in LIFECYCLE:
        for target in (S.HELD, S.FAILED, S.CANCELLED, state):
            check_transition(state, target)


def test_qa_exits_to_passed_or_rejected() -> None:
    check_transition(S.QA, S.PASSED)
    check_transition(S.QA, S.REJECTED)


def test_terminal_states_admit_nothing() -> None:
    assert {S.REJECTED, S.FAILED, S.CANCELLED} == TERMINAL_STATES
    for state in TERMINAL_STATES:
        for target in PipelineState:
            with pytest.raises(IllegalTransitionError):
                check_transition(state, target)


def test_held_returns_only_to_where_it_was_held_from() -> None:
    check_transition(S.HELD, S.QA, held_from=S.QA)
    check_transition(S.HELD, S.HELD, held_from=S.QA)
    for end in TERMINAL_STATES:
        check_transition(S.HELD, end, held_from=S.QA)
    with pytest.raises(IllegalTransitionError):
        check_transition(S.HELD, S.PASSED, held_from=S.QA)  # a hold never skips its state
    with pytest.raises(IllegalTransitionError):
        check_transition(S.HELD, S.QA)  # held_from unknown: refused, never guessed


def test_paid_steps_and_origins_are_declared() -> None:
    assert dict(PAID_STEP_CAPABILITIES) == {
        Step.GENERATE: "music.generate",
        Step.UPLOAD: "youtube.upload",
        Step.SUBMIT_DISTRIBUTION: "distribution.submit",
    }
    assert set(STEP_ORIGIN_PHASE) == set(Step)
    assert step_activity_name(Step.QA) == "pipeline.step.qa"
