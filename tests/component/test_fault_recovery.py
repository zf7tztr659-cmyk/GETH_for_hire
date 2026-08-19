from __future__ import annotations

from pathlib import Path

import pytest

from geth_ai.persistence import (
    Database,
    EventStore,
    MemoryRepository,
    RunRepository,
    ToolCallRepository,
)


@pytest.mark.requirement("AUD-003")
@pytest.mark.requirement("PRV-001")
@pytest.mark.parametrize("tool_name", ["fs.read", "sandbox.write_text"])
def test_tool_call_audit_retains_only_safe_postcondition_metadata(
    tmp_path: Path, tool_name: str
) -> None:
    database = Database(tmp_path / "state.db")
    store = EventStore(database)
    calls = ToolCallRepository(store)
    raw_content = f"raw {tool_name} content must not be journaled"
    raw_error = f"adapter error included {raw_content}"
    calls.propose(
        call_id="call",
        run_id="run",
        actor_id="executor",
        tool_name=tool_name,
        action_digest="d" * 64,
    )
    changed = calls.change_state(
        call_id="call",
        actor_id="executor",
        state="SUCCEEDED",
        result={
            "path": "demo.txt",
            "content": raw_content,
            "byte_length": len(raw_content.encode()),
            "sha256": "e" * 64,
        },
        error_summary=raw_error,
    )
    assert changed.result == {
        "byte_length": len(raw_content.encode()),
        "path": "demo.txt",
        "sha256": "e" * 64,
    }
    assert changed.error_summary is not None
    assert raw_error not in changed.error_summary

    event = store.list(run_id="run")[-1]
    assert event.event_type == "ToolCallStateChanged"
    assert raw_content not in repr(event.payload)
    assert raw_error not in repr(event.payload)
    assert "content" not in event.payload["result"]

    connection = database.connect()
    try:
        row = connection.execute(
            "SELECT payload_json FROM events WHERE event_type = 'ToolCallStateChanged'"
        ).fetchone()
        projected = connection.execute(
            "SELECT result_json, error_summary FROM tool_calls WHERE call_id = 'call'"
        ).fetchone()
        assert raw_content not in str(row["payload_json"])
        assert raw_error not in str(row["payload_json"])
        assert raw_content not in str(projected["result_json"])
        assert raw_error not in str(projected["error_summary"])
    finally:
        connection.close()


@pytest.mark.requirement("OPR-002")
def test_running_call_is_recovered_as_incomplete_not_replayed(tmp_path: Path) -> None:
    store = EventStore(Database(tmp_path / "state.db"))
    runs = RunRepository(store)
    calls = ToolCallRepository(store)
    runs.create(run_id="run", owner_id="owner", objective_summary="demo")
    calls.propose(
        call_id="call",
        run_id="run",
        actor_id="executor",
        tool_name="sandbox.write_text",
        action_digest="a" * 64,
    )
    calls.change_state(call_id="call", actor_id="executor", state="RUNNING")

    reopened = ToolCallRepository(EventStore(Database(tmp_path / "state.db")))
    assert [call.call_id for call in reopened.incomplete()] == ["call"]
    assert not (tmp_path / "sandbox" / "anything").exists()


@pytest.mark.requirement("MEM-003")
def test_owner_forget_removes_content_and_search_but_keeps_tombstone(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "state.db")
    store = EventStore(database)
    memory = MemoryRepository(store)
    memory.accept(
        memory_id="memory",
        run_id="run",
        owner_id="owner",
        category="fact",
        content="a unique retained phrase",
        sensitivity="internal",
        provenance={"run_id": "run", "event_id": "event"},
    )
    assert memory.search("unique retained", owner_id="owner")

    forgotten = memory.forget("memory", owner_id="owner")
    assert forgotten.status == "FORGOTTEN"
    assert forgotten.content is None
    assert memory.search("unique retained", owner_id="owner") == ()
    assert any(event.event_type == "MemoryForgotten" for event in store.list(run_id="run"))
    assert b"a unique retained phrase" not in (tmp_path / "state.db").read_bytes()
