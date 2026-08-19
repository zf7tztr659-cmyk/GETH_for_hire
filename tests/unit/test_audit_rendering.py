from __future__ import annotations

import pytest

from geth_ai.observability import render_integrity, render_timeline
from geth_ai.persistence import EventRecord, IntegrityReport


@pytest.mark.requirement("AUD-003")
@pytest.mark.requirement("PRV-001")
def test_default_timeline_is_ordered_and_content_minimized() -> None:
    event = EventRecord(
        sequence=1,
        run_sequence=1,
        event_id="event",
        run_id="run",
        schema_version=1,
        event_type="PolicyEvaluated",
        actor_id="system:policy",
        created_at="2026-01-01T00:00:00Z",
        payload={
            "outcome": "require_approval",
            "action_digest": "a" * 64,
            "raw_message": "do not print this content",
        },
        payload_hash="b" * 64,
        previous_hash="0" * 64,
        event_hash="c" * 64,
    )

    rendered = render_timeline((event,))

    assert "0001" in rendered
    assert "PolicyEvaluated" in rendered
    assert "require_approval" in rendered
    assert "do not print this content" not in rendered


@pytest.mark.requirement("AUD-004")
def test_integrity_output_states_unanchored_chain_limitations() -> None:
    rendered = render_integrity(
        IntegrityReport(valid=True, event_count=3, head_hash="a" * 64)
    )

    assert "VALID" in rendered
    assert "tamper-evident, not tamper-proof" in rendered
    assert "tail truncation" in rendered
