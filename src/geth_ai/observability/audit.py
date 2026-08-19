"""Concise, redacted views over the canonical local audit journal."""

from __future__ import annotations

import json
from collections.abc import Iterable

from geth_ai.persistence import EventRecord, IntegrityReport
from geth_ai.policy.redaction import redact

_DETAIL_FIELDS = (
    "state",
    "outcome",
    "reason",
    "tool",
    "tool_name",
    "risk_class",
    "status",
    "action_digest",
    "approval_id",
    "verification_status",
    "artifact_digest",
    "confidence_basis_points",
    "rounds",
    "retries",
)


def _safe_details(event: EventRecord) -> dict[str, object]:
    """Return decision metadata suitable for the default audit timeline."""

    return {
        field: redact(event.payload[field])
        for field in _DETAIL_FIELDS
        if field in event.payload and event.payload[field] is not None
    }


def render_timeline(events: Iterable[EventRecord], *, verbose: bool = False) -> str:
    """Render ordered entries without exposing message or file content by default."""

    lines: list[str] = []
    for event in events:
        prefix = (
            f"{event.run_sequence:04d} {event.created_at} "
            f"{event.event_type} actor={event.actor_id}"
        )
        details: object = redact(event.payload) if verbose else _safe_details(event)
        if details:
            prefix += " " + json.dumps(
                details,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
        lines.append(prefix)
    return "\n".join(lines)


def render_integrity(report: IntegrityReport) -> str:
    """Describe verification and the honest local-chain limitation."""

    status = "VALID" if report.valid else "INVALID"
    lines = [
        f"Audit chain: {status}",
        f"Events: {report.event_count}",
        f"Head: {report.head_hash}",
    ]
    lines.extend(f"Error: {error}" for error in report.errors)
    lines.append(f"Limitation: {report.limitation}")
    return "\n".join(lines)


__all__ = ["render_integrity", "render_timeline"]
