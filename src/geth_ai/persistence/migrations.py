"""SQLite schema migrations for the local runtime database."""

from __future__ import annotations

import sqlite3

LATEST_SCHEMA_VERSION = 2

MIGRATION_1 = r"""
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    sequence INTEGER PRIMARY KEY,
    run_sequence INTEGER NOT NULL,
    event_id TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    previous_hash TEXT NOT NULL,
    event_hash TEXT NOT NULL UNIQUE,
    UNIQUE (run_id, run_sequence)
);

CREATE INDEX IF NOT EXISTS events_run_sequence_idx
    ON events (run_id, run_sequence);
CREATE INDEX IF NOT EXISTS events_type_idx
    ON events (event_type, sequence);

CREATE TRIGGER IF NOT EXISTS events_reject_update
BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(ABORT, 'canonical events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS events_reject_delete
BEFORE DELETE ON events
BEGIN
    SELECT RAISE(ABORT, 'canonical events are append-only');
END;

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    state TEXT NOT NULL,
    objective_summary TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    cancelled_at TEXT,
    lease_expires_at TEXT,
    terminal_reason TEXT,
    last_sequence INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    sender_id TEXT NOT NULL,
    sender_role TEXT NOT NULL,
    recipient TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    causation_id TEXT,
    payload_type TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    sequence INTEGER NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS messages_run_idx ON messages (run_id, sequence);

CREATE TABLE IF NOT EXISTS work_items (
    work_item_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    objective_summary TEXT NOT NULL,
    acceptance_json TEXT NOT NULL,
    assigned_role TEXT NOT NULL,
    dependencies_json TEXT NOT NULL,
    state TEXT NOT NULL,
    lease_expires_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    last_sequence INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS work_items_run_idx
    ON work_items (run_id, state, last_sequence);

CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    work_item_id TEXT,
    requester_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    action_digest TEXT NOT NULL,
    action_json TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('PENDING','APPROVED','CLAIMED','CONSUMED','DENIED','CANCELLED','EXPIRED')
    ),
    nonce TEXT NOT NULL UNIQUE,
    claimed_by TEXT,
    claimed_at TEXT,
    consumed_at TEXT,
    decided_at TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    last_sequence INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS approvals_run_idx ON approvals (run_id, status);
CREATE INDEX IF NOT EXISTS approvals_expiry_idx ON approvals (status, expires_at);

CREATE TABLE IF NOT EXISTS tool_calls (
    call_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    approval_id TEXT,
    tool_name TEXT NOT NULL,
    action_digest TEXT NOT NULL,
    state TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    started_at TEXT,
    finished_at TEXT,
    result_json TEXT,
    error_summary TEXT,
    last_sequence INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS tool_calls_run_idx ON tool_calls (run_id, last_sequence);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    tool_call_id TEXT,
    kind TEXT NOT NULL,
    locator TEXT NOT NULL,
    digest TEXT NOT NULL,
    byte_length INTEGER NOT NULL,
    verification_status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_sequence INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS artifacts_run_idx ON artifacts (run_id, last_sequence);

CREATE TABLE IF NOT EXISTS budgets (
    run_id TEXT PRIMARY KEY,
    limits_json TEXT NOT NULL,
    usage_json TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    last_sequence INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_items (
    memory_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    category TEXT NOT NULL CHECK (
        category IN ('FACT','OUTCOME','OWNER_FEEDBACK','IMPROVEMENT_CANDIDATE')
    ),
    content TEXT,
    content_hash TEXT NOT NULL,
    sensitivity TEXT NOT NULL,
    provenance_json TEXT,
    provenance_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ACCEPTED','SUPERSEDED','FORGOTTEN')),
    supersedes_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    forgotten_at TEXT,
    last_sequence INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS memory_owner_status_idx
    ON memory_items (owner_id, status, updated_at);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    memory_id UNINDEXED,
    content,
    tokenize = 'unicode61'
);
"""

MIGRATION_2 = r"""
ALTER TABLE approvals
ADD COLUMN action_available INTEGER NOT NULL DEFAULT 1
CHECK (action_available IN (0, 1));
"""


def apply_migrations(connection: sqlite3.Connection, applied_at: str) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    rows = connection.execute("SELECT version FROM schema_migrations").fetchall()
    applied = {int(row[0]) for row in rows}
    if 1 not in applied:
        connection.executescript(MIGRATION_1)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (1, applied_at),
        )
    if 2 not in applied:
        connection.executescript(MIGRATION_2)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (2, applied_at),
        )

    current = connection.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
    ).fetchone()[0]
    if int(current) != LATEST_SCHEMA_VERSION:
        raise RuntimeError(
            f"unsupported database schema {current}; expected {LATEST_SCHEMA_VERSION}"
        )
