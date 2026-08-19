"""Fail-closed startup recovery for interrupted local side effects."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from pathlib import PurePath

from geth_ai.application.approvals import action_from_record
from geth_ai.application.clock import Clock
from geth_ai.application.emergency import EmergencyStop
from geth_ai.config import Settings
from geth_ai.domain.enums import ApprovalStatus, RiskClass, RunState, ToolCallState
from geth_ai.domain.transitions import TERMINAL_RUN_STATES, can_transition
from geth_ai.persistence import (
    ApprovalRecord,
    ApprovalRepository,
    ArtifactRepository,
    EventStore,
    RunRepository,
    ToolCallRecord,
    ToolCallRepository,
)
from geth_ai.policy.actions import ActionSpec
from geth_ai.tools.paths import open_regular_file_fd, open_root_fd, validate_relative_path
from geth_ai.tools.protocol import WriteTextInput

RECOVERY_ACTOR = "system:recovery"


class RecoveryError(RuntimeError):
    """Startup recovery could not establish a safe durable state."""


class RecoveryDisposition(StrEnum):
    """Outcome for one interrupted tool call."""

    RECONCILED = "reconciled"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class RecoveryItem:
    call_id: str
    run_id: str
    disposition: RecoveryDisposition
    reason: str


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    """Non-sensitive startup recovery summary."""

    audit_valid: bool
    emergency_stopped: bool
    expired_approvals: int
    calls_examined: int
    reconciled: int
    blocked: int
    items: tuple[RecoveryItem, ...]


@dataclass(frozen=True, slots=True)
class _ReconciliationBinding:
    action: ActionSpec
    approval: ApprovalRecord
    relative_path: str
    expected_content: bytes


@dataclass(slots=True)
class RecoveryService:
    """Reconcile only exact persisted effects; never replay an interrupted tool."""

    settings: Settings
    clock: Clock
    events: EventStore
    runs: RunRepository
    approvals: ApprovalRepository
    tool_calls: ToolCallRepository
    artifacts: ArtifactRepository
    emergency_stop: EmergencyStop

    def recover(self) -> RecoveryReport:
        """Expire stale authority and reconcile durable uncertain calls.

        Audit validation happens before the first mutation. Interrupted ``RUNNING``
        calls are durably changed to ``UNCERTAIN`` before observation. Recovery has
        no provider, broker, or tool-adapter reference, so it cannot retry an effect.
        """

        try:
            integrity = self.events.verify()
        except Exception as exc:
            raise RecoveryError("audit chain could not be read; recovery stopped") from exc
        if not integrity.valid:
            raise RecoveryError("audit chain is invalid; recovery stopped before mutation")

        try:
            emergency_stopped = self.emergency_stop.is_active()
            expired = self.approvals.expire_due(at=self.clock.now())
            calls = self._uncertain_calls()
        except Exception as exc:
            raise RecoveryError("startup state could not be prepared safely") from exc

        items: list[RecoveryItem] = []
        for call in calls:
            current = call
            if call.state.casefold() == ToolCallState.RUNNING.value:
                try:
                    current = self.tool_calls.change_state(
                        call_id=call.call_id,
                        actor_id=RECOVERY_ACTOR,
                        state=ToolCallState.UNCERTAIN.value,
                        error_summary="process ended before a durable tool result",
                        at=self.clock.now(),
                    )
                except Exception as exc:
                    raise RecoveryError(
                        "an interrupted tool call could not be marked uncertain"
                    ) from exc

            if emergency_stopped:
                self._block(current, reason="emergency_stop_active")
                items.append(self._blocked_item(current, "emergency_stop_active"))
                continue

            try:
                binding = self._binding(current)
                run = self.runs.require(current.run_id)
                if RunState(run.state.casefold()) not in {
                    RunState.EXECUTING,
                    RunState.VERIFYING,
                }:
                    self._block(current, reason="run_not_recoverable")
                    items.append(self._blocked_item(current, "run_not_recoverable"))
                    continue
                self.emergency_stop.require_clear()
                exact = self._observe_exact_postcondition(binding)
                self.emergency_stop.require_clear()
                latest_run = self.runs.require(current.run_id)
                if RunState(latest_run.state.casefold()) is RunState.CANCELLED:
                    self._block(current, reason="run_cancelled")
                    items.append(self._blocked_item(current, "run_cancelled"))
                    continue
                if not exact:
                    self._block(current, reason="exact_postcondition_mismatch")
                    items.append(
                        self._blocked_item(current, "exact_postcondition_mismatch")
                    )
                    continue
                self._commit_reconciliation(current, binding)
            except Exception:
                self._block(current, reason="persisted_authority_or_effect_invalid")
                items.append(
                    self._blocked_item(current, "persisted_authority_or_effect_invalid")
                )
                continue
            items.append(
                RecoveryItem(
                    call_id=current.call_id,
                    run_id=current.run_id,
                    disposition=RecoveryDisposition.RECONCILED,
                    reason="exact_persisted_postcondition_observed",
                )
            )

        reconciled = sum(
            item.disposition is RecoveryDisposition.RECONCILED for item in items
        )
        blocked = len(items) - reconciled
        return RecoveryReport(
            audit_valid=True,
            emergency_stopped=emergency_stopped,
            expired_approvals=expired,
            calls_examined=len(items),
            reconciled=reconciled,
            blocked=blocked,
            items=tuple(items),
        )

    def _uncertain_calls(self) -> tuple[ToolCallRecord, ...]:
        already_blocked = {
            str(event.payload["call_id"])
            for event in self.events.list()
            if event.event_type == "RecoveryBlocked"
            and isinstance(event.payload.get("call_id"), str)
        }
        connection = self.events.database.connect()
        try:
            rows = connection.execute(
                """
                SELECT call_id FROM tool_calls
                WHERE lower(state) IN ('running', 'uncertain')
                ORDER BY last_sequence, call_id
                """
            ).fetchall()
        finally:
            connection.close()
        return tuple(
            self.tool_calls.require(str(row["call_id"]))
            for row in rows
            if str(row["call_id"]) not in already_blocked
        )

    def _binding(self, call: ToolCallRecord) -> _ReconciliationBinding:
        if call.approval_id is None:
            raise ValueError("write call has no approval")
        approval = self.approvals.require(call.approval_id)
        action = action_from_record(approval)
        status = ApprovalStatus(approval.status.casefold())
        if status not in {ApprovalStatus.CLAIMED, ApprovalStatus.CONSUMED}:
            raise ValueError("approval was not claimed before interruption")
        if approval.claimed_by is None or approval.claimed_by != str(action.requester_id):
            raise ValueError("approval claimant does not match the exact action")
        if (
            call.run_id != approval.run_id
            or call.run_id != str(action.run_id)
            or call.tool_name != action.tool_name
            or call.action_digest != approval.action_digest
            or call.action_digest != action.digest
            or approval.policy_version != action.policy_version
        ):
            raise ValueError("durable action bindings disagree")
        if (
            action.tool_name != "sandbox.write_text"
            or action.tool_schema_version != "1"
            or action.risk_class is not RiskClass.REVERSIBLE_WORKSPACE_WRITE
            or action.root != str(self.settings.sandbox_root)
            or action.expected_prior_state != "absent"
            or action.overwrite
        ):
            raise ValueError("action is not a recoverable no-overwrite sandbox write")

        request = WriteTextInput.model_validate(action.decoded_arguments())
        relative = validate_relative_path(request.path)
        expected_target = str(PurePath(self.settings.sandbox_root) / relative.text)
        if action.target != expected_target:
            raise ValueError("action target does not match its persisted arguments")
        expected_content = request.content.encode("utf-8")
        if (
            len(expected_content) > self.settings.budgets.max_read_bytes
            or action.content_length != len(expected_content)
            or action.content_sha256 != hashlib.sha256(expected_content).hexdigest()
        ):
            raise ValueError("persisted content postcondition is invalid")
        return _ReconciliationBinding(
            action=action,
            approval=approval,
            relative_path=relative.text,
            expected_content=expected_content,
        )

    def _observe_exact_postcondition(self, binding: _ReconciliationBinding) -> bool:
        relative = validate_relative_path(binding.relative_path)
        try:
            root_fd = open_root_fd(self.settings.sandbox_root)
            try:
                descriptor = open_regular_file_fd(root_fd, relative.parts)
                try:
                    content = _read_bounded(
                        descriptor, maximum=len(binding.expected_content)
                    )
                finally:
                    os.close(descriptor)
            finally:
                os.close(root_fd)
        except (OSError, PermissionError):
            return False
        return content is not None and content == binding.expected_content

    def _commit_reconciliation(
        self,
        call: ToolCallRecord,
        binding: _ReconciliationBinding,
    ) -> None:
        """Commit recoverable metadata with SUCCEEDED last for restart idempotence."""

        latest_approval = self.approvals.require(binding.approval.approval_id)
        status = ApprovalStatus(latest_approval.status.casefold())
        if status is ApprovalStatus.CLAIMED:
            assert latest_approval.claimed_by is not None
            self.approvals.consume(
                latest_approval.approval_id,
                claimed_by=latest_approval.claimed_by,
                at=self.clock.now(),
            )
        elif status is not ApprovalStatus.CONSUMED:
            raise ValueError("approval changed during reconciliation")

        digest = hashlib.sha256(binding.expected_content).hexdigest()
        artifact_id = f"recovery:{call.call_id}"
        artifact = self.artifacts.get(artifact_id)
        if artifact is None:
            self.artifacts.record(
                artifact_id=artifact_id,
                run_id=call.run_id,
                actor_id=RECOVERY_ACTOR,
                kind="sandbox_text",
                locator=f"sandbox/{binding.relative_path}",
                digest=digest,
                byte_length=len(binding.expected_content),
                tool_call_id=call.call_id,
                verification_status="UNVERIFIED",
                at=self.clock.now(),
            )
        elif (
            artifact.run_id != call.run_id
            or artifact.tool_call_id != call.call_id
            or artifact.digest != digest
            or artifact.byte_length != len(binding.expected_content)
        ):
            raise ValueError("recovery artifact metadata conflicts")

        run = self.runs.require(call.run_id)
        state = RunState(run.state.casefold())
        if state is RunState.EXECUTING:
            self.emergency_stop.require_clear()
            self.runs.transition(
                run_id=run.run_id,
                state=RunState.VERIFYING.value,
                actor_id=RECOVERY_ACTOR,
                expected_state=run.state,
                expected_version=run.version,
                reason="exact interrupted effect recovered; independent verification pending",
                lease_expires_at=self.clock.now()
                + timedelta(seconds=self.settings.budgets.lease_seconds),
                created_at=self.clock.now(),
            )
        elif state is not RunState.VERIFYING:
            raise ValueError("run changed during reconciliation")

        self.emergency_stop.require_clear()
        latest_run = self.runs.require(call.run_id)
        if RunState(latest_run.state.casefold()) is RunState.CANCELLED:
            raise ValueError("run was cancelled during reconciliation")
        self.tool_calls.change_state(
            call_id=call.call_id,
            actor_id=RECOVERY_ACTOR,
            state=ToolCallState.SUCCEEDED.value,
            result={
                "path": binding.relative_path,
                "sha256": digest,
                "byte_length": len(binding.expected_content),
                "status": "verified",
            },
            at=self.clock.now(),
        )

    def _block(self, call: ToolCallRecord, *, reason: str) -> None:
        self.events.append(
            run_id=call.run_id,
            event_type="RecoveryBlocked",
            actor_id=RECOVERY_ACTOR,
            payload={"call_id": call.call_id, "reason": reason},
            created_at=self.clock.now(),
        )
        run = self.runs.get(call.run_id)
        if run is None:
            return
        state = RunState(run.state.casefold())
        if state in TERMINAL_RUN_STATES:
            return
        target = RunState.BLOCKED if can_transition(state, RunState.BLOCKED) else RunState.FAILED
        if not can_transition(state, target):
            return
        self.runs.transition(
            run_id=run.run_id,
            state=target.value,
            actor_id=RECOVERY_ACTOR,
            expected_state=run.state,
            expected_version=run.version,
            reason="uncertain side effect requires owner review",
            created_at=self.clock.now(),
        )

    @staticmethod
    def _blocked_item(call: ToolCallRecord, reason: str) -> RecoveryItem:
        return RecoveryItem(
            call_id=call.call_id,
            run_id=call.run_id,
            disposition=RecoveryDisposition.BLOCKED,
            reason=reason,
        )


def _read_bounded(descriptor: int, *, maximum: int) -> bytes | None:
    chunks: list[bytes] = []
    remaining = maximum + 1
    while remaining:
        chunk = os.read(descriptor, min(65_536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    content = b"".join(chunks)
    if len(content) > maximum:
        return None
    return content


__all__ = [
    "RECOVERY_ACTOR",
    "RecoveryDisposition",
    "RecoveryError",
    "RecoveryItem",
    "RecoveryReport",
    "RecoveryService",
]
