"""Canonical append-only event journal."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from .audit_safety import sanitize_event_payload
from .canonical_json import (
    Redactor,
    canonical_dumps,
    default_redactor,
    redact_and_canonicalize,
    sha256_hex,
)
from .database import Database, utc_now_text
from .integrity import GENESIS_HASH, IntegrityReport, compute_event_hash
from .projections import ProjectionStore


@dataclass(frozen=True, slots=True)
class EventRecord:
    sequence: int
    run_sequence: int
    event_id: str
    run_id: str
    schema_version: int
    event_type: str
    actor_id: str
    created_at: str
    payload: dict[str, Any]
    payload_hash: str
    previous_hash: str
    event_hash: str


type Projector = Callable[[sqlite3.Connection, EventRecord], None]


class EventStore:
    def __init__(
        self,
        database: Database,
        *,
        projections: ProjectionStore | None = None,
        redactor: Redactor | None = None,
        initialize: bool = True,
    ) -> None:
        self.database = database
        self.projections = projections or ProjectionStore()
        self.redactor = redactor or default_redactor
        if initialize:
            self.database.initialize()

    def append(
        self,
        *,
        run_id: str,
        event_type: str,
        actor_id: str,
        payload: Mapping[str, Any],
        schema_version: int = 1,
        event_id: str | None = None,
        created_at: datetime | str | None = None,
        projector: Projector | None = None,
    ) -> EventRecord:
        with self.database.transaction() as connection:
            return self.append_in_transaction(
                connection,
                run_id=run_id,
                event_type=event_type,
                actor_id=actor_id,
                payload=payload,
                schema_version=schema_version,
                event_id=event_id,
                created_at=created_at,
                projector=projector,
            )

    def append_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        event_type: str,
        actor_id: str,
        payload: Mapping[str, Any],
        schema_version: int = 1,
        event_id: str | None = None,
        created_at: datetime | str | None = None,
        projector: Projector | None = None,
    ) -> EventRecord:
        if not run_id or not event_type or not actor_id:
            raise ValueError("run_id, event_type, and actor_id are required")
        if schema_version < 1:
            raise ValueError("schema_version must be positive")

        last = connection.execute(
            "SELECT sequence, event_hash FROM events ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        sequence = 1 if last is None else int(last["sequence"]) + 1
        previous_hash = GENESIS_HASH if last is None else str(last["event_hash"])
        run_row = connection.execute(
            "SELECT COALESCE(MAX(run_sequence), 0) AS n FROM events WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        run_sequence = int(run_row["n"]) + 1
        event_id = event_id or str(uuid4())
        timestamp = _timestamp_text(created_at)
        # Event-specific minimization precedes generic redaction. This prevents a
        # caller from placing executable approval arguments or filesystem content
        # into the immutable journal under an otherwise innocuous key.
        safe_payload = sanitize_event_payload(event_type, payload)
        payload_json = redact_and_canonicalize(safe_payload, self.redactor)
        decoded_payload = json.loads(payload_json)
        if not isinstance(decoded_payload, dict):
            raise ValueError("event payload must be a JSON object")
        payload_hash = sha256_hex(payload_json)
        event_hash = compute_event_hash(
            sequence=sequence,
            run_sequence=run_sequence,
            event_id=event_id,
            run_id=run_id,
            schema_version=schema_version,
            event_type=event_type,
            actor_id=actor_id,
            created_at=timestamp,
            payload_hash=payload_hash,
            previous_hash=previous_hash,
        )
        connection.execute(
            """
            INSERT INTO events(
                sequence, run_sequence, event_id, run_id, schema_version,
                event_type, actor_id, created_at, payload_json, payload_hash,
                previous_hash, event_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sequence,
                run_sequence,
                event_id,
                run_id,
                schema_version,
                event_type,
                actor_id,
                timestamp,
                payload_json,
                payload_hash,
                previous_hash,
                event_hash,
            ),
        )
        record = EventRecord(
            sequence=sequence,
            run_sequence=run_sequence,
            event_id=event_id,
            run_id=run_id,
            schema_version=schema_version,
            event_type=event_type,
            actor_id=actor_id,
            created_at=timestamp,
            payload=decoded_payload,
            payload_hash=payload_hash,
            previous_hash=previous_hash,
            event_hash=event_hash,
        )
        self.projections.apply(connection, record)
        if projector is not None:
            projector(connection, record)
        return record

    def get(self, event_id: str) -> EventRecord | None:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM events WHERE event_id = ?", (event_id,)
            ).fetchone()
            return None if row is None else _row_to_event(row)
        finally:
            connection.close()

    def list(self, *, run_id: str | None = None) -> tuple[EventRecord, ...]:
        connection = self.database.connect()
        try:
            if run_id is None:
                rows = connection.execute("SELECT * FROM events ORDER BY sequence")
            else:
                rows = connection.execute(
                    "SELECT * FROM events WHERE run_id = ? ORDER BY run_sequence",
                    (run_id,),
                )
            return tuple(_row_to_event(row) for row in rows)
        finally:
            connection.close()

    def verify(
        self,
        *,
        expected_head: str | None = None,
        expected_count: int | None = None,
    ) -> IntegrityReport:
        connection = self.database.connect()
        try:
            rows = connection.execute("SELECT * FROM events ORDER BY sequence").fetchall()
        finally:
            connection.close()
        return verify_rows(rows, expected_head=expected_head, expected_count=expected_count)

    def rebuild_projections(self) -> None:
        with self.database.transaction() as connection:
            rows = connection.execute("SELECT * FROM events ORDER BY sequence").fetchall()
            self.projections.rebuild(connection, (_row_to_event(row) for row in rows))


