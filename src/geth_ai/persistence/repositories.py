"""Typed repository APIs layered on the canonical event journal."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from geth_ai.policy.redaction import redact_text

from .audit_safety import (
    UnsafeApprovalAction,
    approval_action_metadata,
    reject_secret_shaped_action,
)
from .canonical_json import canonical_dumps, sha256_hex, to_jsonable
from .event_store import EventRecord, EventStore


class RepositoryError(RuntimeError):
    pass


class NotFoundError(RepositoryError):
    pass


class ConflictError(RepositoryError):
    pass


class ApprovalError(RepositoryError):
    pass


class ApprovalExpiredError(ApprovalError):
    pass


class ApprovalMismatchError(ApprovalError):
    pass


class ApprovalArgumentsUnavailableError(ApprovalError):
    """Exact arguments were deliberately absent from a journal-only rebuild."""


class ApprovalUnsafeActionError(ApprovalError):
    """An exact action would place secret-shaped data in ordinary state."""


class MemoryValidationError(RepositoryError):
    pass


@dataclass(frozen=True, slots=True)
class RunRecord:
    run_id: str
    owner_id: str
    state: str
    objective_summary: str
    version: int
    created_at: str
    updated_at: str
    cancelled_at: str | None
    lease_expires_at: str | None
    terminal_reason: str | None
    last_sequence: int


@dataclass(frozen=True, slots=True)
class MessageRecord:
    message_id: str
    run_id: str
    schema_version: int
    sender_id: str
    sender_role: str
    recipient: str
    correlation_id: str
    causation_id: str | None
    payload_type: str
    payload_hash: str
    payload: dict[str, Any]
    created_at: str
    sequence: int


@dataclass(frozen=True, slots=True)
class WorkItemRecord:
    work_item_id: str
    run_id: str
    objective_summary: str
    acceptance_criteria: tuple[str, ...]
    assigned_role: str
    dependencies: tuple[str, ...]
    state: str
    lease_expires_at: str
    version: int
    last_sequence: int


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    approval_id: str
    run_id: str
    work_item_id: str | None
    requester_id: str
    owner_id: str
    action_digest: str
    action: dict[str, Any]
    action_available: bool
    policy_version: str
    expires_at: str
    status: str
    nonce: str
    claimed_by: str | None
    claimed_at: str | None
    consumed_at: str | None
    decided_at: str | None
    version: int
    last_sequence: int


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    call_id: str
    run_id: str
    approval_id: str | None
    tool_name: str
    action_digest: str
    state: str
    attempt: int
    started_at: str | None
    finished_at: str | None
    result: dict[str, Any] | None
    error_summary: str | None
    last_sequence: int


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    artifact_id: str
    run_id: str
    tool_call_id: str | None
    kind: str
    locator: str
    digest: str
    byte_length: int
    verification_status: str
    created_at: str
    last_sequence: int


@dataclass(frozen=True, slots=True)
class BudgetRecord:
    run_id: str
    limits: dict[str, Any]
    usage: dict[str, Any]
    version: int
    last_sequence: int


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    memory_id: str
    owner_id: str
    category: str
    content: str | None
    content_hash: str
    sensitivity: str
    provenance: dict[str, Any] | None
    provenance_hash: str
    status: str
    supersedes_id: str | None
    created_at: str
    updated_at: str
    forgotten_at: str | None
    last_sequence: int


class RunRepository:
    def __init__(self, events: EventStore) -> None:
        self.events = events

    def create(
        self,
        *,
        run_id: str,
        owner_id: str,
        objective_summary: str,
        actor_id: str | None = None,
        state: str = "RECEIVED",
        lease_expires_at: datetime | str | None = None,
        created_at: datetime | str | None = None,
    ) -> RunRecord:
        self.events.append(
            run_id=run_id,
            event_type="RunCreated",
            actor_id=actor_id or owner_id,
            payload={
                "owner_id": owner_id,
                "state": _enum_text(state),
                "objective_summary": objective_summary,
                "lease_expires_at": _time_text(lease_expires_at),
            },
            created_at=created_at,
        )
        return self.require(run_id)

    def transition(
        self,
        *,
        run_id: str,
        state: str,
        actor_id: str,
        expected_state: str | None = None,
        expected_version: int | None = None,
        reason: str | None = None,
        lease_expires_at: datetime | str | None = None,
        created_at: datetime | str | None = None,
    ) -> RunRecord:
        with self.events.database.transaction() as connection:
            current = connection.execute(
                "SELECT state, version FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if current is None:
                raise NotFoundError(f"run {run_id} not found")
            if expected_state is not None and current["state"] != _enum_text(expected_state):
                raise ConflictError("run state changed concurrently")
            if expected_version is not None and int(current["version"]) != expected_version:
                raise ConflictError("run version changed concurrently")
            self.events.append_in_transaction(
                connection,
                run_id=run_id,
                event_type="RunStateChanged",
                actor_id=actor_id,
                payload={
                    "state": _enum_text(state),
                    "reason": reason,
                    "lease_expires_at": _time_text(lease_expires_at),
                },
                created_at=created_at,
            )
        return self.require(run_id)

    def cancel(
        self,
        *,
        run_id: str,
        actor_id: str,
        reason: str = "owner cancellation",
        created_at: datetime | str | None = None,
    ) -> RunRecord:
        with self.events.database.transaction() as connection:
            self.events.append_in_transaction(
                connection,
                run_id=run_id,
                event_type="RunCancelled",
                actor_id=actor_id,
                payload={"reason": reason},
                created_at=created_at,
            )
            approvals = connection.execute(
                """
                SELECT approval_id FROM approvals
                WHERE run_id = ? AND status IN ('PENDING','APPROVED')
                ORDER BY approval_id
                """,
                (run_id,),
            ).fetchall()
            for approval in approvals:
                self.events.append_in_transaction(
                    connection,
                    run_id=run_id,
                    event_type="ApprovalCancelled",
                    actor_id=actor_id,
                    payload={
                        "approval_id": str(approval["approval_id"]),
                        "reason": "run cancelled",
                    },
                    created_at=created_at,
                )
        return self.require(run_id)

    def get(self, run_id: str) -> RunRecord | None:
        row = _fetch_one(self.events, "SELECT * FROM runs WHERE run_id = ?", (run_id,))
        return None if row is None else _run_record(row)

    def require(self, run_id: str) -> RunRecord:
        record = self.get(run_id)
        if record is None:
            raise NotFoundError(f"run {run_id} not found")
        return record

    def list(self) -> tuple[RunRecord, ...]:
        rows = _fetch_all(self.events, "SELECT * FROM runs ORDER BY created_at, run_id")
        return tuple(_run_record(row) for row in rows)


class MessageRepository:
    def __init__(self, events: EventStore) -> None:
        self.events = events

    def record(
        self,
        *,
        message_id: str,
        run_id: str,
        sender_id: str,
        sender_role: str,
        recipient: str,
        correlation_id: str,
        causation_id: str | None,
        payload_type: str,
        payload: Mapping[str, Any] | Any,
        actor_id: str,
        payload_hash: str | None = None,
        schema_version: int = 1,
        created_at: datetime | str | None = None,
    ) -> MessageRecord:
        normalized = to_jsonable(payload)
        if not isinstance(normalized, dict):
            raise ValueError("message payload must be an object")
        payload_hash = payload_hash or sha256_hex(canonical_dumps(normalized))
        self.events.append(
            run_id=run_id,
            event_type="MessageRecorded",
            actor_id=actor_id,
            payload={
                "message_id": message_id,
                "schema_version": schema_version,
                "sender_id": sender_id,
                "sender_role": _enum_text(sender_role),
                "recipient": recipient,
                "correlation_id": correlation_id,
                "causation_id": causation_id,
                "payload_type": payload_type,
                "payload_hash": payload_hash,
                "payload": normalized,
            },
            created_at=created_at,
        )
        record = self.get(message_id)
        assert record is not None
        return record

    def record_envelope(self, envelope: Any, *, actor_id: str | None = None) -> MessageRecord:
        payload = envelope.decoded_payload()
        return self.record(
            message_id=str(envelope.message_id),
            run_id=str(envelope.run_id),
            sender_id=str(envelope.sender_id),
            sender_role=_enum_text(envelope.sender_role),
            recipient=str(envelope.recipient),
            correlation_id=str(envelope.correlation_id),
            causation_id=(
                None if envelope.causation_id is None else str(envelope.causation_id)
            ),
            payload_type=str(envelope.payload_type),
            payload=payload,
            actor_id=actor_id or str(envelope.sender_id),
            payload_hash=str(envelope.payload_sha256),
            schema_version=int(envelope.schema_version),
            created_at=envelope.created_at,
        )

    def get(self, message_id: str) -> MessageRecord | None:
        row = _fetch_one(
            self.events, "SELECT * FROM messages WHERE message_id = ?", (message_id,)
        )
        return None if row is None else _message_record(row)

    def list_for_run(self, run_id: str) -> tuple[MessageRecord, ...]:
        rows = _fetch_all(
            self.events,
            "SELECT * FROM messages WHERE run_id = ? ORDER BY sequence",
            (run_id,),
        )
        return tuple(_message_record(row) for row in rows)


class WorkItemRepository:
    def __init__(self, events: EventStore) -> None:
        self.events = events

    def create(
        self,
        *,
        work_item_id: str,
        run_id: str,
        actor_id: str,
        objective_summary: str,
        acceptance_criteria: tuple[str, ...],
        assigned_role: str,
        lease_expires_at: datetime | str,
        dependencies: tuple[str, ...] = (),
        state: str = "pending",
        at: datetime | str | None = None,
    ) -> WorkItemRecord:
        if not acceptance_criteria:
            raise ValueError("work item acceptance criteria are required")
        self.events.append(
            run_id=run_id,
            event_type="WorkItemCreated",
            actor_id=actor_id,
            payload={
                "work_item_id": work_item_id,
                "objective_summary": objective_summary,
                "acceptance_criteria": acceptance_criteria,
                "assigned_role": _enum_text(assigned_role),
                "dependencies": dependencies,
                "state": _enum_text(state),
                "lease_expires_at": _required_time_text(lease_expires_at),
            },
            created_at=at,
        )
        return self.require(work_item_id)

    def change_state(
        self,
        *,
        work_item_id: str,
        actor_id: str,
        state: str,
        lease_expires_at: datetime | str | None = None,
        at: datetime | str | None = None,
    ) -> WorkItemRecord:
        current = self.require(work_item_id)
        self.events.append(
            run_id=current.run_id,
            event_type="WorkItemStateChanged",
            actor_id=actor_id,
            payload={
                "work_item_id": work_item_id,
                "state": _enum_text(state),
                "lease_expires_at": _time_text(lease_expires_at),
            },
            created_at=at,
        )
        return self.require(work_item_id)

    def get(self, work_item_id: str) -> WorkItemRecord | None:
        row = _fetch_one(
            self.events,
            "SELECT * FROM work_items WHERE work_item_id = ?",
            (work_item_id,),
        )
        return None if row is None else _work_item_record(row)

    def require(self, work_item_id: str) -> WorkItemRecord:
        record = self.get(work_item_id)
        if record is None:
            raise NotFoundError(f"work item {work_item_id} not found")
        return record

    def list_for_run(self, run_id: str) -> tuple[WorkItemRecord, ...]:
        rows = _fetch_all(
            self.events,
            "SELECT * FROM work_items WHERE run_id = ? ORDER BY last_sequence",
            (run_id,),
        )
        return tuple(_work_item_record(row) for row in rows)


class ApprovalRepository:
    def __init__(self, events: EventStore) -> None:
        self.events = events

    def request(
        self,
        *,
        approval_id: str,
        run_id: str,
        requester_id: str,
        owner_id: str,
        action: Mapping[str, Any] | Any,
        action_digest: str,
        policy_version: str,
        expires_at: datetime | str,
        nonce: str,
        work_item_id: str | None = None,
        actor_id: str | None = None,
        created_at: datetime | str | None = None,
    ) -> ApprovalRecord:
        normalized = to_jsonable(action)
        if not isinstance(normalized, dict):
            raise ValueError("action must be an object")
        try:
            reject_secret_shaped_action(normalized)
        except UnsafeApprovalAction as exc:
            raise ApprovalUnsafeActionError(str(exc)) from exc
        if redact_text(nonce) != nonce:
            raise ApprovalUnsafeActionError("secret-shaped approval nonce is not persistable")
        exact_action_json = canonical_dumps(normalized)

        def retain_exact_action(
            connection: sqlite3.Connection, event: EventRecord
        ) -> None:
            # The exact action is ordinary mutable projection state, never event
            # payload. It survives process restart, but not a journal-only rebuild.
            cursor = connection.execute(
                """
                UPDATE approvals
                SET action_json = ?, action_available = 1
                WHERE approval_id = ? AND last_sequence = ?
                """,
                (exact_action_json, approval_id, event.sequence),
            )
            if cursor.rowcount != 1:
                raise ConflictError("exact approval projection was not retained")

        self.events.append(
            run_id=run_id,
            event_type="ApprovalRequested",
            actor_id=actor_id or requester_id,
            payload={
                "approval_id": approval_id,
                "work_item_id": work_item_id,
                "requester_id": requester_id,
                "owner_id": owner_id,
                "action_digest": action_digest,
                "action_metadata": approval_action_metadata(normalized),
                "policy_version": policy_version,
                "expires_at": _required_time_text(expires_at),
                "nonce": nonce,
            },
            created_at=created_at,
            projector=retain_exact_action,
        )
        return self.require(approval_id)

    def approve(
        self,
        approval_id: str,
        *,
        owner_id: str,
        at: datetime | str | None = None,
    ) -> ApprovalRecord:
        now = _required_time_text(at or datetime.now(UTC))
        expired = False
        with self.events.database.transaction() as connection:
            row = _approval_row(connection, approval_id)
            _require_owner(row, owner_id)
            if row["status"] != "PENDING":
                raise ApprovalError(f"approval is {row['status']}, not PENDING")
            if not bool(row["action_available"]):
                raise ApprovalArgumentsUnavailableError(
                    "exact action arguments are unavailable after projection rebuild"
                )
            expired = _is_expired(str(row["expires_at"]), now)
            self.events.append_in_transaction(
                connection,
                run_id=str(row["run_id"]),
                event_type="ApprovalExpired" if expired else "ApprovalApproved",
                actor_id=owner_id,
                payload={"approval_id": approval_id},
                created_at=now,
            )
        if expired:
            raise ApprovalExpiredError("approval expired before decision")
        return self.require(approval_id)

    def deny(
        self,
        approval_id: str,
        *,
        owner_id: str,
        reason: str | None = None,
        at: datetime | str | None = None,
    ) -> ApprovalRecord:
        with self.events.database.transaction() as connection:
            row = _approval_row(connection, approval_id)
            _require_owner(row, owner_id)
            self.events.append_in_transaction(
                connection,
                run_id=str(row["run_id"]),
                event_type="ApprovalDenied",
                actor_id=owner_id,
                payload={"approval_id": approval_id, "reason": reason},
                created_at=at,
            )
        return self.require(approval_id)

    def cancel(
        self,
        approval_id: str,
        *,
        actor_id: str,
        at: datetime | str | None = None,
    ) -> ApprovalRecord:
        with self.events.database.transaction() as connection:
            row = _approval_row(connection, approval_id)
            self.events.append_in_transaction(
                connection,
                run_id=str(row["run_id"]),
                event_type="ApprovalCancelled",
                actor_id=actor_id,
                payload={"approval_id": approval_id},
                created_at=at,
            )
        return self.require(approval_id)

    def claim(
        self,
        approval_id: str,
        *,
        run_id: str,
        requester_id: str,
        action_digest: str,
        claimed_by: str,
        action: Mapping[str, Any] | Any | None = None,
        at: datetime | str | None = None,
    ) -> ApprovalRecord:
        now = _required_time_text(at or datetime.now(UTC))
        rejection: str | None = None
        rejection_type: type[ApprovalError] = ApprovalMismatchError
        with self.events.database.transaction() as connection:
            row = _approval_row(connection, approval_id)
            if row["status"] != "APPROVED":
                rejection = f"approval is {row['status']}, not APPROVED"
            elif _is_expired(str(row["expires_at"]), now):
                self.events.append_in_transaction(
                    connection,
                    run_id=str(row["run_id"]),
                    event_type="ApprovalExpired",
                    actor_id=claimed_by,
                    payload={"approval_id": approval_id},
                    created_at=now,
                )
                rejection = "approval expired"
                rejection_type = ApprovalExpiredError
            elif str(row["run_id"]) != run_id:
                rejection = "approval belongs to another run"
            elif str(row["requester_id"]) != requester_id:
                rejection = "approval belongs to another requester"
            elif str(row["action_digest"]) != action_digest:
                rejection = "action digest changed"
            elif not bool(row["action_available"]):
                rejection = "exact action arguments are unavailable after projection rebuild"
                rejection_type = ApprovalArgumentsUnavailableError
            elif action is not None and canonical_dumps(action) != str(row["action_json"]):
                rejection = "canonical action changed"

            if rejection is None:
                self.events.append_in_transaction(
                    connection,
                    run_id=run_id,
                    event_type="ApprovalClaimed",
                    actor_id=claimed_by,
                    payload={"approval_id": approval_id, "claimed_by": claimed_by},
                    created_at=now,
                )
            elif row["status"] != "EXPIRED" and rejection != "approval expired":
                self.events.append_in_transaction(
                    connection,
                    run_id=str(row["run_id"]),
                    event_type="ApprovalClaimRejected",
                    actor_id=claimed_by,
                    payload={
                        "approval_id": approval_id,
                        "reason": rejection,
                        "presented_action_digest": action_digest,
                    },
                    created_at=now,
                )
        if rejection is not None:
            raise rejection_type(rejection)
        return self.require(approval_id)

    def consume(
        self,
        approval_id: str,
        *,
        claimed_by: str,
        at: datetime | str | None = None,
    ) -> ApprovalRecord:
        with self.events.database.transaction() as connection:
            row = _approval_row(connection, approval_id)
            self.events.append_in_transaction(
                connection,
                run_id=str(row["run_id"]),
                event_type="ApprovalConsumed",
                actor_id=claimed_by,
                payload={"approval_id": approval_id, "claimed_by": claimed_by},
                created_at=at,
            )
        return self.require(approval_id)

    def expire_due(self, *, at: datetime | str | None = None) -> int:
        now = _required_time_text(at or datetime.now(UTC))
        expired = 0
        with self.events.database.transaction() as connection:
            rows = connection.execute(
                """
                SELECT approval_id, run_id FROM approvals
                WHERE status IN ('PENDING','APPROVED') AND expires_at <= ?
                ORDER BY approval_id
                """,
                (now,),
            ).fetchall()
            for row in rows:
                self.events.append_in_transaction(
                    connection,
                    run_id=str(row["run_id"]),
                    event_type="ApprovalExpired",
                    actor_id="system:expiry",
                    payload={"approval_id": str(row["approval_id"])},
                    created_at=now,
                )
                expired += 1
        return expired

    def get(self, approval_id: str) -> ApprovalRecord | None:
        row = _fetch_one(
            self.events,
            "SELECT * FROM approvals WHERE approval_id = ?",
            (approval_id,),
        )
        return None if row is None else _approval_record(row)

    def require(self, approval_id: str) -> ApprovalRecord:
        record = self.get(approval_id)
        if record is None:
            raise NotFoundError(f"approval {approval_id} not found")
        return record

    def require_exact_action(self, approval_id: str) -> dict[str, Any]:
        """Return executable binding state or fail closed after journal rebuild."""

        record = self.require(approval_id)
        if not record.action_available:
            raise ApprovalArgumentsUnavailableError(
                "exact action arguments are unavailable after projection rebuild"
            )
        return record.action

    def list_for_run(self, run_id: str) -> tuple[ApprovalRecord, ...]:
        rows = _fetch_all(
            self.events,
            "SELECT * FROM approvals WHERE run_id = ? ORDER BY last_sequence",
            (run_id,),
        )
        return tuple(_approval_record(row) for row in rows)


class ToolCallRepository:
    def __init__(self, events: EventStore) -> None:
        self.events = events

    def propose(
        self,
        *,
        call_id: str,
        run_id: str,
        actor_id: str,
        tool_name: str,
        action_digest: str,
        approval_id: str | None = None,
        state: str = "PROPOSED",
        attempt: int = 1,
        at: datetime | str | None = None,
    ) -> ToolCallRecord:
        self.events.append(
            run_id=run_id,
            event_type="ToolCallProposed",
            actor_id=actor_id,
            payload={
                "call_id": call_id,
                "approval_id": approval_id,
                "tool_name": tool_name,
                "action_digest": action_digest,
                "state": _enum_text(state),
                "attempt": attempt,
            },
            created_at=at,
        )
        return self.require(call_id)

    def change_state(
        self,
        *,
        call_id: str,
        actor_id: str,
        state: str,
        result: Mapping[str, Any] | Any | None = None,
        error_summary: str | None = None,
        at: datetime | str | None = None,
    ) -> ToolCallRecord:
        current = self.require(call_id)
        self.events.append(
            run_id=current.run_id,
            event_type="ToolCallStateChanged",
            actor_id=actor_id,
            payload={
                "call_id": call_id,
                "tool_name": current.tool_name,
                "state": _enum_text(state),
                "result": None if result is None else to_jsonable(result),
                "error_summary": error_summary,
            },
            created_at=at,
        )
        return self.require(call_id)

    def get(self, call_id: str) -> ToolCallRecord | None:
        row = _fetch_one(
            self.events, "SELECT * FROM tool_calls WHERE call_id = ?", (call_id,)
        )
        return None if row is None else _tool_call_record(row)

    def require(self, call_id: str) -> ToolCallRecord:
        record = self.get(call_id)
        if record is None:
            raise NotFoundError(f"tool call {call_id} not found")
        return record

    def incomplete(self) -> tuple[ToolCallRecord, ...]:
        rows = _fetch_all(
            self.events,
            "SELECT * FROM tool_calls WHERE state = 'RUNNING' ORDER BY last_sequence",
        )
        return tuple(_tool_call_record(row) for row in rows)


class ArtifactRepository:
    def __init__(self, events: EventStore) -> None:
        self.events = events

    def record(
        self,
        *,
        artifact_id: str,
        run_id: str,
        actor_id: str,
        kind: str,
        locator: str,
        digest: str,
        byte_length: int,
        tool_call_id: str | None = None,
        verification_status: str = "UNVERIFIED",
        at: datetime | str | None = None,
    ) -> ArtifactRecord:
        self.events.append(
            run_id=run_id,
            event_type="ArtifactRecorded",
            actor_id=actor_id,
            payload={
                "artifact_id": artifact_id,
                "tool_call_id": tool_call_id,
                "kind": kind,
                "locator": locator,
                "digest": digest,
                "byte_length": byte_length,
                "verification_status": verification_status,
            },
            created_at=at,
        )
        return self.require(artifact_id)

    def get(self, artifact_id: str) -> ArtifactRecord | None:
        row = _fetch_one(
            self.events, "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
        )
        return None if row is None else _artifact_record(row)

    def require(self, artifact_id: str) -> ArtifactRecord:
        record = self.get(artifact_id)
        if record is None:
            raise NotFoundError(f"artifact {artifact_id} not found")
        return record


class BudgetRepository:
    def __init__(self, events: EventStore) -> None:
        self.events = events

    def initialize(
        self,
        *,
        run_id: str,
        actor_id: str,
        limits: Mapping[str, Any] | Any,
        usage: Mapping[str, Any] | Any | None = None,
        at: datetime | str | None = None,
    ) -> BudgetRecord:
        self.events.append(
            run_id=run_id,
            event_type="BudgetInitialized",
            actor_id=actor_id,
            payload={"limits": to_jsonable(limits), "usage": to_jsonable(usage or {})},
            created_at=at,
        )
        return self.require(run_id)

    def update_usage(
        self,
        *,
        run_id: str,
        actor_id: str,
        usage: Mapping[str, Any] | Any,
        at: datetime | str | None = None,
    ) -> BudgetRecord:
        self.events.append(
            run_id=run_id,
            event_type="BudgetUpdated",
            actor_id=actor_id,
            payload={"usage": to_jsonable(usage)},
            created_at=at,
        )
        return self.require(run_id)

    def get(self, run_id: str) -> BudgetRecord | None:
        row = _fetch_one(self.events, "SELECT * FROM budgets WHERE run_id = ?", (run_id,))
        return None if row is None else _budget_record(row)

    def require(self, run_id: str) -> BudgetRecord:
        record = self.get(run_id)
        if record is None:
            raise NotFoundError(f"budget for run {run_id} not found")
        return record


class MemoryRepository:
    ALLOWED_CATEGORIES = frozenset(
        {"FACT", "OUTCOME", "OWNER_FEEDBACK", "IMPROVEMENT_CANDIDATE"}
    )

    def __init__(self, events: EventStore) -> None:
        self.events = events

    def accept(
        self,
        *,
        memory_id: str,
        run_id: str,
        owner_id: str,
        category: str,
        content: str,
        sensitivity: str,
        provenance: Mapping[str, Any],
        at: datetime | str | None = None,
    ) -> MemoryRecord:
        category = _enum_text(category).upper()
        _validate_memory(category, content, provenance)
        _reject_secret_memory(content)
        content_hash = sha256_hex(content)
        provenance_json = canonical_dumps(provenance)
        provenance_hash = sha256_hex(provenance_json)

        def project(connection: sqlite3.Connection, event: EventRecord) -> None:
            connection.execute(
                """
                INSERT INTO memory_items(
                    memory_id, owner_id, category, content, content_hash,
                    sensitivity, provenance_json, provenance_hash, status,
                    created_at, updated_at, last_sequence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ACCEPTED', ?, ?, ?)
                """,
                (
                    memory_id,
                    owner_id,
                    category,
                    content,
                    content_hash,
                    _enum_text(sensitivity),
                    provenance_json,
                    provenance_hash,
                    event.created_at,
                    event.created_at,
                    event.sequence,
                ),
            )
            connection.execute(
                "INSERT INTO memory_fts(memory_id, content) VALUES (?, ?)",
                (memory_id, content),
            )

        self.events.append(
            run_id=run_id,
            event_type="MemoryAccepted",
            actor_id=owner_id,
            payload={
                "memory_id": memory_id,
                "category": category,
                "content_hash": content_hash,
                "provenance_hash": provenance_hash,
                "sensitivity": _enum_text(sensitivity),
            },
            created_at=at,
            projector=project,
        )
        return self.require(memory_id)

    def correct(
        self,
        memory_id: str,
        *,
        new_memory_id: str,
        owner_id: str,
        content: str,
        provenance: Mapping[str, Any],
        at: datetime | str | None = None,
    ) -> MemoryRecord:
        current = self.require(memory_id)
        if current.owner_id != owner_id:
            raise ApprovalError("only the memory owner may correct it")
        if current.status != "ACCEPTED":
            raise ConflictError("only active memory can be corrected")
        _validate_memory(current.category, content, provenance)
        _reject_secret_memory(content)
        content_hash = sha256_hex(content)
        provenance_json = canonical_dumps(provenance)
        provenance_hash = sha256_hex(provenance_json)

        def project(connection: sqlite3.Connection, event: EventRecord) -> None:
            updated = connection.execute(
                """
                UPDATE memory_items SET status = 'SUPERSEDED', updated_at = ?,
                    last_sequence = ?
                WHERE memory_id = ? AND owner_id = ? AND status = 'ACCEPTED'
                """,
                (event.created_at, event.sequence, memory_id, owner_id),
            )
            if updated.rowcount != 1:
                raise ConflictError("memory changed concurrently")
            connection.execute("DELETE FROM memory_fts WHERE memory_id = ?", (memory_id,))
            connection.execute(
                """
                INSERT INTO memory_items(
                    memory_id, owner_id, category, content, content_hash,
                    sensitivity, provenance_json, provenance_hash, status,
                    supersedes_id, created_at, updated_at, last_sequence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ACCEPTED', ?, ?, ?, ?)
                """,
                (
                    new_memory_id,
                    owner_id,
                    current.category,
                    content,
                    content_hash,
                    current.sensitivity,
                    provenance_json,
                    provenance_hash,
                    memory_id,
                    event.created_at,
                    event.created_at,
                    event.sequence,
                ),
            )
            connection.execute(
                "INSERT INTO memory_fts(memory_id, content) VALUES (?, ?)",
                (new_memory_id, content),
            )

        self.events.append(
            run_id=_provenance_run_id(provenance),
            event_type="MemoryCorrected",
            actor_id=owner_id,
            payload={
                "memory_id": new_memory_id,
                "supersedes_id": memory_id,
                "content_hash": content_hash,
                "provenance_hash": provenance_hash,
            },
            created_at=at,
            projector=project,
        )
        return self.require(new_memory_id)

    def forget(
        self,
        memory_id: str,
        *,
        owner_id: str,
        run_id: str | None = None,
        at: datetime | str | None = None,
    ) -> MemoryRecord:
        current = self.require(memory_id)
        if current.owner_id != owner_id:
            raise ApprovalError("only the memory owner may forget it")
        if current.status == "FORGOTTEN":
            return current
        event_run_id = run_id or _record_provenance_run_id(current)

        def project(connection: sqlite3.Connection, event: EventRecord) -> None:
            updated = connection.execute(
                """
                UPDATE memory_items
                SET content = NULL, provenance_json = NULL, status = 'FORGOTTEN',
                    forgotten_at = ?, updated_at = ?, last_sequence = ?
                WHERE memory_id = ? AND owner_id = ? AND status != 'FORGOTTEN'
                """,
                (
                    event.created_at,
                    event.created_at,
                    event.sequence,
                    memory_id,
                    owner_id,
                ),
            )
            if updated.rowcount != 1:
                raise ConflictError("memory changed concurrently")
            connection.execute("DELETE FROM memory_fts WHERE memory_id = ?", (memory_id,))

        self.events.append(
            run_id=event_run_id,
            event_type="MemoryForgotten",
            actor_id=owner_id,
            payload={
                "memory_id": memory_id,
                "provenance_hash": current.provenance_hash,
            },
            created_at=at,
            projector=project,
        )
        self._purge_forgotten_storage()
        return self.require(memory_id)

    def search(
        self, query: str, *, owner_id: str, limit: int = 20
    ) -> tuple[MemoryRecord, ...]:
        if not query.strip():
            return ()
        if limit < 1 or limit > 100:
            raise ValueError("memory search limit must be between 1 and 100")
        expression = '"' + query.replace('"', '""') + '"'
        rows = _fetch_all(
            self.events,
            """
            SELECT m.* FROM memory_fts f
            JOIN memory_items m ON m.memory_id = f.memory_id
            WHERE memory_fts MATCH ? AND m.owner_id = ? AND m.status = 'ACCEPTED'
            ORDER BY bm25(memory_fts), m.updated_at DESC LIMIT ?
            """,
            (expression, owner_id, limit),
        )
        return tuple(_memory_record(row) for row in rows)

    def export(self, *, owner_id: str) -> tuple[MemoryRecord, ...]:
        rows = _fetch_all(
            self.events,
            """
            SELECT * FROM memory_items
            WHERE owner_id = ? AND status != 'FORGOTTEN'
            ORDER BY created_at, memory_id
            """,
            (owner_id,),
        )
        return tuple(_memory_record(row) for row in rows)

    def get(self, memory_id: str) -> MemoryRecord | None:
        row = _fetch_one(
            self.events, "SELECT * FROM memory_items WHERE memory_id = ?", (memory_id,)
        )
        return None if row is None else _memory_record(row)

    def require(self, memory_id: str) -> MemoryRecord:
        record = self.get(memory_id)
        if record is None:
            raise NotFoundError(f"memory {memory_id} not found")
        return record

    def _purge_forgotten_storage(self) -> None:
        connection = self.events.database.connect()
        try:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.execute("VACUUM")
        finally:
            connection.close()


def _fetch_one(
    events: EventStore, sql: str, values: tuple[Any, ...] = ()
) -> sqlite3.Row | None:
    connection = events.database.connect()
    try:
        return cast(sqlite3.Row | None, connection.execute(sql, values).fetchone())
    finally:
        connection.close()


def _fetch_all(
    events: EventStore, sql: str, values: tuple[Any, ...] = ()
) -> list[sqlite3.Row]:
    connection = events.database.connect()
    try:
        return cast(list[sqlite3.Row], connection.execute(sql, values).fetchall())
    finally:
        connection.close()


def _run_record(row: sqlite3.Row) -> RunRecord:
    return RunRecord(**{field: row[field] for field in RunRecord.__dataclass_fields__})


def _message_record(row: sqlite3.Row) -> MessageRecord:
    return MessageRecord(
        message_id=str(row["message_id"]),
        run_id=str(row["run_id"]),
        schema_version=int(row["schema_version"]),
        sender_id=str(row["sender_id"]),
        sender_role=str(row["sender_role"]),
        recipient=str(row["recipient"]),
        correlation_id=str(row["correlation_id"]),
        causation_id=row["causation_id"],
        payload_type=str(row["payload_type"]),
        payload_hash=str(row["payload_hash"]),
        payload=json.loads(str(row["payload_json"])),
        created_at=str(row["created_at"]),
        sequence=int(row["sequence"]),
    )


def _work_item_record(row: sqlite3.Row) -> WorkItemRecord:
    return WorkItemRecord(
        work_item_id=str(row["work_item_id"]),
        run_id=str(row["run_id"]),
        objective_summary=str(row["objective_summary"]),
        acceptance_criteria=tuple(json.loads(str(row["acceptance_json"]))),
        assigned_role=str(row["assigned_role"]),
        dependencies=tuple(json.loads(str(row["dependencies_json"]))),
        state=str(row["state"]),
        lease_expires_at=str(row["lease_expires_at"]),
        version=int(row["version"]),
        last_sequence=int(row["last_sequence"]),
    )


def _approval_record(row: sqlite3.Row) -> ApprovalRecord:
    action_available = bool(row["action_available"])
    return ApprovalRecord(
        approval_id=str(row["approval_id"]),
        run_id=str(row["run_id"]),
        work_item_id=row["work_item_id"],
        requester_id=str(row["requester_id"]),
        owner_id=str(row["owner_id"]),
        action_digest=str(row["action_digest"]),
        action=(json.loads(str(row["action_json"])) if action_available else {}),
        action_available=action_available,
        policy_version=str(row["policy_version"]),
        expires_at=str(row["expires_at"]),
        status=str(row["status"]),
        nonce=str(row["nonce"]),
        claimed_by=row["claimed_by"],
        claimed_at=row["claimed_at"],
        consumed_at=row["consumed_at"],
        decided_at=row["decided_at"],
        version=int(row["version"]),
        last_sequence=int(row["last_sequence"]),
    )


def _tool_call_record(row: sqlite3.Row) -> ToolCallRecord:
    return ToolCallRecord(
        call_id=str(row["call_id"]),
        run_id=str(row["run_id"]),
        approval_id=row["approval_id"],
        tool_name=str(row["tool_name"]),
        action_digest=str(row["action_digest"]),
        state=str(row["state"]),
        attempt=int(row["attempt"]),
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        result=None if row["result_json"] is None else json.loads(str(row["result_json"])),
        error_summary=row["error_summary"],
        last_sequence=int(row["last_sequence"]),
    )


def _artifact_record(row: sqlite3.Row) -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id=str(row["artifact_id"]),
        run_id=str(row["run_id"]),
        tool_call_id=row["tool_call_id"],
        kind=str(row["kind"]),
        locator=str(row["locator"]),
        digest=str(row["digest"]),
        byte_length=int(row["byte_length"]),
        verification_status=str(row["verification_status"]),
        created_at=str(row["created_at"]),
        last_sequence=int(row["last_sequence"]),
    )


def _budget_record(row: sqlite3.Row) -> BudgetRecord:
    return BudgetRecord(
        run_id=str(row["run_id"]),
        limits=json.loads(str(row["limits_json"])),
        usage=json.loads(str(row["usage_json"])),
        version=int(row["version"]),
        last_sequence=int(row["last_sequence"]),
    )


def _memory_record(row: sqlite3.Row) -> MemoryRecord:
    return MemoryRecord(
        memory_id=str(row["memory_id"]),
        owner_id=str(row["owner_id"]),
        category=str(row["category"]),
        content=row["content"],
        content_hash=str(row["content_hash"]),
        sensitivity=str(row["sensitivity"]),
        provenance=(
            None if row["provenance_json"] is None else json.loads(str(row["provenance_json"]))
        ),
        provenance_hash=str(row["provenance_hash"]),
        status=str(row["status"]),
        supersedes_id=row["supersedes_id"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        forgotten_at=row["forgotten_at"],
        last_sequence=int(row["last_sequence"]),
    )


def _approval_row(connection: sqlite3.Connection, approval_id: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)
    ).fetchone()
    if row is None:
        raise NotFoundError(f"approval {approval_id} not found")
    return cast(sqlite3.Row, row)


def _require_owner(row: sqlite3.Row, owner_id: str) -> None:
    if str(row["owner_id"]) != owner_id:
        raise ApprovalError("approver is not the bound owner")


def _required_time_text(value: datetime | str) -> str:
    result = _time_text(value)
    if result is None:
        raise ValueError("timestamp is required")
    return result


def _time_text(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    parsed = (
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        if isinstance(value, str)
        else value
    )
    if parsed.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return parsed.astimezone(UTC).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _is_expired(expires_at: str, now: str) -> bool:
    return expires_at <= now


def _enum_text(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw)


def _validate_memory(
    category: str, content: str, provenance: Mapping[str, Any]
) -> None:
    if category not in MemoryRepository.ALLOWED_CATEGORIES:
        raise MemoryValidationError(f"unsupported memory category {category}")
    if not content.strip():
        raise MemoryValidationError("memory content cannot be empty")
    if not provenance:
        raise MemoryValidationError("memory provenance is required")
    keys = {str(key) for key, value in provenance.items() if value not in (None, "")}
    if not keys.intersection({"run_id", "event_id", "artifact_id"}):
        raise MemoryValidationError("provenance must link a run, event, or artifact")


def _reject_secret_memory(content: str) -> None:
    from geth_ai.policy.redaction import redact_text

    if redact_text(content) != content:
        raise MemoryValidationError("secret-shaped content cannot be retained as memory")


def _provenance_run_id(provenance: Mapping[str, Any]) -> str:
    value = provenance.get("run_id")
    if not value:
        raise MemoryValidationError("memory correction provenance requires run_id")
    return str(value)


def _record_provenance_run_id(record: MemoryRecord) -> str:
    if record.provenance and record.provenance.get("run_id"):
        return str(record.provenance["run_id"])
    return f"memory:{record.memory_id}"
