from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from geth_ai.domain import (
    BudgetLimits,
    Principal,
    PrincipalId,
    PrincipalKind,
    RiskClass,
    RunId,
)
from geth_ai.policy import (
    ActionSpec,
    AuthorityError,
    can_approve,
    require_owner_approver,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.requirement("GOV-001")
def test_owner_is_only_root_approver() -> None:
    owner_id = PrincipalId(uuid4())
    agent_id = PrincipalId(uuid4())
    owner = Principal(
        principal_id=owner_id,
        kind=PrincipalKind.OWNER,
        display_name="Owner",
        created_at=NOW,
    )
    agent = Principal(
        principal_id=agent_id,
        kind=PrincipalKind.AGENT,
        display_name="Executor",
        created_at=NOW,
    )
    action = ActionSpec.build(
        tool_name="sandbox.write_text",
        tool_schema_version="1",
        policy_version="policy-v1",
        requester_id=agent_id,
        owner_id=owner_id,
        run_id=RunId(uuid4()),
        work_item_id=None,
        risk_class=RiskClass.REVERSIBLE_WORKSPACE_WRITE,
        root="/sandbox",
        target="/sandbox/result.txt",
        arguments={"path": "result.txt", "content": "ok"},
        expected_prior_state="absent",
        overwrite=False,
        budget=BudgetLimits(max_bytes=10, max_tool_calls=1),
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=1),
        nonce="b" * 32,
    )
    assert can_approve(owner, action)
    assert not can_approve(agent, action)
    require_owner_approver(owner, action)
    with pytest.raises(AuthorityError):
        require_owner_approver(agent, action)
