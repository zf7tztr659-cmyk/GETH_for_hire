"""Owner authority checks that providers and agents cannot satisfy."""

from __future__ import annotations

from geth_ai.domain.enums import PrincipalKind
from geth_ai.domain.models import Delegation, Principal
from geth_ai.policy.actions import ActionSpec


class AuthorityError(PermissionError):
    pass


def can_approve(principal: Principal, action: ActionSpec) -> bool:
    return (
        principal.active
        and principal.kind is PrincipalKind.OWNER
        and principal.principal_id == action.owner_id
    )


def require_owner_approver(principal: Principal, action: ActionSpec) -> None:
    """Require a principal record loaded by trusted code, not provider text."""

    if not can_approve(principal, action):
        raise AuthorityError("only the bound active owner may approve this action")


def validate_root_delegation(owner: Principal, delegation: Delegation) -> None:
    """Validate the first bounded delegation beneath owner authority."""

    if not owner.active or owner.kind is not PrincipalKind.OWNER:
        raise AuthorityError("root delegation issuer must be an active owner")
    if delegation.issuer_id != owner.principal_id:
        raise AuthorityError("root delegation issuer does not match owner")
    if delegation.parent_delegation_id is not None:
        raise AuthorityError("root delegation cannot name a parent delegation")


__all__ = [
    "AuthorityError",
    "can_approve",
    "require_owner_approver",
    "validate_root_delegation",
]
