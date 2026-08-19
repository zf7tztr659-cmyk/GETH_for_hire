from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from geth_ai.domain import (
    AgentRole,
    MessageEnvelope,
    MessageId,
    PrincipalId,
    RunId,
)


@pytest.mark.requirement("FUN-003")
def test_immutable_versioned_message_round_trip() -> None:
    envelope = MessageEnvelope.from_payload(
        message_id=MessageId(uuid4()),
        run_id=RunId(uuid4()),
        sender_id=PrincipalId(uuid4()),
        sender_role=AgentRole.SKEPTIC,
        recipient="orchestrator",
        correlation_id=uuid4(),
        causation_id=None,
        payload_type="critique.v1",
        payload={"z": [1, 2], "a": "evidence gap"},
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    restored = MessageEnvelope.model_validate_json(envelope.model_dump_json())
    assert restored == envelope
    assert restored.payload_json == '{"a":"evidence gap","z":[1,2]}'
    copy = restored.decoded_payload()
    copy["a"] = "mutated"
    assert restored.decoded_payload()["a"] == "evidence gap"
    with pytest.raises(ValidationError):
        envelope.sender_role = AgentRole.EXECUTOR
    with pytest.raises(ValidationError):
        MessageEnvelope.model_validate({**envelope.model_dump(), "unknown": True})


@pytest.mark.requirement("FUN-003")
def test_message_rejects_payload_tampering() -> None:
    envelope = MessageEnvelope.from_payload(
        message_id=MessageId(uuid4()),
        run_id=RunId(uuid4()),
        sender_id=PrincipalId(uuid4()),
        sender_role=AgentRole.STEWARD,
        recipient="orchestrator",
        correlation_id=uuid4(),
        causation_id=None,
        payload_type="proposal.v1",
        payload={"objective": "safe"},
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    with pytest.raises(ValidationError, match="hash mismatch"):
        MessageEnvelope(**{**envelope.model_dump(), "payload_json": '{"objective":"unsafe"}'})
