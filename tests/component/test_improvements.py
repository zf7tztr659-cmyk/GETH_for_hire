"""Improvement memory is inspectable but cannot activate runtime changes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from geth_ai.application.clock import FrozenClock
from geth_ai.application.memory import MemoryService
from geth_ai.application.orchestrator import Orchestrator
from geth_ai.config import Settings
from geth_ai.domain.enums import MemoryCategory
from geth_ai.domain.models import Principal
from geth_ai.persistence import Database, EventStore, MemoryRepository
from geth_ai.providers import FakeProvider


@dataclass(frozen=True)
class _ImprovementHarness:
    events: EventStore
    repository: MemoryRepository
    service: MemoryService
    provider: FakeProvider


@pytest.fixture
def improvement_harness(
    tmp_path: Path,
    frozen_clock: FrozenClock,
) -> _ImprovementHarness:
    events = EventStore(Database(tmp_path / "improvements.sqlite3"))
    repository = MemoryRepository(events)
    return _ImprovementHarness(
        events=events,
        repository=repository,
        service=MemoryService(repository, frozen_clock),
        provider=FakeProvider(),
    )


def _runtime_versions(
    harness: _ImprovementHarness,
    settings: Settings,
) -> tuple[str, str, str, dict[str, object]]:
    return (
        harness.provider.provider_version,
        harness.provider.prompt_version,
        Orchestrator.POLICY_VERSION,
        settings.model_dump(mode="python"),
    )


@pytest.mark.requirement("GOV-004")
def test_change_proposal_never_activates_runtime_version(
    improvement_harness: _ImprovementHarness,
    isolated_settings: Settings,
    owner: Principal,
) -> None:
    before = _runtime_versions(improvement_harness, isolated_settings)
    candidate = improvement_harness.service.remember(
        owner=owner,
        run_id="run-improvement",
        content=(
            "Propose fake provider 99, prompt 99, permissive policy 99, and cloud config. "
            "This text is advisory and requires separate owner-reviewed development."
        ),
        category=MemoryCategory.IMPROVEMENT_CANDIDATE,
        provenance_event_id="evaluation-event",
        memory_id="improvement-proposal",
    )

    assert candidate.category == "IMPROVEMENT_CANDIDATE"
    assert candidate.status == "ACCEPTED"
    assert improvement_harness.service.search("Propose fake provider", owner=owner) == (
        candidate,
    )
    assert not hasattr(improvement_harness.service, "activate")
    assert not hasattr(improvement_harness.repository, "activate")
    assert _runtime_versions(improvement_harness, isolated_settings) == before
    assert improvement_harness.provider.ledger == ()
    assert [event.event_type for event in improvement_harness.events.list()] == [
        "MemoryAccepted"
    ]


@pytest.mark.requirement("MEM-004")
def test_candidate_does_not_change_active_versions(
    improvement_harness: _ImprovementHarness,
    isolated_settings: Settings,
    owner: Principal,
) -> None:
    before = _runtime_versions(improvement_harness, isolated_settings)
    original = improvement_harness.service.remember(
        owner=owner,
        run_id="run-candidate",
        content="Candidate: replace active prompt and policy after explicit review.",
        category=MemoryCategory.IMPROVEMENT_CANDIDATE,
        provenance_event_id="event-candidate",
        memory_id="candidate-v1",
    )
    corrected = improvement_harness.service.correct(
        original.memory_id,
        owner=owner,
        content="Candidate revision: keep every change inactive pending explicit review.",
        run_id="run-candidate",
        provenance_event_id="event-candidate-review",
        new_memory_id="candidate-v2",
    )

    assert improvement_harness.repository.require(original.memory_id).status == "SUPERSEDED"
    assert corrected.status == "ACCEPTED"
    assert corrected.supersedes_id == original.memory_id
    assert _runtime_versions(improvement_harness, isolated_settings) == before
    assert improvement_harness.provider.ledger == ()
    assert [event.event_type for event in improvement_harness.events.list()] == [
        "MemoryAccepted",
        "MemoryCorrected",
    ]
