"""Trusted owner approval service around exact persisted actions."""

from __future__ import annotations

import json
from dataclasses import dataclass

from geth_ai.application.clock import Clock
from geth_ai.domain.models import Principal
from geth_ai.persistence.repositories import (
    ApprovalArgumentsUnavailableError,
    ApprovalRecord,
    ApprovalRepository,
)
from geth_ai.policy.actions import ActionSpec
from geth_ai.policy.authority import AuthorityError, require_owner_approver


def action_from_record(record: ApprovalRecord) -> ActionSpec:
    """Revalidate a persisted action through Pydantic's strict JSON boundary."""

    if not record.action_available:
        raise ApprovalArgumentsUnavailableError(
            "exact action arguments are unavailable; issue a fresh approval"
        )
    return ActionSpec.model_validate_json(
        json.dumps(record.action, sort_keys=True, separators=(",", ":"))
    )


@dataclass(slots=True)
class ApprovalService:
    repository: ApprovalRepository
    clock: Clock

    def request(self, *, approval_id: str, action: ActionSpec) -> ApprovalRecord:
        if action.expires_at <= self.clock.now():
            raise ValueError("cannot request an already expired approval")
        return self.repository.request(
            approval_id=approval_id,
            run_id=str(action.run_id),
            work_item_id=(
                None if action.work_item_id is None else str(action.work_item_id)
            ),
            requester_id=str(action.requester_id),
            owner_id=str(action.owner_id),
            action=action,
            action_digest=action.digest,
            policy_version=action.policy_version,
            expires_at=action.expires_at,
            nonce=action.nonce,
            actor_id=str(action.requester_id),
            created_at=action.created_at,
        )

    def approve(self, approval_id: str, *, owner: Principal) -> ApprovalRecord:
        current = self.repository.require(approval_id)
        action = action_from_record(current)
        try:
            require_owner_approver(owner, action)
        except AuthorityError:
            self._record_rejected_decision(current, owner, decision="approve")
            raise
        return self.repository.approve(
            approval_id,
            owner_id=str(owner.principal_id),
            at=self.clock.now(),
        )

    def deny(
        self,
        approval_id: str,
        *,
        owner: Principal,
        reason: str = "owner denied the exact action",
    ) -> ApprovalRecord:
        current = self.repository.require(approval_id)
        action = action_from_record(current)
        try:
            require_owner_approver(owner, action)
        except AuthorityError:
            self._record_rejected_decision(current, owner, decision="deny")
            raise
        return self.repository.deny(
            approval_id,
            owner_id=str(owner.principal_id),
            reason=reason,
            at=self.clock.now(),
        )

    def _record_rejected_decision(
        self,
        approval: ApprovalRecord,
        owner: Principal,
        *,
        decision: str,
    ) -> None:
        self.repository.events.append(
            run_id=approval.run_id,
            event_type="ApprovalDecisionRejected",
            actor_id=str(owner.principal_id),
            payload={
                "approval_id": approval.approval_id,
                "decision": decision,
                "reason": "principal is not the exact active owner",
            },
            created_at=self.clock.now(),
        )
