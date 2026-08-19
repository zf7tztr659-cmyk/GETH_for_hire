"""The complete, closed run lifecycle."""

from __future__ import annotations

from collections.abc import Mapping

from geth_ai.domain.base import UtcDateTime
from geth_ai.domain.enums import RunState
from geth_ai.domain.models import Run


class IllegalTransition(ValueError):
    def __init__(self, current: RunState, target: RunState) -> None:
        self.current = current
        self.target = target
        super().__init__(f"illegal run transition: {current.value} -> {target.value}")


TERMINAL_RUN_STATES: frozenset[RunState] = frozenset(
    {
        RunState.COMPLETED,
        RunState.FAILED,
        RunState.BLOCKED,
        RunState.CANCELLED,
    }
)

LEGAL_RUN_TRANSITIONS: Mapping[RunState, frozenset[RunState]] = {
    RunState.RECEIVED: frozenset(
        {RunState.TRIAGED, RunState.FAILED, RunState.CANCELLED}
    ),
    RunState.TRIAGED: frozenset(
        {
            RunState.PLANNING,
            RunState.BLOCKED,
            RunState.FAILED,
            RunState.CANCELLED,
        }
    ),
    RunState.PLANNING: frozenset(
        {
            RunState.AWAITING_APPROVAL,
            RunState.VERIFYING,
            RunState.BLOCKED,
            RunState.FAILED,
            RunState.CANCELLED,
        }
    ),
    RunState.AWAITING_APPROVAL: frozenset(
        {RunState.EXECUTING, RunState.BLOCKED, RunState.CANCELLED}
    ),
    RunState.EXECUTING: frozenset(
        {
            RunState.VERIFYING,
            RunState.BLOCKED,
            RunState.FAILED,
            RunState.CANCELLED,
        }
    ),
    RunState.VERIFYING: frozenset(
        {
            RunState.COMPLETED,
            RunState.BLOCKED,
            RunState.FAILED,
            RunState.CANCELLED,
        }
    ),
    RunState.COMPLETED: frozenset(),
    RunState.FAILED: frozenset(),
    RunState.BLOCKED: frozenset(),
    RunState.CANCELLED: frozenset(),
}


def can_transition(current: RunState, target: RunState) -> bool:
    return target in LEGAL_RUN_TRANSITIONS[current]


def require_transition(current: RunState, target: RunState) -> None:
    if not can_transition(current, target):
        raise IllegalTransition(current, target)


def transition_run(
    run: Run,
    target: RunState,
    *,
    at: UtcDateTime,
    expected_version: int | None = None,
    reason: str | None = None,
) -> Run:
    """Validate an edge and return a new versioned projection value."""

    if expected_version is not None and run.version != expected_version:
        raise ValueError("stale run version")
    if at < run.updated_at:
        raise ValueError("transition timestamp precedes current run projection")
    require_transition(run.state, target)

    values = run.model_dump(mode="python")
    values.update(state=target, version=run.version + 1, updated_at=at)
    if target is RunState.CANCELLED:
        values["cancelled_at"] = at
        values["terminal_reason"] = reason or "owner cancellation"
    elif target in TERMINAL_RUN_STATES:
        values["terminal_reason"] = reason
    return Run(**values)


__all__ = [
    "IllegalTransition",
    "LEGAL_RUN_TRANSITIONS",
    "TERMINAL_RUN_STATES",
    "can_transition",
    "require_transition",
    "transition_run",
]
