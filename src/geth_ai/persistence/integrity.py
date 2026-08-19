"""Hash-chain primitives and verification result types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .canonical_json import canonical_bytes, sha256_hex

GENESIS_HASH = "0" * 64


@dataclass(frozen=True, slots=True)
class IntegrityReport:
    valid: bool
    event_count: int
    head_hash: str
    errors: tuple[str, ...] = ()

    @property
    def limitation(self) -> str:
        return (
            "The local unkeyed chain is tamper-evident, not tamper-proof; a party "
            "controlling the database and code can replace/recompute it, and tail "
            "truncation requires an independently retained head checkpoint to detect."
        )


def event_hash_material(
    *,
    sequence: int,
    run_sequence: int,
    event_id: str,
    run_id: str,
    schema_version: int,
    event_type: str,
    actor_id: str,
    created_at: str,
    payload_hash: str,
    previous_hash: str,
) -> dict[str, Any]:
    return {
        "actor_id": actor_id,
        "created_at": created_at,
        "event_id": event_id,
        "event_type": event_type,
        "payload_hash": payload_hash,
        "previous_hash": previous_hash,
        "run_id": run_id,
        "run_sequence": run_sequence,
        "schema_version": schema_version,
        "sequence": sequence,
    }


def compute_event_hash(**values: Any) -> str:
    return sha256_hex(canonical_bytes(event_hash_material(**values)))
