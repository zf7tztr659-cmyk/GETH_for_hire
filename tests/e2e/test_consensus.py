"""Offline acceptance tests for bounded consensus and all seven roles."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from geth_ai.application.consensus import (
    ConsensusBudgetExceeded,
    ConsensusCoordinator,
)
from geth_ai.domain.consensus import DeliberationLimits
from geth_ai.domain.enums import AgentRole, ConsensusOutcome
from geth_ai.domain.models import BudgetLimits
from geth_ai.providers import (
    FakeProvider,
    FakeScenario,
    MalformedProviderResponse,
    ProviderCallStatus,
    ProviderContext,
    ProviderRequest,
    ProviderStage,
    ProviderTimedOut,
)


@pytest.mark.requirement("FUN-004")
def test_proposals_are_independent_before_critique() -> None:
    provider = FakeProvider()
    result = asyncio.run(
        ConsensusCoordinator(provider).run_pipeline(
            run_id="run-independent",
            objective="Write one bounded demonstration artifact",
            verification_evidence="dry-run evidence only",
        )
    )

    assert result.consensus.outcome is ConsensusOutcome.PASS
    assert result.bounded_roles is not None
    assert [record.role for record in provider.ledger] == [
        AgentRole.STEWARD,
        AgentRole.STRATEGIST,
        AgentRole.SKEPTIC,
        AgentRole.SYNTHESIZER,
        AgentRole.LOCAL_COMMANDER,
        AgentRole.EXECUTOR,
        AgentRole.VERIFIER,
    ]
    assert provider.ledger[0].context_roles == ()
    assert provider.ledger[1].context_roles == ()
    assert provider.ledger[2].context_roles == (
        AgentRole.STEWARD,
        AgentRole.STRATEGIST,
    )
    assert provider.ledger[-1].role is not provider.ledger[-2].role
    assert result.bounded_roles.action_proposal.advisory_only is True
    assert result.bounded_roles.action_proposal.side_effect_performed is False
    assert result.bounded_roles.verifier_assessment.independent is True
    assert result.bounded_roles.verifier_assessment.passed is False
    assert result.consensus.provider_calls == 7
    assert result.consensus.token_units == provider.total_token_units
    assert all(record.provider_version == "fake/1.0" for record in provider.ledger)
    assert all(record.prompt_version == "consensus/1.0" for record in provider.ledger)


@pytest.mark.requirement("FUN-008")
def test_executor_plans_before_approval_and_verifier_runs_after_effect_evidence() -> None:
    provider = FakeProvider()
    coordinator = ConsensusCoordinator(provider)
    consensus = asyncio.run(
        coordinator.deliberate(run_id="run-split", objective="bounded objective")
    )
    local_plan, action_proposal = asyncio.run(
        coordinator.plan_action(
            run_id="run-split",
            objective="bounded objective",
            synthesis=consensus.final_synthesis,
        )
    )
    assert local_plan.steps
    assert action_proposal.advisory_only is True
    assert action_proposal.side_effect_performed is False
    assert provider.ledger[-2].role is AgentRole.LOCAL_COMMANDER
    assert provider.ledger[-1].role is AgentRole.EXECUTOR
    assert AgentRole.VERIFIER not in {record.role for record in provider.ledger}

    assessment = asyncio.run(
        coordinator.verify_effect(
            run_id="run-split",
            objective="bounded objective",
            action_proposal=action_proposal,
            effect_evidence="broker result sha256=abc and exact target snapshot verified",
        )
    )
    assert assessment.independent is True
    assert assessment.passed is True
    assert assessment.side_effect_performed is False
    assert provider.ledger[-1].role is AgentRole.VERIFIER


@pytest.mark.requirement("FUN-005")
def test_pass_revise_escalate_dissent_and_veto() -> None:
    passed = asyncio.run(
        ConsensusCoordinator(FakeProvider(FakeScenario.PASS)).deliberate(
            run_id="run-pass", objective="bounded objective"
        )
    )
    assert passed.outcome is ConsensusOutcome.PASS
    assert passed.rounds == 1
    assert passed.retained_dissent
    assert isinstance(passed.final_synthesis.confidence_basis_points, int)
    with pytest.raises(ValidationError):
        passed.retained_dissent[0].summary = "silenced"

    revise_provider = FakeProvider(FakeScenario.REVISE_THEN_PASS)
    revised = asyncio.run(
        ConsensusCoordinator(revise_provider).deliberate(
            run_id="run-revise", objective="bounded objective"
        )
    )
    assert revised.outcome is ConsensusOutcome.PASS
    assert revised.rounds == 2
    assert revised.revisions_used == 1
    assert [item.outcome for item in revised.round_syntheses] == [
        ConsensusOutcome.REVISE,
        ConsensusOutcome.PASS,
    ]
    assert revised.retained_dissent

    escalated = asyncio.run(
        ConsensusCoordinator(FakeProvider(FakeScenario.ESCALATE)).deliberate(
            run_id="run-escalate", objective="bounded objective"
        )
    )
    assert escalated.outcome is ConsensusOutcome.ESCALATE_TO_OWNER
    assert escalated.final_synthesis.dissent[0].material is True

    veto_provider = FakeProvider(FakeScenario.PASS)
    vetoed = asyncio.run(
        ConsensusCoordinator(veto_provider).run_pipeline(
            run_id="run-veto",
            objective="bounded objective",
            policy_veto=True,
        )
    )
    assert vetoed.consensus.outcome is ConsensusOutcome.ESCALATE_TO_OWNER
    assert vetoed.bounded_roles is None
    assert AgentRole.EXECUTOR not in {record.role for record in veto_provider.ledger}


@pytest.mark.requirement("OPS-002")
def test_fake_provider_retry_malformed_timeout_and_redaction() -> None:
    retry_provider = FakeProvider(FakeScenario.RETRYABLE_FAILURE)
    retried = asyncio.run(
        ConsensusCoordinator(retry_provider).deliberate(
            run_id="run-retry", objective="bounded objective"
        )
    )
    assert retried.outcome is ConsensusOutcome.PASS
    assert retried.retries_used == 1
    assert retry_provider.ledger[0].status is ProviderCallStatus.RETRYABLE_FAILURE
    assert len(retry_provider.ledger) == 5

    with pytest.raises(MalformedProviderResponse):
        asyncio.run(
            ConsensusCoordinator(FakeProvider(FakeScenario.MALFORMED_RESPONSE)).deliberate(
                run_id="run-malformed", objective="bounded objective"
            )
        )

    timeout_provider = FakeProvider(FakeScenario.TIMEOUT, timeout_delay_seconds=0.1)
    with pytest.raises(ProviderTimedOut):
        asyncio.run(
            ConsensusCoordinator(timeout_provider, timeout_seconds=0.001).deliberate(
                run_id="run-timeout", objective="bounded objective"
            )
        )
    assert timeout_provider.ledger[0].status is ProviderCallStatus.TIMED_OUT

    request = ProviderRequest(
        run_id="run-secret",
        role=AgentRole.STEWARD,
        stage=ProviderStage.PROPOSAL,
        round_number=1,
        objective="password=hunter2 bearer abc.def.ghi sk-1234567890ABCDEF",
        context=(ProviderContext(label="secret=raw", content="api_key=abcd1234"),),
        provider_version="fake/1.0",
        prompt_version="consensus/1.0",
    )
    serialized = request.model_dump_json()
    assert "hunter2" not in serialized
    assert "abc.def.ghi" not in serialized
    assert "1234567890ABCDEF" not in serialized
    assert "abcd1234" not in serialized
    assert serialized.count("[REDACTED]") >= 4


@pytest.mark.requirement("SAF-005")
def test_consensus_round_and_provider_budgets_are_hard_limits() -> None:
    with pytest.raises(ValueError, match="at most two rounds"):
        ConsensusCoordinator(
            FakeProvider(),
            limits=DeliberationLimits(max_rounds=3),
        )

    call_limited = FakeProvider()
    with pytest.raises(ConsensusBudgetExceeded, match="provider call budget"):
        asyncio.run(
            ConsensusCoordinator(
                call_limited,
                budget=BudgetLimits(max_provider_calls=3),
            ).deliberate(run_id="run-budget", objective="bounded objective")
        )
    assert len(call_limited.ledger) == 3

    token_limited = FakeProvider()
    with pytest.raises(ConsensusBudgetExceeded, match="provider token budget"):
        asyncio.run(
            ConsensusCoordinator(
                token_limited,
                budget=BudgetLimits(max_tokens=1),
            ).deliberate(run_id="run-token-budget", objective="bounded objective")
        )
    assert len(token_limited.ledger) == 1
