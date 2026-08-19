from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from geth_ai.domain import (
    LEGAL_RUN_TRANSITIONS,
    Budget,
    IllegalTransition,
    PrincipalId,
    Run,
    RunId,
    RunState,
    can_transition,
    transition_run,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _run(state: RunState) -> Run:
    return Run(
        run_id=RunId(uuid4()),
        owner_id=PrincipalId(uuid4()),
        objective="test objective",
        workspace_root="/workspace",
        state=state,
        version=4,
        budget=Budget(),
        created_at=NOW,
        updated_at=NOW,
        lease_expires_at=NOW + timedelta(minutes=5),
        cancelled_at=NOW if state is RunState.CANCELLED else None,
        terminal_reason="stopped" if state is RunState.CANCELLED else None,
    )


@pytest.mark.requirement("FUN-001")
@pytest.mark.parametrize("current", list(RunState))
@pytest.mark.parametrize("target", list(RunState))
def test_complete_legal_and_illegal_transition_matrix(
    current: RunState, target: RunState
) -> None:
    expected = target in LEGAL_RUN_TRANSITIONS[current]
    assert can_transition(current, target) is expected
    if expected:
        changed = transition_run(
            _run(current),
            target,
            at=NOW + timedelta(seconds=1),
            expected_version=4,
            reason="test terminal reason",
        )
        assert changed.state is target
        assert changed.version == 5
        if target is RunState.CANCELLED:
            assert changed.cancelled_at == NOW + timedelta(seconds=1)
    else:
        with pytest.raises(IllegalTransition):
            transition_run(_run(current), target, at=NOW + timedelta(seconds=1))


@pytest.mark.requirement("FUN-001")
def test_optimistic_version_and_time_checks_fail_closed() -> None:
    run = _run(RunState.RECEIVED)
    with pytest.raises(ValueError, match="stale"):
        transition_run(run, RunState.TRIAGED, at=NOW, expected_version=3)
    with pytest.raises(ValueError, match="precedes"):
        transition_run(run, RunState.TRIAGED, at=NOW - timedelta(seconds=1))
