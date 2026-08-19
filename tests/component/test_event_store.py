from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from geth_ai.persistence import (
    Database,
    EventStore,
    MessageRepository,
    RunRepository,
    WorkItemRepository,
)
from geth_ai.persistence.migrations import MIGRATION_1


@pytest.mark.requirement("AUD-002")
def test_hash_chain_is_canonical_global_and_append_only(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.db")
    store = EventStore(database)
    first = store.append(
        run_id="run-a", event_type="Observed", actor_id="owner", payload={"b": 2, "a": 1}
    )
    second = store.append(
        run_id="run-b", event_type="Observed", actor_id="owner", payload={"ok": True}
    )

    report = store.verify(expected_head=second.event_hash, expected_count=2)
    assert report.valid
    assert first.sequence == 1
    assert second.sequence == 2
    assert second.previous_hash == first.event_hash

    connection = database.connect()
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("UPDATE events SET actor_id = 'agent' WHERE sequence = 1")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM events WHERE sequence = 1")
    finally:
        connection.close()


@pytest.mark.requirement("AUD-002")
def test_verification_detects_payload_mutation(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.db")
    store = EventStore(database)
    store.append(run_id="run", event_type="Observed", actor_id="owner", payload={"ok": True})

    connection = database.connect()
    try:
        connection.execute("DROP TRIGGER events_reject_update")
        connection.execute("UPDATE events SET payload_json = '{\"ok\":false}' WHERE sequence = 1")
    finally:
        connection.close()

    report = store.verify()
    assert not report.valid
    assert any("payload hash mismatch" in error for error in report.errors)


@pytest.mark.requirement("AUD-001")
def test_projection_rebuild_uses_canonical_journal(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.db")
    store = EventStore(database)
    runs = RunRepository(store)
    runs.create(run_id="run", owner_id="owner", objective_summary="demo")

    connection = database.connect()
    try:
        connection.execute("DELETE FROM runs")
    finally:
        connection.close()
    assert runs.get("run") is None

    store.rebuild_projections()
    assert runs.require("run").objective_summary == "demo"


@pytest.mark.requirement("FUN-003")
def test_message_and_work_item_projection_retains_typed_identity_fields(
    tmp_path: Path,
) -> None:
    store = EventStore(Database(tmp_path / "state.db"))
    work = WorkItemRepository(store)
    messages = MessageRepository(store)
    work.create(
        work_item_id="work",
        run_id="run",
        actor_id="commander",
        objective_summary="create demo",
        acceptance_criteria=("exact content",),
        assigned_role="executor",
        lease_expires_at="2030-01-01T00:01:00Z",
    )
    messages.record(
        message_id="message",
        run_id="run",
        sender_id="agent:steward",
        sender_role="steward",
        recipient="orchestrator",
        correlation_id="00000000-0000-0000-0000-000000000001",
        causation_id=None,
        payload_type="proposal",
        payload={"claim": "bounded"},
        actor_id="agent:steward",
    )

    message = messages.get("message")
    assert message is not None
    assert message.sender_id == "agent:steward"
    assert message.correlation_id.endswith("1")
    assert work.require("work").acceptance_criteria == ("exact content",)


@pytest.mark.requirement("OPR-001")
def test_v1_database_migrates_approval_action_availability(tmp_path: Path) -> None:
    path = tmp_path / "v1.db"
    connection = sqlite3.connect(path)
    try:
        connection.executescript(MIGRATION_1)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (1, 'legacy')"
        )
        connection.commit()
    finally:
        connection.close()

    Database(path).initialize()
    connection = sqlite3.connect(path)
    try:
        versions = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(approvals)")
        }
    finally:
        connection.close()
    assert versions == [(1,), (2,)]
    assert "action_available" in columns