def verify_rows(
    rows: Iterable[sqlite3.Row | Mapping[str, Any]],
    *,
    expected_head: str | None = None,
    expected_count: int | None = None,
) -> IntegrityReport:
    errors: list[str] = []
    previous_hash = GENESIS_HASH
    expected_sequence = 1
    run_sequences: dict[str, int] = {}
    count = 0
    head = GENESIS_HASH

    for row in rows:
        count += 1
        sequence = int(row["sequence"])
        run_id = str(row["run_id"])
        run_sequence = int(row["run_sequence"])
        if sequence != expected_sequence:
            errors.append(f"global sequence gap: expected {expected_sequence}, got {sequence}")
        expected_sequence = sequence + 1
        expected_run_sequence = run_sequences.get(run_id, 0) + 1
        if run_sequence != expected_run_sequence:
            errors.append(
                f"run {run_id} sequence gap: expected {expected_run_sequence}, got {run_sequence}"
            )
        run_sequences[run_id] = run_sequence
        if str(row["previous_hash"]) != previous_hash:
            errors.append(f"event {sequence} previous hash mismatch")

        try:
            decoded = json.loads(str(row["payload_json"]))
            canonical_payload = canonical_dumps(decoded)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            errors.append(f"event {sequence} payload is invalid: {exc}")
            canonical_payload = ""
        if canonical_payload != str(row["payload_json"]):
            errors.append(f"event {sequence} payload is not canonical")
        payload_hash = sha256_hex(canonical_payload)
        if payload_hash != str(row["payload_hash"]):
            errors.append(f"event {sequence} payload hash mismatch")

        calculated = compute_event_hash(
            sequence=sequence,
            run_sequence=run_sequence,
            event_id=str(row["event_id"]),
            run_id=run_id,
            schema_version=int(row["schema_version"]),
            event_type=str(row["event_type"]),
            actor_id=str(row["actor_id"]),
            created_at=str(row["created_at"]),
            payload_hash=str(row["payload_hash"]),
            previous_hash=str(row["previous_hash"]),
        )
        if calculated != str(row["event_hash"]):
            errors.append(f"event {sequence} event hash mismatch")
        previous_hash = str(row["event_hash"])
        head = previous_hash

    if expected_count is not None and count != expected_count:
        errors.append(f"event count mismatch: expected {expected_count}, got {count}")
    if expected_head is not None and head != expected_head:
        errors.append("head checkpoint mismatch")
    return IntegrityReport(not errors, count, head, tuple(errors))


def _row_to_event(row: sqlite3.Row | Mapping[str, Any]) -> EventRecord:
    payload = json.loads(str(row["payload_json"]))
    return EventRecord(
        sequence=int(row["sequence"]),
        run_sequence=int(row["run_sequence"]),
        event_id=str(row["event_id"]),
        run_id=str(row["run_id"]),
        schema_version=int(row["schema_version"]),
        event_type=str(row["event_type"]),
        actor_id=str(row["actor_id"]),
        created_at=str(row["created_at"]),
        payload=payload,
        payload_hash=str(row["payload_hash"]),
        previous_hash=str(row["previous_hash"]),
        event_hash=str(row["event_hash"]),
    )


def _timestamp_text(value: datetime | str | None) -> str:
    if value is None:
        return utc_now_text()
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = value
    if parsed.tzinfo is None:
        raise ValueError("event timestamps must be timezone-aware")
    return parsed.astimezone(UTC).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )
