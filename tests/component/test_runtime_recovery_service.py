from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import pytest

from geth_ai.application.bootstrap import Runtime, build_runtime
from geth_ai.application.clock import FrozenClock
from geth_ai.application.health import HealthLevel
from geth_ai.application.recovery import RecoveryDisposition, RecoveryError
from geth_ai.config import Settings
from geth_ai.domain.enums import RiskClass, RunState, ToolCallState
from geth_ai.domain.ids import PrincipalId, RunId
from geth_ai.domain.models import BudgetLimits
from geth_ai.policy.actions import ActionSpec

REQUESTER = PrincipalId(UUID("00000000-0000-0000-0000-000000000002"))


def _seed_interrupted_write(
    runtime: Runtime,
    *,
    run_uuid: UUID,
    relative_path: str = "recovered.txt",
    content: str = "exact durable recovery content\n",
) -> tuple[ActionSpec, str, str]:
    run_id = str(run_uuid)
    approval_id = f"approval-{run_uuid}"
    call_id = f"call-{run_uuid}"
    now = runtime.clock.now()
    runtime.repositories.runs.create(
        run_id=run_id,
        owner_id=str(runtime.owner.principal_id),
        objective_summary="recover one interrupted no-overwrite write",
        actor_id=str(runtime.owner.principal_id),
        state=RunState.EXECUTING.value,
        lease_expires_at=now + timedelta(minutes=1),
        created_at=now,
    )
    action = ActionSpec.build(
        tool_name="sandbox.write_text",
        tool_schema_version="1",
        policy_version=runtime.orchestrator.POLICY_VERSION,
        requester_id=REQUESTER,
        owner_id=runtime.owner.principal_id,
        run_id=RunId(run_uuid),
        work_item_id=None,
        risk_class=RiskClass.REVERSIBLE_WORKSPACE_WRITE,
        root=str(runtime.settings.sandbox_root),
        target=str(runtime.settings.sandbox_root / relative_path),
        arguments={"path": relative_path, "content": content},
        expected_prior_state="absent",
        overwrite=False,
        budget=BudgetLimits(max_bytes=1_024, max_tool_calls=1),
        created_at=now,
        expires_at=now + timedelta(minutes=5),
        nonce=f"recovery-nonce-{run_uuid}",
    )
    runtime.approvals.request(approval_id=approval_id, action=action)
    runtime.approvals.approve(approval_id, owner=runtime.owner)
    runtime.repositories.approvals.claim(
        approval_id,
        run_id=run_id,
        requester_id=str(REQUESTER),
        action_digest=action.digest,
        claimed_by=str(REQUESTER),
        action=action,
        at=now,
    )
    runtime.repositories.tool_calls.propose(
        call_id=call_id,
        run_id=run_id,
        actor_id=str(REQUESTER),
        tool_name=action.tool_name,
        action_digest=action.digest,
        approval_id=approval_id,
        state=ToolCallState.AUTHORIZED.value,
        at=now,
    )
    runtime.repositories.tool_calls.change_state(
        call_id=call_id,
        actor_id=str(REQUESTER),
        state=ToolCallState.RUNNING.value,
        at=now,
    )
    return action, approval_id, call_id


@pytest.mark.requirement("OPR-002")
@pytest.mark.requirement("SAF-005")
def test_startup_reconciles_only_an_exact_persisted_postcondition(
    isolated_settings: Settings,
    frozen_clock: FrozenClock,
) -> None:
    first = build_runtime(isolated_settings, clock=frozen_clock, recover=False)
    run_uuid = UUID("10000000-0000-0000-0000-000000000001")
    action, approval_id, call_id = _seed_interrupted_write(first, run_uuid=run_uuid)
    content = action.decoded_arguments()["content"]
    assert isinstance(content, str)
    (isolated_settings.sandbox_root / "recovered.txt").write_bytes(content.encode())

    reopened = build_runtime(isolated_settings, clock=frozen_clock)

    report = reopened.startup_recovery
    assert report is not None
    assert report.audit_valid
    assert report.calls_examined == 1
    assert report.reconciled == 1
    assert report.blocked == 0
    assert report.items[0].disposition is RecoveryDisposition.RECONCILED
    assert reopened.repositories.tool_calls.require(call_id).state.casefold() == "succeeded"
    assert reopened.repositories.approvals.require(approval_id).status == "CONSUMED"
    assert reopened.repositories.runs.require(str(run_uuid)).state.casefold() == "verifying"
    artifact = reopened.repositories.artifacts.require(f"recovery:{call_id}")
    assert artifact.digest == action.content_sha256
    assert artifact.verification_status == "UNVERIFIED"
    assert reopened.provider.ledger == ()


@pytest.mark.requirement("OPR-002")
@pytest.mark.requirement("SAF-005")
def test_startup_never_retries_a_missing_or_mismatched_effect(
    isolated_settings: Settings,
    frozen_clock: FrozenClock,
) -> None:
    first = build_runtime(isolated_settings, clock=frozen_clock, recover=False)
    run_uuid = UUID("10000000-0000-0000-0000-000000000002")
    _, approval_id, call_id = _seed_interrupted_write(first, run_uuid=run_uuid)
    target = isolated_settings.sandbox_root / "recovered.txt"
    target.write_bytes(b"different bytes")

    reopened = build_runtime(isolated_settings, clock=frozen_clock)

    report = reopened.startup_recovery
    assert report is not None
    assert report.reconciled == 0
    assert report.blocked == 1
    assert report.items[0].reason == "exact_postcondition_mismatch"
    assert reopened.repositories.tool_calls.require(call_id).state.casefold() == "uncertain"
    assert reopened.repositories.approvals.require(approval_id).status == "CLAIMED"
    assert reopened.repositories.runs.require(str(run_uuid)).state.casefold() == "blocked"
    assert target.read_bytes() == b"different bytes"
    assert reopened.provider.ledger == ()

    event_count = len(reopened.events.list())
    second_recovery = reopened.recovery.recover()
    assert second_recovery.calls_examined == 0
    assert len(reopened.events.list()) == event_count


@pytest.mark.requirement("OPR-002")
@pytest.mark.requirement("SAF-005")
def test_nonempty_effect_cannot_match_an_approved_empty_postcondition(
    isolated_settings: Settings,
    frozen_clock: FrozenClock,
) -> None:
    first = build_runtime(isolated_settings, clock=frozen_clock, recover=False)
    run_uuid = UUID("10000000-0000-0000-0000-000000000006")
    _, _, call_id = _seed_interrupted_write(
        first,
        run_uuid=run_uuid,
        content="",
    )
    (isolated_settings.sandbox_root / "recovered.txt").write_bytes(b"not empty")

    reopened = build_runtime(isolated_settings, clock=frozen_clock)

    assert reopened.startup_recovery is not None
    assert reopened.startup_recovery.reconciled == 0
    assert reopened.repositories.tool_calls.require(call_id).state.casefold() == "uncertain"
    assert reopened.provider.ledger == ()


@pytest.mark.requirement("OPR-001")
@pytest.mark.requirement("OPR-002")
def test_active_emergency_stop_prevents_exact_effect_reconciliation(
    isolated_settings: Settings,
    frozen_clock: FrozenClock,
) -> None:
    first = build_runtime(isolated_settings, clock=frozen_clock, recover=False)
    run_uuid = UUID("10000000-0000-0000-0000-000000000003")
    action, _, call_id = _seed_interrupted_write(first, run_uuid=run_uuid)
    content = action.decoded_arguments()["content"]
    assert isinstance(content, str)
    (isolated_settings.sandbox_root / "recovered.txt").write_bytes(content.encode())
    first.emergency.activate(actor="owner", at=frozen_clock.now())

    reopened = build_runtime(isolated_settings, clock=frozen_clock)

    report = reopened.startup_recovery
    assert report is not None
    assert report.emergency_stopped
    assert report.reconciled == 0
    assert report.items[0].reason == "emergency_stop_active"
    assert reopened.repositories.tool_calls.require(call_id).state.casefold() == "uncertain"
    assert reopened.startup_health.level is HealthLevel.DEGRADED
    assert reopened.provider.ledger == ()


@pytest.mark.requirement("AUD-002")
@pytest.mark.requirement("OPR-002")
def test_invalid_audit_chain_stops_recovery_before_any_projection_mutation(
    isolated_settings: Settings,
    frozen_clock: FrozenClock,
) -> None:
    runtime = build_runtime(isolated_settings, clock=frozen_clock, recover=False)
    run_uuid = UUID("10000000-0000-0000-0000-000000000004")
    _, _, call_id = _seed_interrupted_write(runtime, run_uuid=run_uuid)
    connection = runtime.database.connect()
    try:
        connection.execute("DROP TRIGGER events_reject_update")
        connection.execute("UPDATE events SET payload_json = '{}' WHERE sequence = 1")
    finally:
        connection.close()
    before_count = len(runtime.events.list())

    with pytest.raises(RecoveryError, match="before mutation"):
        runtime.recovery.recover()

    assert len(runtime.events.list()) == before_count
    assert runtime.repositories.tool_calls.require(call_id).state.casefold() == "running"
    assert runtime.provider.ledger == ()


@pytest.mark.requirement("SAF-005")
@pytest.mark.requirement("OPR-002")
def test_startup_expires_stale_unclaimed_approval(
    isolated_settings: Settings,
    frozen_clock: FrozenClock,
) -> None:
    runtime = build_runtime(isolated_settings, clock=frozen_clock, recover=False)
    run_uuid = UUID("10000000-0000-0000-0000-000000000005")
    now = frozen_clock.now()
    runtime.repositories.runs.create(
        run_id=str(run_uuid),
        owner_id=str(runtime.owner.principal_id),
        objective_summary="approval expiry",
        state=RunState.AWAITING_APPROVAL.value,
        created_at=now,
    )
    action = ActionSpec.build(
        tool_name="sandbox.write_text",
        tool_schema_version="1",
        policy_version=runtime.orchestrator.POLICY_VERSION,
        requester_id=REQUESTER,
        owner_id=runtime.owner.principal_id,
        run_id=RunId(run_uuid),
        work_item_id=None,
        risk_class=RiskClass.REVERSIBLE_WORKSPACE_WRITE,
        root=str(runtime.settings.sandbox_root),
        target=str(runtime.settings.sandbox_root / "expires.txt"),
        arguments={"path": "expires.txt", "content": "bounded\n"},
        expected_prior_state="absent",
        overwrite=False,
        budget=BudgetLimits(max_bytes=100, max_tool_calls=1),
        created_at=now,
        expires_at=now + timedelta(seconds=30),
        nonce="stale-approval-nonce-00000001",
    )
    runtime.approvals.request(approval_id="stale-approval", action=action)
    frozen_clock.advance(seconds=31)

    reopened = build_runtime(isolated_settings, clock=frozen_clock)

    assert reopened.startup_recovery is not None
    assert reopened.startup_recovery.expired_approvals == 1
    assert reopened.repositories.approvals.require("stale-approval").status == "EXPIRED"
    assert reopened.provider.ledger == ()
