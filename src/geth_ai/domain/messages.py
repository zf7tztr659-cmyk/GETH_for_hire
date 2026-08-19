"""Immutable, versioned envelopes for role-to-orchestrator communication."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import model_validator

from geth_ai.domain.base import NonEmptyStr, Sha256Hex, StrictFrozenModel, UtcDateTime
from geth_ai.domain.canonical import canonical_json_text, require_canonical_json
from geth_ai.domain.enums import AgentRole
from geth_ai.domain.ids import MessageId, PrincipalId, RunId


class MessageEnvelope(StrictFrozenModel):
    """A message whose authoritative payload is immutable canonical JSON.

    ``decoded_payload`` returns a fresh object; mutating that object cannot alter
    the envelope or the hash persisted in the audit stream.
    """

    schema_version: Literal[1] = 1
    message_id: MessageId
    run_id: RunId
    sender_id: PrincipalId
    sender_role: AgentRole
    recipient: NonEmptyStr
    correlation_id: UUID
    causation_id: MessageId | None = None
    payload_type: NonEmptyStr
    payload_json: NonEmptyStr
    payload_sha256: Sha256Hex
    created_at: UtcDateTime

    @model_validator(mode="after")
    def payload_is_canonical_and_bound(self) -> Self:
        value = require_canonical_json(self.payload_json)
        if not isinstance(value, dict):
            raise ValueError("message payload must be a JSON object")
        actual = hashlib.sha256(self.payload_json.encode("utf-8")).hexdigest()
        if actual != self.payload_sha256:
            raise ValueError("message payload hash mismatch")
        return self

    @classmethod
    def from_payload(
        cls,
        *,
        message_id: MessageId,
        run_id: RunId,
        sender_id: PrincipalId,
        sender_role: AgentRole,
        recipient: str,
        correlation_id: UUID,
        causation_id: MessageId | None,
        payload_type: str,
        payload: Mapping[str, Any] | StrictFrozenModel,
        created_at: UtcDateTime,
    ) -> MessageEnvelope:
        value: Any = payload.model_dump(mode="python") if isinstance(
            payload, StrictFrozenModel
        ) else payload
        text = canonical_json_text(value)
        return cls(
            message_id=message_id,
            run_id=run_id,
            sender_id=sender_id,
            sender_role=sender_role,
            recipient=recipient,
            correlation_id=correlation_id,
            causation_id=causation_id,
            payload_type=payload_type,
            payload_json=text,
            payload_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            created_at=created_at,
        )

    def decoded_payload(self) -> dict[str, Any]:
        value = require_canonical_json(self.payload_json)
        if not isinstance(value, dict):  # defensive; validator already proves this
            raise ValueError("message payload must be a JSON object")
        return value

    @property
    def payload_hash(self) -> str:
        return self.payload_sha256


__all__ = ["MessageEnvelope"]
