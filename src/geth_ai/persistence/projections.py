"""Deterministic reducers for rebuildable current-state projections."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from .canonical_json import canonical_dumps

if TYPE_CHECKING:
    from .event_store import EventRecord


class ProjectionError(RuntimeError):
    pass


class ProjectionStore:
    """Apply canonical events to disposable current-state tables."""

    REBUILDABLE_TABLES = (
        "runs",
        "work_items",
        "messages",
        "approvals",
        "tool_calls",
        "artifacts",
        "budgets",
    )

    def apply(self, connection: sqlite3.Connection, event: EventRecord) -> None:
        payload = event.payload
        event_type = event.event_type

        if event_type == "RunCreated":
            connection.execute(
                """
                INSERT INTO runs(
                    run_id, owner_id, state, objective_summary, version,
                    created_at, updated_at, lease_expires_at, last_sequence
                ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)
                """,
                (
                    event.run_id,
                    str(payload["owner_id"]),
                    str(payload.get("state", "RECEIVED")),
                    str(payload.get("objective_summary", "")),
                    event.created_at,
                    event.created_at,
                    _optional_text(payload.get("lease_expires_at")),
                    event.sequence,
                ),
            )
        elif event_type == "RunStateChanged":
            _require_one(
                connection.execute(
                    """
                    UPDATE runs
                    SET state = ?, version = version + 1, updated_at = ?,
                        terminal_reason = ?, lease_expires_at = ?, last_sequence = ?
                    WHERE run_id = ?
                    """,
                    (
                        str(payload["state"]),
                        event.created_at,
                        _optional_text(payload.get("reason")),
                        _optional_text(payload.get("lease_expires_at")),
                        event.sequence,
                        event.run_id,
                    ),
                ),
                "run not found for state transition",
            )
        elif event_type == "RunCancelled":
            _require_one(
                connection.execute(
                    """
                    UPDATE runs
                    SET state = 'CANCELLED', version = version + 1,
                        updated_at = ?, cancelled_at = ?, terminal_reason = ?,
                        lease_expires_at = NULL, last_sequence = ?
                    WHERE run_id = ?
                    """,
                    (
                        event.created_at,
                        event.created_at,
                        str(payload.get("reason", "owner cancellation")),
                        event.sequence,
                        event.run_id,
                    ),
                ),
                "run not found for cancellation",
            )
        elif event_type == "MessageRecorded":
            connection.execute(
                """
                INSERT INTO messages(
                    message_id, run_id, schema_version, sender_id, sender_role,
                    recipient, correlation_id, causation_id, payload_type,
                    payload_hash, payload_json, created_at, sequence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(payload["message_id"]),
                    event.run_id,
                    int(payload.get("schema_version", 1)),
                    str(payload["sender_id"]),
                    str(payload["sender_role"]),
                    str(payload["recipient"]),
                    str(payload["correlation_id"]),
                    _optional_text(payload.get("causation_id")),
                    str(payload["payload_type"]),
                    str(payload["payload_hash"]),
                    canonical_dumps(payload.get("payload", {})),
                    event.created_at,
                    event.sequence,
                ),
            )
        elif event_type == "WorkItemCreated":
            connection.execute(
                """
                INSERT INTO work_items(
                    work_item_id, run_id, objective_summary, acceptance_json,
                    assigned_role, dependencies_json, state, lease_expires_at,
                    version, last_sequence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    str(payload["work_item_id"]),
                    event.run_id,
                    str(payload["objective_summary"]),
                    canonical_dumps(payload["acceptance_criteria"]),
                    str(payload["assigned_role"]),
                    canonical_dumps(payload.get("dependencies", [])),
                    str(payload.get("state", "pending")),
                    str(payload["lease_expires_at"]),
                    event.sequence,
                ),
            )
        elif event_type == "WorkItemStateChanged":
            _require_one(
                connection.execute(
                    """
                    UPDATE work_items
                    SET state = ?, lease_expires_at = COALESCE(?, lease_expires_at),
                        version = version + 1, last_sequence = ?
                    WHERE work_item_id = ?
                    """,
                    (
                        str(payload["state"]),
                        _optional_text(payload.get("lease_expires_at")),
                        event.sequence,
                        str(payload["work_item_id"]),
                    ),
                ),
                "work item not found",
            )
        elif event_type == "ApprovalRequested":
            # Canonical audit intentionally cannot reconstruct executable action
            # arguments. A live request replaces this metadata-only row in the
            # same transaction through ApprovalRepository's private projector.
            # During journal-only rebuild action_available remains false, so the
            # approval can be inspected/denied but never approved or claimed.
            connection.execute(
                """
                INSERT INTO approvals(
                    approval_id, run_id, work_item_id, requester_id, owner_id,
                    action_digest, action_json, policy_version, expires_at,
                    status, nonce, version, last_sequence, action_available
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, 1, ?, 0)
                """,
                (
                    str(payload["approval_id"]),
                    event.run_id,
                    _optional_text(payload.get("work_item_id")),
                    str(payload["requester_id"]),
                    str(payload["owner_id"]),
                    str(payload["action_digest"]),
                    canonical_dumps(payload.get("action_metadata", {})),
                    str(payload["policy_version"]),
                    str(payload["expires_at"]),
                    str(payload["nonce"]),
                    event.sequence,
                ),
            )
        elif event_type in {
            "ApprovalApproved",
            "ApprovalDenied",
            "ApprovalCancelled",
            "ApprovalExpired",
        }:
            target = {
                "ApprovalApproved": "APPROVED",
                "ApprovalDenied": "DENIED",
                "ApprovalCancelled": "CANCELLED",
                "ApprovalExpired": "EXPIRED",
            }[event_type]
            allowed = (
                "PENDING','APPROVED"
                if target in {"CANCELLED", "EXPIRED"}
                else "PENDING"
            )
            _require_one(
                connection.execute(
                    f"""
                    UPDATE approvals
                    SET status = ?, decided_at = ?, version = version + 1,
                        last_sequence = ?
                    WHERE approval_id = ? AND status IN ('{allowed}')
                    """,
                    (
                        target,
                        event.created_at,
                        event.sequence,
                        str(payload["approval_id"]),
                    ),
                ),
                f"approval cannot transition to {target}",
            )
        elif event_type == "ApprovalClaimed":
            _require_one(
                connection.execute(
                    """
                    UPDATE approvals
                    SET status = 'CLAIMED', claimed_by = ?, claimed_at = ?,
                        version = version + 1, last_sequence = ?
                    WHERE approval_id = ? AND status = 'APPROVED'
                """,
                    (
                        str(payload["claimed_by"]),
                        event.created_at,
                        event.sequence,
                        str(payload["approval_id"]),
                    ),
                ),
                "approval is not atomically claimable",
            )
        elif event_type == "ApprovalConsumed":
            _require_one(
                connection.execute(
                    """
                    UPDATE approvals
                    SET status = 'CONSUMED', consumed_at = ?,
                        version = version + 1, last_sequence = ?
                    WHERE approval_id = ? AND status = 'CLAIMED'
                        AND claimed_by = ?
                    """,
                    (
                        event.created_at,
                        event.sequence,
                        str(payload["approval_id"]),
                        str(payload["claimed_by"]),
                    ),
                ),
                "approval is not consumable by this claimant",
            )
        elif event_type == "ToolCallProposed":
            connection.execute(
                """
                INSERT INTO tool_calls(
                    call_id, run_id, approval_id, tool_name, action_digest,
                    state, attempt, last_sequence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(payload["call_id"]),
                    event.run_id,
                    _optional_text(payload.get("approval_id")),
                    str(payload["tool_name"]),
                    str(payload["action_digest"]),
                    str(payload.get("state", "PROPOSED")),
                    int(payload.get("attempt", 1)),
                    event.sequence,
                ),
            )
        elif event_type == "ToolCallStateChanged":
            state = str(payload["state"])
            started_at = event.created_at if state == "RUNNING" else None
            finished_at = (
                event.created_at
                if state in {"SUCCEEDED", "FAILED", "TIMED_OUT", "UNCERTAIN", "DENIED"}
                else None
            )
            _require_one(
                connection.execute(
                    """
                    UPDATE tool_calls
                    SET state = ?, started_at = COALESCE(started_at, ?),
                        finished_at = COALESCE(?, finished_at), result_json = ?,
                        error_summary = ?, last_sequence = ?
                    WHERE call_id = ?
                    """,
                    (
                        state,
                        started_at,
                        finished_at,
                        _optional_json(payload.get("result")),
                        _optional_text(payload.get("error_summary")),
                        event.sequence,
                        str(payload["call_id"]),
                    ),
                ),
                "tool call not found",
            )
        elif event_type == "ArtifactRecorded":
            connection.execute(
                """
                INSERT INTO artifacts(
                    artifact_id, run_id, tool_call_id, kind, locator, digest,
                    byte_length, verification_status, created_at, last_sequence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(payload["artifact_id"]),
                    event.run_id,
                    _optional_text(payload.get("tool_call_id")),
                    str(payload["kind"]),
                    str(payload["locator"]),
                    str(payload["digest"]),
                    int(payload["byte_length"]),
                    str(payload.get("verification_status", "UNVERIFIED")),
                    event.created_at,
                    event.sequence,
                ),
            )
        elif event_type == "BudgetInitialized":
            connection.execute(
                """
                INSERT INTO budgets(run_id, limits_json, usage_json, version, last_sequence)
                VALUES (?, ?, ?, 1, ?)
                """,
                (
                    event.run_id,
                    canonical_dumps(payload["limits"]),
                    canonical_dumps(payload.get("usage", {})),
                    event.sequence,
                ),
            )
        elif event_type == "BudgetUpdated":
            _require_one(
                connection.execute(
                    """
                    UPDATE budgets
                    SET usage_json = ?, version = version + 1, last_sequence = ?
                    WHERE run_id = ?
                    """,
                    (
                        canonical_dumps(payload["usage"]),
                        event.sequence,
                        event.run_id,
                    ),
                ),
                "budget not found",
            )

    def rebuild(
        self, connection: sqlite3.Connection, events: Iterable[EventRecord]
    ) -> None:
        for table in self.REBUILDABLE_TABLES:
            connection.execute(f"DELETE FROM {table}")
        for event in events:
            self.apply(connection, event)


def _require_one(cursor: sqlite3.Cursor, message: str) -> None:
    if cursor.rowcount != 1:
        raise ProjectionError(message)


def _optional_text(value: Any) -> str | None:
    return None if value is None else str(value)


def _optional_json(value: Any) -> str | None:
    return None if value is None else canonical_dumps(value)
