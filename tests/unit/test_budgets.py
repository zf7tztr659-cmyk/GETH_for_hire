from __future__ import annotations

import pytest

from geth_ai.domain import Budget, BudgetExceeded, BudgetLimits

COUNTERS = {
    "provider_calls": "max_provider_calls",
    "tokens": "max_tokens",
    "bytes": "max_bytes",
    "retries": "max_retries",
    "rounds": "max_rounds",
    "recursion_depth": "max_recursion_depth",
    "concurrency": "max_concurrency",
    "wall_time_ms": "max_wall_time_ms",
    "lease_ms": "max_lease_ms",
    "tool_calls": "max_tool_calls",
}


@pytest.mark.requirement("OPS-001")
@pytest.mark.parametrize(("counter", "limit_name"), COUNTERS.items())
def test_all_limits_below_at_and_above_boundary(counter: str, limit_name: str) -> None:
    limits = BudgetLimits(**{limit_name: 2})
    budget = Budget(limits=limits)
    below = budget.consume(**{counter: 1})
    assert getattr(below.usage, counter) == 1
    at = below.consume(**{counter: 1})
    assert getattr(at.usage, counter) == 2
    with pytest.raises(BudgetExceeded):
        at.consume(**{counter: 1})


@pytest.mark.requirement("OPS-001")
def test_child_budget_cannot_reset_or_expand_remaining_parent() -> None:
    parent = Budget(limits=BudgetLimits(max_tokens=10)).consume(tokens=7)
    parent.child(BudgetLimits(max_tokens=3))
    with pytest.raises(BudgetExceeded):
        parent.child(BudgetLimits(max_tokens=4))
    with pytest.raises(ValueError):
        parent.consume(tokens=-1)
