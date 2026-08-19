"""Explicit, owner-controlled provenance memory lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from geth_ai.application.clock import Clock
from geth_ai.domain.enums import MemoryCategory, PrincipalKind, Sensitivity
from geth_ai.domain.models import Principal
from geth_ai.persistence.repositories import MemoryRecord, MemoryRepository


class MemoryAuthorityError(PermissionError):
    pass


_CATEGORY_MAP = {
    MemoryCategory.FACT: "FACT",
    MemoryCategory.OUTCOME: "OUTCOME",
    MemoryCategory.OWNER_FEEDBACK: "OWNER_FEEDBACK",
    MemoryCategory.IMPROVEMENT_CANDIDATE: "IMPROVEMENT_CANDIDATE",
}


def _require_owner(owner: Principal) -> None:
    if not owner.active or owner.kind is not PrincipalKind.OWNER:
        raise MemoryAuthorityError("only the active owner controls retained memory")


@dataclass(slots=True)
class MemoryService:
    repository: MemoryRepository
    clock: Clock

    def remember(
        self,
        *,
        owner: Principal,
        run_id: str,
        content: str,
        category: MemoryCategory,
        provenance_event_id: str | None = None,
        provenance_artifact_id: str | None = None,
        sensitivity: Sensitivity = Sensitivity.INTERNAL,
        memory_id: str | None = None,
    ) -> MemoryRecord:
        """Retain one explicit item; orchestration never calls this implicitly."""

        _require_owner(owner)
        provenance = {
            "run_id": run_id,
            "event_id": provenance_event_id,
            "artifact_id": provenance_artifact_id,
            "source": "explicit_owner_command",
        }
        return self.repository.accept(
            memory_id=memory_id or str(uuid4()),
            run_id=run_id,
            owner_id=str(owner.principal_id),
            category=_CATEGORY_MAP[category],
            content=content,
            sensitivity=sensitivity.value,
            provenance=provenance,
            at=self.clock.now(),
        )

    def search(
        self, query: str, *, owner: Principal, limit: int = 20
    ) -> tuple[MemoryRecord, ...]:
        _require_owner(owner)
        return self.repository.search(query, owner_id=str(owner.principal_id), limit=limit)

    def export(self, *, owner: Principal) -> tuple[MemoryRecord, ...]:
        _require_owner(owner)
        return self.repository.export(owner_id=str(owner.principal_id))

    def correct(
        self,
        memory_id: str,
        *,
        owner: Principal,
        content: str,
        run_id: str,
        provenance_event_id: str | None = None,
        new_memory_id: str | None = None,
    ) -> MemoryRecord:
        _require_owner(owner)
        return self.repository.correct(
            memory_id,
            new_memory_id=new_memory_id or str(uuid4()),
            owner_id=str(owner.principal_id),
            content=content,
            provenance={
                "run_id": run_id,
                "event_id": provenance_event_id,
                "source": "explicit_owner_correction",
            },
            at=self.clock.now(),
        )

    def forget(
        self, memory_id: str, *, owner: Principal, run_id: str | None = None
    ) -> MemoryRecord:
        _require_owner(owner)
        return self.repository.forget(
            memory_id,
            owner_id=str(owner.principal_id),
            run_id=run_id,
            at=self.clock.now(),
        )
