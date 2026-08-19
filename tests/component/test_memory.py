"""Service-level tests for explicit, provenance-linked, owner-controlled memory."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest

from geth_ai.application.clock import FrozenClock
from geth_ai.application.memory import MemoryAuthorityError, MemoryService
from geth_ai.domain.enums import MemoryCategory, PrincipalKind, Sensitivity
from geth_ai.domain.ids import PrincipalId
from geth_ai.domain.models import Principal
from geth_ai.persistence import (
    ApprovalError,
    Database,
    EventStore,
    MemoryRepository,
    MemoryValidationError,
)


@dataclass(frozen=True)
class _MemoryHarness:
    path: Path
    events: EventStore
    repository: MemoryRepository
    service: MemoryService


@pytest.fixture
def memory_harness(tmp_path: Path, frozen_clock: FrozenClock) -> _MemoryHarness:
    path = tmp_path / "memory.sqlite3"
    events = EventStore(Database(path))
    repository = MemoryRepository(events)
    return _MemoryHarness(
        path=path,
        events=events,
        repository=repository,
        service=MemoryService(repository, frozen_clock),
    )


def _principal(*, identifier: int, kind: PrincipalKind, clock: FrozenClock) -> Principal:
    return Principal(
        principal_id=PrincipalId(UUID(int=identifier)),
        kind=kind,
        display_name=f"principal-{identifier}",
        created_at=clock.now(),
    )


@pytest.mark.requirement("MEM-001")
def test_only_explicit_allowed_provenance_linked_memory_is_retained(
    memory_harness: _MemoryHarness,
    owner: Principal,
) -> None:
    assert memory_harness.service.export(owner=owner) == ()

    records = []
    for index, category in enumerate(MemoryCategory, start=1):
        record = memory_harness.service.remember(
            owner=owner,
            run_id="run-explicit",
            content=f"explicit retained {category.value} item",
            category=category,
            provenance_event_id=f"event-{index}",
            sensitivity=Sensitivity.CONFIDENTIAL,
            memory_id=f"memory-{index}",
        )
        records.append(record)

    assert {record.category for record in records} == {
        "FACT",
        "OUTCOME",
        "OWNER_FEEDBACK",
        "IMPROVEMENT_CANDIDATE",
    }
    assert all(record.status == "ACCEPTED" for record in records)
    assert all(record.sensitivity == Sensitivity.CONFIDENTIAL.value for record in records)
    assert all(record.provenance is not None for record in records)
    assert all(
        record.provenance["run_id"] == "run-explicit"
        for record in records
        if record.provenance is not None
    )
    assert all(
        record.provenance["source"] == "explicit_owner_command"
        for record in records
        if record.provenance is not None
    )
    assert len(memory_harness.service.export(owner=owner)) == len(MemoryCategory)
    assert [event.event_type for event in memory_harness.events.list()] == [
        "MemoryAccepted"
    ] * len(MemoryCategory)

    with pytest.raises(MemoryValidationError, match="provenance must link"):
        memory_harness.service.remember(
            owner=owner,
            run_id="",
            content="must not survive missing provenance",
            category=MemoryCategory.FACT,
            memory_id="missing-provenance",
        )
    with pytest.raises(MemoryValidationError, match="unsupported memory category"):
        memory_harness.repository.accept(
            memory_id="unsupported",
            run_id="run-explicit",
            owner_id=str(owner.principal_id),
            category="INSTRUCTION",
            content="must not become retained memory",
            sensitivity="internal",
            provenance={"run_id": "run-explicit"},
        )
    assert memory_harness.repository.get("missing-provenance") is None
    assert memory_harness.repository.get("unsupported") is None


@pytest.mark.requirement("MEM-002")
def test_search_export_correct_and_owner_authorization(
    memory_harness: _MemoryHarness,
    owner: Principal,
    frozen_clock: FrozenClock,
) -> None:
    partner = _principal(identifier=20, kind=PrincipalKind.PARTNER, clock=frozen_clock)
    other_owner = _principal(identifier=21, kind=PrincipalKind.OWNER, clock=frozen_clock)
    original = memory_harness.service.remember(
        owner=owner,
        run_id="run-correction",
        content="the launch color is amber",
        category=MemoryCategory.FACT,
        provenance_event_id="event-original",
        memory_id="memory-original",
    )

    with pytest.raises(MemoryAuthorityError, match="only the active owner"):
        memory_harness.service.search("launch color", owner=partner)
    with pytest.raises(MemoryAuthorityError, match="only the active owner"):
        memory_harness.service.export(owner=partner)
    with pytest.raises(MemoryAuthorityError, match="only the active owner"):
        memory_harness.service.correct(
            original.memory_id,
            owner=partner,
            content="unauthorized correction",
            run_id="run-correction",
        )
    assert memory_harness.service.search("launch color", owner=other_owner) == ()
    with pytest.raises(ApprovalError, match="only the memory owner"):
        memory_harness.service.correct(
            original.memory_id,
            owner=other_owner,
            content="wrong owner correction",
            run_id="run-correction",
        )

    corrected = memory_harness.service.correct(
        original.memory_id,
        owner=owner,
        content="the launch color is cobalt",
        run_id="run-correction",
        provenance_event_id="event-correction",
        new_memory_id="memory-corrected",
    )

    old_record = memory_harness.repository.require(original.memory_id)
    assert old_record.status == "SUPERSEDED"
    assert corrected.status == "ACCEPTED"
    assert corrected.supersedes_id == original.memory_id
    assert corrected.provenance is not None
    assert corrected.provenance == {
        "run_id": "run-correction",
        "event_id": "event-correction",
        "source": "explicit_owner_correction",
    }
    assert memory_harness.service.search("launch color is amber", owner=owner) == ()
    assert [
        item.memory_id
        for item in memory_harness.service.search("launch color is cobalt", owner=owner)
    ] == [corrected.memory_id]
    exported = memory_harness.service.export(owner=owner)
    assert {(item.memory_id, item.status, item.supersedes_id) for item in exported} == {
        (original.memory_id, "SUPERSEDED", None),
        (corrected.memory_id, "ACCEPTED", original.memory_id),
    }
    correction_event = memory_harness.events.list(run_id="run-correction")[-1]
    assert correction_event.event_type == "MemoryCorrected"
    assert correction_event.payload["supersedes_id"] == original.memory_id


@pytest.mark.requirement("MEM-003")
def test_forget_removes_content_and_leaves_content_free_tombstone(
    memory_harness: _MemoryHarness,
    owner: Principal,
    frozen_clock: FrozenClock,
) -> None:
    partner = _principal(identifier=30, kind=PrincipalKind.PARTNER, clock=frozen_clock)
    other_owner = _principal(identifier=31, kind=PrincipalKind.OWNER, clock=frozen_clock)
    phrase = "owner removable ultramarine observation"
    retained = memory_harness.service.remember(
        owner=owner,
        run_id="run-forget",
        content=phrase,
        category=MemoryCategory.OWNER_FEEDBACK,
        provenance_event_id="event-feedback",
        memory_id="memory-forget",
    )

    with pytest.raises(MemoryAuthorityError, match="only the active owner"):
        memory_harness.service.forget(retained.memory_id, owner=partner)
    with pytest.raises(ApprovalError, match="only the memory owner"):
        memory_harness.service.forget(retained.memory_id, owner=other_owner)
    assert memory_harness.repository.require(retained.memory_id).content == phrase

    forgotten = memory_harness.service.forget(retained.memory_id, owner=owner)

    assert forgotten.status == "FORGOTTEN"
    assert forgotten.content is None
    assert forgotten.provenance is None
    assert forgotten.forgotten_at is not None
    assert memory_harness.service.search("ultramarine observation", owner=owner) == ()
    assert memory_harness.service.export(owner=owner) == ()
    tombstone = memory_harness.events.list(run_id="run-forget")[-1]
    assert tombstone.event_type == "MemoryForgotten"
    assert tombstone.actor_id == str(owner.principal_id)
    assert set(tombstone.payload) == {"memory_id", "provenance_hash"}
    assert tombstone.payload["memory_id"] == retained.memory_id
    assert phrase not in str(tombstone.payload)

    for path in (
        memory_harness.path,
        memory_harness.path.with_name(f"{memory_harness.path.name}-wal"),
        memory_harness.path.with_name(f"{memory_harness.path.name}-shm"),
    ):
        if path.exists():
            assert phrase.encode() not in path.read_bytes()
