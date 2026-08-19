from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import pytest

from geth_ai.application.approvals import ApprovalService
from geth_ai.application.clock import FrozenClock
from geth_ai.config import Settings
from geth_ai.domain.enums import PrincipalKind, RiskClass
from geth_ai.domain.ids import PrincipalId, RunId
from geth_ai.domain.models import BudgetLimits, Principal
from geth_ai.persistence import ApprovalRepository, Database, EventStore, ToolCallRepository
from geth_ai.policy.actions import ActionSpec
from geth_ai.policy.authority import AuthorityError
from geth_ai.policy.engine import PolicyContext
from geth_ai.tools import CapabilityBroker, PolicyDenied, ToolRegistry


@pytest.mark.requirement("GOV-001")
@pytest.mark.requirement("AUD-003")
def test_forged_approval_decision_is_denied_and_audited(
    isolated_settings: Settings,
    frozen_clock: FrozenClock,
    owner: Principal,
) -> None:
    events = EventStore(Database(isolated_settings.database_path))
    repository = ApprovalRepository(events)
    service = ApprovalService(repository, frozen_clock)
    requester = PrincipalId(UUID("00000000-0000-0000-0000-000000000002"))
    run_id = RunId(UUID("00000000-0000-0000-0000-000000000003"))
    action = ActionSpec.build(
        tool_name="sandbox.write_text",
        tool_schema_version="1",
        policy_version="mvp-policy-v1",
        requester_id=requester,
        owner_id=owner.principal_id,
        run_id=run_id,
        work_item_id=None,
        risk_class=RiskClass.REVERSIBLE_WORKSPACE_WRITE,
        root=str(isolated_settings.sandbox_root),
        target=str(isolated_settings.sandbox_root / "owner-only.txt"),
        arguments={"path": "owner-only.txt", "content": "bounded content\n"},
        expected_prior_state="absent",
        overwrite=False,
        budget=BudgetLimits(max_bytes=100, max_tool_calls=1),
        created_at=frozen_clock.now(),
        expires_at=frozen_clock.now() + timedelta(minutes=5),
        nonce="owner-only-approval-nonce",
    )
    service.request(approval_id="owner-only-approval", action=action)
    forged = Principal(
        principal_id=requester,
        kind=PrincipalKind.AGENT,
        display_name="Untrusted executor",
        created_at=frozen_clock.now(),
    )

    with pytest.raises(AuthorityError):
        service.approve("owner-only-approval", owner=forged)

    assert repository.require("owner-only-approval").status == "PENDING"
    timeline = events.list(run_id=str(run_id))
    assert timeline[-1].event_type == "ApprovalDecisionRejected"
    assert timeline[-1].payload["reason"] == "principal is not the exact active owner"
    assert not any(event.event_type == "ApprovalApproved" for event in timeline)


@pytest.mark.requirement("SAF-001")
@pytest.mark.requirement("AUD-003")
def test_unknown_tool_invocation_is_denied_and_audited(
    isolated_settings: Settings,
    frozen_clock: FrozenClock,
    owner: Principal,
) -> None:
    events = EventStore(Database(isolated_settings.database_path))
    calls = ToolCallRepository(events)
    broker = CapabilityBroker(ToolRegistry(), tool_calls=calls)
    requester = PrincipalId(UUID("00000000-0000-0000-0000-000000000004"))
    run_id = RunId(UUID("00000000-0000-0000-0000-000000000005"))
    action = ActionSpec.build(
        tool_name="shell.run",
        tool_schema_version="1",
        policy_version="mvp-policy-v1",
        requester_id=requester,
        owner_id=owner.principal_id,
        run_id=run_id,
        work_item_id=None,
        risk_class=RiskClass.PROCESS_EXECUTION,
        root=str(isolated_settings.workspace_root),
        target=str(isolated_settings.workspace_root / "noop"),
        arguments={"path": "noop"},
        expected_prior_state="not_applicable",
        overwrite=False,
        budget=BudgetLimits(max_tool_calls=1),
        created_at=frozen_clock.now(),
        expires_at=frozen_clock.now() + timedelta(minutes=1),
        nonce="unsupported-tool-nonce",
    )
    context = PolicyContext(
        owner_id=owner.principal_id,
        authorized_requesters=frozenset({requester}),
        read_roots=(str(isolated_settings.workspace_root),),
        sandbox_root=str(isolated_settings.sandbox_root),
        active_policy_version="mvp-policy-v1",
        now=frozen_clock.now(),
    )

    assert broker.dry_run(action, context).outcome.value == "deny"
    with pytest.raises(PolicyDenied):
        broker.invoke(
            action=action,
            context=context,
            arguments=action.decoded_arguments(),
            actor_id=str(requester),
            call_id="denied-unknown-tool",
        )

    assert calls.require("denied-unknown-tool").state == "DENIED"
    event_types = [event.event_type for event in events.list(run_id=str(run_id))]
    assert event_types == ["ToolCallProposed"]
