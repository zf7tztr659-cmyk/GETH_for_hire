"""Bounded advisory consensus and seven-role provider coordination."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, Field

from geth_ai.domain.base import StrictFrozenModel
from geth_ai.domain.consensus import DeliberationLimits, enforce_consensus_limits
from geth_ai.domain.enums import AgentRole, ConsensusOutcome
from geth_ai.domain.models import BudgetLimits, Critique, Dissent, Proposal, Synthesis
from geth_ai.providers.base import (
    ExecutorActionProposal,
    LocalCommanderPlan,
    MalformedProviderResponse,
    ProviderContext,
    ProviderProtocol,
    ProviderRequest,
    ProviderStage,
    ProviderTimedOut,
    ResponseT,
    RetryableProviderError,
    VerifierAssessment,
)


class ConsensusBudgetExceeded(RuntimeError):
    """The advisory pipeline exhausted a hard provider/retry/token budget."""


class ConsensusResult(StrictFrozenModel):
    """Immutable transcript summary; raw round syntheses preserve prior dissent."""

    outcome: ConsensusOutcome
    rounds: int = Field(ge=1, le=2)
    revisions_used: int = Field(ge=0, le=1)
    proposals: tuple[Proposal, ...]
    critiques: tuple[Critique, ...]
    round_syntheses: tuple[Synthesis, ...]
    final_synthesis: Synthesis
    retained_dissent: tuple[Dissent, ...]
    provider_calls: int = Field(ge=0)
    token_units: int = Field(ge=0)
    retries_used: int = Field(ge=0)


class BoundedRoleResult(StrictFrozenModel):
    """Advisory outputs for commander, executor, and independent verifier."""

    local_plan: LocalCommanderPlan
    action_proposal: ExecutorActionProposal
    verifier_assessment: VerifierAssessment


class FullRolePipelineResult(StrictFrozenModel):
    """Consensus plus optional post-consensus bounded-role responses."""

    consensus: ConsensusResult
    bounded_roles: BoundedRoleResult | None = None


@dataclass
class _CallState:
    start_calls: int
    start_tokens: int
    retries_used: int = 0


def adjudicate_consensus(
    synthesis: Synthesis,
    critique: Critique,
    *,
    limits: DeliberationLimits,
    round_number: int,
    revisions_used: int,
    policy_veto: bool,
) -> ConsensusOutcome:
    """Apply deterministic limits and vetoes to an advisory synthesis."""

    unresolved_material_dissent = any(
        item.material and not item.resolved for item in synthesis.dissent
    )
    material_disagreement = critique.material or unresolved_material_dissent
    remediable = critique.recommended_revision is not None
    return enforce_consensus_limits(
        synthesis.outcome,
        limits=limits,
        round_number=round_number,
        revisions_used=revisions_used,
        material_disagreement=material_disagreement,
        remediable=remediable,
        policy_veto=policy_veto,
    )


def _replace_outcome(synthesis: Synthesis, outcome: ConsensusOutcome) -> Synthesis:
    if synthesis.outcome is outcome:
        return synthesis
    escalation = outcome is ConsensusOutcome.ESCALATE_TO_OWNER
    return Synthesis(
        outcome=outcome,
        recommendation=(
            "Escalate to the owner without executing."
            if escalation
            else "Perform one bounded revision."
        ),
        rationale=(
            f"{synthesis.rationale} Deterministic consensus limits or policy changed "
            f"the advisory outcome to {outcome.value}."
        ),
        confidence_basis_points=synthesis.confidence_basis_points,
        accepted_points=synthesis.accepted_points,
        dissent=synthesis.dissent,
    )


class ConsensusCoordinator:
    """Coordinates typed calls; it owns no tools, approvals, or mutable run state."""

    def __init__(
        self,
        provider: ProviderProtocol,
        *,
        limits: DeliberationLimits | None = None,
        budget: BudgetLimits | None = None,
        timeout_seconds: float = 1.0,
        boundary_check: Callable[[str], None] | None = None,
    ) -> None:
        self._provider = provider
        self._limits = limits or DeliberationLimits()
        self._budget = budget or BudgetLimits()
        if self._limits.max_rounds > 2 or self._limits.max_revisions > 1:
            raise ValueError("the MVP permits at most two rounds and one revision")
        if self._limits.max_recursion_depth != 0:
            raise ValueError("the MVP consensus coordinator does not recurse")
        if self._budget.max_rounds < 1:
            raise ConsensusBudgetExceeded("round budget is zero")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._timeout_seconds = timeout_seconds
        self._boundary_check = boundary_check

    def _new_call_state(self) -> _CallState:
        return _CallState(
            start_calls=len(self._provider.ledger),
            start_tokens=self._provider.total_token_units,
        )

    def _provider_calls(self, state: _CallState) -> int:
        return len(self._provider.ledger) - state.start_calls

    def _token_units(self, state: _CallState) -> int:
        return self._provider.total_token_units - state.start_tokens

    def _check_before_call(self, state: _CallState, *, run_id: str) -> None:
        if self._boundary_check is not None:
            self._boundary_check(run_id)
        if self._provider_calls(state) >= self._budget.max_provider_calls:
            raise ConsensusBudgetExceeded("provider call budget exhausted")
        if self._token_units(state) >= self._budget.max_tokens:
            raise ConsensusBudgetExceeded("provider token budget exhausted")

    def _check_after_call(self, state: _CallState) -> None:
        if self._provider_calls(state) > self._budget.max_provider_calls:
            raise ConsensusBudgetExceeded("provider call budget exceeded")
        if self._token_units(state) > self._budget.max_tokens:
            raise ConsensusBudgetExceeded("provider token budget exceeded")

    async def _complete(
        self,
        request: ProviderRequest,
        response_model: type[ResponseT],
        state: _CallState,
    ) -> ResponseT:
        while True:
            self._check_before_call(state, run_id=request.run_id)
            try:
                response = await asyncio.wait_for(
                    self._provider.complete(request, response_model),
                    timeout=self._timeout_seconds,
                )
            except TimeoutError as exc:
                self._check_after_call(state)
                raise ProviderTimedOut("provider call exceeded its timeout") from exc
            except RetryableProviderError:
                self._check_after_call(state)
                if state.retries_used >= self._budget.max_retries:
                    raise
                state.retries_used += 1
                continue
            except MalformedProviderResponse:
                self._check_after_call(state)
                raise
            self._check_after_call(state)
            return response

    def _request(
        self,
        *,
        run_id: str,
        objective: str,
        role: AgentRole,
        stage: ProviderStage,
        round_number: int,
        context: tuple[ProviderContext, ...] = (),
    ) -> ProviderRequest:
        return ProviderRequest(
            run_id=run_id,
            role=role,
            stage=stage,
            round_number=round_number,
            objective=objective,
            context=context,
            provider_version=self._provider.provider_version,
            prompt_version=self._provider.prompt_version,
        )

    @staticmethod
    def _context(
        label: str,
        value: BaseModel,
        role: AgentRole,
    ) -> ProviderContext:
        return ProviderContext(
            label=label,
            content=value.model_dump_json(),
            source_role=role,
        )

    async def _deliberate(
        self,
        *,
        run_id: str,
        objective: str,
        policy_veto: bool,
        state: _CallState,
    ) -> ConsensusResult:
        proposals: list[Proposal] = []
        critiques: list[Critique] = []
        syntheses: list[Synthesis] = []
        retained_dissent: list[Dissent] = []
        revisions_used = 0
        prior_context: tuple[ProviderContext, ...] = ()
        effective_rounds = min(self._limits.max_rounds, self._budget.max_rounds, 2)

        for round_number in range(1, effective_rounds + 1):
            # These calls have identical prior-round context and neither sees the
            # other's current proposal, preserving analytical independence.
            steward = await self._complete(
                self._request(
                    run_id=run_id,
                    objective=objective,
                    role=AgentRole.STEWARD,
                    stage=ProviderStage.PROPOSAL,
                    round_number=round_number,
                    context=prior_context,
                ),
                Proposal,
                state,
            )
            strategist = await self._complete(
                self._request(
                    run_id=run_id,
                    objective=objective,
                    role=AgentRole.STRATEGIST,
                    stage=ProviderStage.PROPOSAL,
                    round_number=round_number,
                    context=prior_context,
                ),
                Proposal,
                state,
            )
            proposals.extend((steward, strategist))
            proposal_context = (
                self._context("steward_proposal", steward, AgentRole.STEWARD),
                self._context("strategist_proposal", strategist, AgentRole.STRATEGIST),
            )
            critique = await self._complete(
                self._request(
                    run_id=run_id,
                    objective=objective,
                    role=AgentRole.SKEPTIC,
                    stage=ProviderStage.CRITIQUE,
                    round_number=round_number,
                    context=proposal_context,
                ),
                Critique,
                state,
            )
            critiques.append(critique)
            synthesis = await self._complete(
                self._request(
                    run_id=run_id,
                    objective=objective,
                    role=AgentRole.SYNTHESIZER,
                    stage=ProviderStage.SYNTHESIS,
                    round_number=round_number,
                    context=proposal_context
                    + (self._context("skeptic_critique", critique, AgentRole.SKEPTIC),),
                ),
                Synthesis,
                state,
            )
            outcome = adjudicate_consensus(
                synthesis,
                critique,
                limits=self._limits,
                round_number=round_number,
                revisions_used=revisions_used,
                policy_veto=policy_veto,
            )
            synthesis = _replace_outcome(synthesis, outcome)
            syntheses.append(synthesis)
            for dissent in synthesis.dissent:
                if dissent not in retained_dissent:
                    retained_dissent.append(dissent)

            if outcome is ConsensusOutcome.REVISE:
                revisions_used += 1
                prior_context = (
                    self._context("prior_critique", critique, AgentRole.SKEPTIC),
                    self._context("prior_synthesis", synthesis, AgentRole.SYNTHESIZER),
                )
                continue
            return ConsensusResult(
                outcome=outcome,
                rounds=round_number,
                revisions_used=revisions_used,
                proposals=tuple(proposals),
                critiques=tuple(critiques),
                round_syntheses=tuple(syntheses),
                final_synthesis=synthesis,
                retained_dissent=tuple(retained_dissent),
                provider_calls=self._provider_calls(state),
                token_units=self._token_units(state),
                retries_used=state.retries_used,
            )

        final = _replace_outcome(syntheses[-1], ConsensusOutcome.ESCALATE_TO_OWNER)
        return ConsensusResult(
            outcome=ConsensusOutcome.ESCALATE_TO_OWNER,
            rounds=effective_rounds,
            revisions_used=revisions_used,
            proposals=tuple(proposals),
            critiques=tuple(critiques),
            round_syntheses=tuple(syntheses[:-1]) + (final,),
            final_synthesis=final,
            retained_dissent=tuple(retained_dissent),
            provider_calls=self._provider_calls(state),
            token_units=self._token_units(state),
            retries_used=state.retries_used,
        )

    async def deliberate(
        self,
        *,
        run_id: str,
        objective: str,
        policy_veto: bool = False,
    ) -> ConsensusResult:
        """Run bounded proposal, critique, and synthesis calls."""

        return await self._deliberate(
            run_id=run_id,
            objective=objective,
            policy_veto=policy_veto,
            state=self._new_call_state(),
        )

    async def _bounded_roles(
        self,
        *,
        run_id: str,
        objective: str,
        synthesis: Synthesis,
        verification_evidence: str,
        round_number: int,
        state: _CallState,
    ) -> BoundedRoleResult:
        local_plan, action_proposal = await self._plan_action(
            run_id=run_id,
            objective=objective,
            synthesis=synthesis,
            round_number=round_number,
            state=state,
        )
        verifier = await self._verify_effect(
            run_id=run_id,
            objective=objective,
            action_proposal=action_proposal,
            verification_evidence=verification_evidence,
            round_number=round_number,
            state=state,
        )
        return BoundedRoleResult(
            local_plan=local_plan,
            action_proposal=action_proposal,
            verifier_assessment=verifier,
        )

    async def _plan_action(
        self,
        *,
        run_id: str,
        objective: str,
        synthesis: Synthesis,
        round_number: int,
        state: _CallState,
    ) -> tuple[LocalCommanderPlan, ExecutorActionProposal]:
        local_plan = await self._complete(
            self._request(
                run_id=run_id,
                objective=objective,
                role=AgentRole.LOCAL_COMMANDER,
                stage=ProviderStage.LOCAL_PLAN,
                round_number=round_number,
                context=(
                    self._context("approved_synthesis", synthesis, AgentRole.SYNTHESIZER),
                ),
            ),
            LocalCommanderPlan,
            state,
        )
        action_proposal = await self._complete(
            self._request(
                run_id=run_id,
                objective=objective,
                role=AgentRole.EXECUTOR,
                stage=ProviderStage.ACTION_PROPOSAL,
                round_number=round_number,
                context=(
                    self._context("local_plan", local_plan, AgentRole.LOCAL_COMMANDER),
                ),
            ),
            ExecutorActionProposal,
            state,
        )
        return local_plan, action_proposal

    async def _verify_effect(
        self,
        *,
        run_id: str,
        objective: str,
        action_proposal: ExecutorActionProposal,
        verification_evidence: str,
        round_number: int,
        state: _CallState,
    ) -> VerifierAssessment:
        verification_context = (
            self._context("action_proposal", action_proposal, AgentRole.EXECUTOR),
            ProviderContext(label="effect_evidence", content=verification_evidence),
        )
        verifier = await self._complete(
            self._request(
                run_id=run_id,
                objective=objective,
                role=AgentRole.VERIFIER,
                stage=ProviderStage.VERIFICATION,
                round_number=round_number,
                context=verification_context,
            ),
            VerifierAssessment,
            state,
        )
        return verifier

    async def plan_action(
        self,
        *,
        run_id: str,
        objective: str,
        synthesis: Synthesis,
        round_number: int = 1,
    ) -> tuple[LocalCommanderPlan, ExecutorActionProposal]:
        """Collect commander and executor proposals before any approval or effect."""

        return await self._plan_action(
            run_id=run_id,
            objective=objective,
            synthesis=synthesis,
            round_number=round_number,
            state=self._new_call_state(),
        )

    async def verify_effect(
        self,
        *,
        run_id: str,
        objective: str,
        action_proposal: ExecutorActionProposal,
        effect_evidence: str,
        round_number: int = 1,
    ) -> VerifierAssessment:
        """Assess concrete broker evidence after execution; never invoke the effect."""

        return await self._verify_effect(
            run_id=run_id,
            objective=objective,
            action_proposal=action_proposal,
            verification_evidence=effect_evidence,
            round_number=round_number,
            state=self._new_call_state(),
        )

    async def run_pipeline(
        self,
        *,
        run_id: str,
        objective: str,
        policy_veto: bool = False,
        verification_evidence: str = "dry-run evidence: no side effect was requested",
    ) -> FullRolePipelineResult:
        """Run all seven roles when consensus passes, without executing any action."""

        state = self._new_call_state()
        consensus = await self._deliberate(
            run_id=run_id,
            objective=objective,
            policy_veto=policy_veto,
            state=state,
        )
        if consensus.outcome is not ConsensusOutcome.PASS:
            return FullRolePipelineResult(consensus=consensus)
        bounded = await self._bounded_roles(
            run_id=run_id,
            objective=objective,
            synthesis=consensus.final_synthesis,
            verification_evidence=verification_evidence,
            round_number=consensus.rounds,
            state=state,
        )
        # Reflect the full seven-role accounting in the immutable returned result.
        consensus = ConsensusResult(
            outcome=consensus.outcome,
            rounds=consensus.rounds,
            revisions_used=consensus.revisions_used,
            proposals=consensus.proposals,
            critiques=consensus.critiques,
            round_syntheses=consensus.round_syntheses,
            final_synthesis=consensus.final_synthesis,
            retained_dissent=consensus.retained_dissent,
            provider_calls=self._provider_calls(state),
            token_units=self._token_units(state),
            retries_used=state.retries_used,
        )
        return FullRolePipelineResult(consensus=consensus, bounded_roles=bounded)


__all__ = [
    "BoundedRoleResult",
    "ConsensusBudgetExceeded",
    "ConsensusCoordinator",
    "ConsensusResult",
    "FullRolePipelineResult",
    "adjudicate_consensus",
]
