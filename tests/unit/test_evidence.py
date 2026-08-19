from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from geth_ai.domain import (
    EvidenceId,
    EvidenceRef,
    EvidenceSourceKind,
    Sensitivity,
)


@pytest.mark.requirement("FUN-007")
def test_evidence_requires_provenance_and_uncertainty() -> None:
    evidence = EvidenceRef(
        evidence_id=EvidenceId(uuid4()),
        claim="The file has a stable digest",
        source_kind=EvidenceSourceKind.LOCAL_FILE,
        source_locator="docs/requirements.md",
        content_sha256="a" * 64,
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
        uncertainty_basis_points=250,
        uncertainty_reason="The file may change after observation",
        sensitivity=Sensitivity.INTERNAL,
    )
    assert evidence.uncertainty_basis_points == 250

    values = evidence.model_dump()
    del values["source_locator"]
    with pytest.raises(ValidationError):
        EvidenceRef(**values)
    with pytest.raises(ValidationError):
        EvidenceRef(**{**evidence.model_dump(), "uncertainty_basis_points": 10_001})
    with pytest.raises(ValidationError):
        EvidenceRef(**{**evidence.model_dump(), "content_sha256": "not-a-digest"})
