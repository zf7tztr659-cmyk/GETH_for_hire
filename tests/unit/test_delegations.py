from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from geth_ai.domain import BudgetLimits, Delegation, DelegationId, PrincipalId
from geth_ai.policy import DelegationError, validate_delegation_subset

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _parent() -> Delegation:
    return Delegation(
        delegation_id=DelegationId(uuid4()),
        issuer_id=PrincipalId(uuid4()),
        subject_id=PrincipalId(uuid4()),
        capabilities=frozenset({"fs.read", "fs.list"}),
        roots=("/workspace",),
        budget=BudgetLimits(max_bytes=1_000, max_tool_calls=5),
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(hours=1),
        may_delegate=True,
    )


def _child(parent: Delegation, **changes: object) -> Delegation:
    values: dict[str, object] = {
        "delegation_id": DelegationId(uuid4()),
        "issuer_id": parent.subject_id,
        "subject_id": PrincipalId(uuid4()),
        "parent_delegation_id": parent.delegation_id,
        "capabilities": frozenset({"fs.read"}),
        "roots": ("/workspace/docs",),
        "budget": BudgetLimits(max_bytes=500, max_tool_calls=2),
        "issued_at": NOW,
        "expires_at": NOW + timedelta(minutes=30),
        "may_delegate": False,
    }
    values.update(changes)
    return Delegation(**values)  # type: ignore[arg-type]


@pytest.mark.requirement("GOV-002")
def test_delegation_must_be_strict_subset() -> None:
    parent = _parent()
    validate_delegation_subset(parent, _child(parent), at=NOW)

    with pytest.raises(DelegationError, match="capabilities"):
        validate_delegation_subset(
            parent,
            _child(parent, capabilities=frozenset({"fs.read", "network"})),
            at=NOW,
        )
    with pytest.raises(DelegationError, match="root"):
        validate_delegation_subset(parent, _child(parent, roots=("/outside",)), at=NOW)
    with pytest.raises(DelegationError, match="budget"):
        validate_delegation_subset(
            parent,
            _child(parent, budget=BudgetLimits(max_bytes=2_000, max_tool_calls=2)),
            at=NOW,
        )
    with pytest.raises(DelegationError, match="expiry"):
        validate_delegation_subset(
            parent,
            _child(parent, expires_at=parent.expires_at + timedelta(seconds=1)),
            at=NOW,
        )
    with pytest.raises(DelegationError, match="parent"):
        validate_delegation_subset(
            parent, _child(parent, parent_delegation_id=None), at=NOW
        )

    equal = _child(
        parent,
        capabilities=parent.capabilities,
        roots=parent.roots,
        budget=parent.budget,
        expires_at=parent.expires_at,
        may_delegate=True,
    )
    with pytest.raises(DelegationError, match="strict"):
        validate_delegation_subset(parent, equal, at=NOW)
