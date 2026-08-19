"""Pure subset checks for optional principal delegation chains."""

from __future__ import annotations

from datetime import datetime
from pathlib import PurePath

from geth_ai.domain.base import ensure_utc
from geth_ai.domain.models import Delegation


class DelegationError(PermissionError):
    pass


def _root_is_within(child_root: str, parent_root: str) -> bool:
    return PurePath(child_root).is_relative_to(PurePath(parent_root))


def validate_delegation_subset(
    parent: Delegation,
    child: Delegation,
    *,
    at: datetime,
) -> None:
    """Reject missing, stale, equal, or authority-expanding subdelegations."""

    instant = ensure_utc(at)
    if child.parent_delegation_id != parent.delegation_id:
        raise DelegationError("subdelegation must reference its parent")
    if child.issuer_id != parent.subject_id:
        raise DelegationError("only the parent subject may subdelegate")
    if not parent.may_delegate:
        raise DelegationError("parent grant does not permit subdelegation")
    if not parent.is_active_at(instant):
        raise DelegationError("parent delegation is inactive or expired")
    if not child.is_active_at(instant):
        raise DelegationError("child delegation is inactive or expired")
    if child.issued_at < parent.issued_at:
        raise DelegationError("child cannot predate its parent")
    if child.expires_at > parent.expires_at:
        raise DelegationError("child expiry exceeds parent expiry")
    if not child.capabilities.issubset(parent.capabilities):
        raise DelegationError("child capabilities exceed parent capabilities")
    if child.may_delegate and not parent.may_delegate:
        raise DelegationError("child cannot gain delegation authority")
    for child_root in child.roots:
        if not any(_root_is_within(child_root, root) for root in parent.roots):
            raise DelegationError("child root exceeds parent roots")
    if not child.budget.is_subset_of(parent.budget):
        raise DelegationError("child budget exceeds parent budget")

    strictly_narrower = (
        child.capabilities != parent.capabilities
        or frozenset(child.roots) != frozenset(parent.roots)
        or child.budget != parent.budget
        or child.expires_at < parent.expires_at
        or (parent.may_delegate and not child.may_delegate)
    )
    if not strictly_narrower:
        raise DelegationError("subdelegation must be a strict authority subset")


def is_valid_delegation_subset(
    parent: Delegation,
    child: Delegation,
    *,
    at: datetime,
) -> bool:
    try:
        validate_delegation_subset(parent, child, at=at)
    except (DelegationError, ValueError):
        return False
    return True


__all__ = [
    "DelegationError",
    "is_valid_delegation_subset",
    "validate_delegation_subset",
]
