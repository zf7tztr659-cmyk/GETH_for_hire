"""Deterministic, offline provider used by every MVP demo and test."""

from __future__ import annotations

import asyncio
import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ValidationError

from geth_ai.domain.canonical import canonical_json_bytes, canonical_sha256
from geth_ai.domain.enums import (
    AgentRole,
    ConsensusOutcome,
    CritiqueSeverity,
)
from geth_ai.domain.models import BudgetLimits, Critique, Dissent, Proposal, Synthesis
from geth_ai.providers.base import (
    ExecutorActionProposal,
    LocalCommanderPlan,
    MalformedProviderResponse,
    ProviderCallRecord,
    ProviderCallStatus,
    ProviderError,
    ProviderRequest,
    ProviderStage,
    ResponseT,
    RetryableProviderError,
    VerifierAssessment,
)


class FakeScenario(StrEnum):
    """Finite scripts exposed by the credential-free fake provider."""

    PASS = "pass"
    REVISE_THEN_PASS = "revise_then_pass"
    ESCALATE = "escalate"
    RETRYABLE_FAILURE = "retryable_failure"
    MALFORMED_RESPONSE = "malformed_response"
    TIMEOUT = "timeout"


def _token_units(value: bytes) -> int:
    """Use a deterministic, provider-independent accounting approximation."""

    return (len(value) + 3) // 4


class FakeProvider:
    """A closed deterministic script with no network, tool, or authority surface."""

    def __init__(
        self,
        scenario: FakeScenario = FakeScenario.PASS,
        *,
        timeout_delay_seconds: float = 60.0,
    ) -> None:
        if timeout_delay_seconds <= 0:
            raise ValueError("timeout_delay_seconds must be positive")
        self._scenario = scenario
        self._timeout_delay_seconds = timeout_delay_seconds
        self._ledger: list[ProviderCallRecord] = []
        self._retryable_failure_emitted = False
        self._malformed_response_emitted = False

    @property
    def scenario(self) -> FakeScenario:
        return self._scenario

    @property
    def provider_version(self) -> str:
        return "fake/1.0"

    @property
    def prompt_version(self) -> str:
        return "consensus/1.0"

    @property
    def ledger(self) -> tuple[ProviderCallRecord, ...]:
        return tuple(self._ledger)

    @property
    def total_token_units(self) -> int:
        return sum(item.total_token_units for item in self._ledger)

    def _append_record(
        self,
        request: ProviderRequest,
        *,
        output: bytes = b"",
        status: ProviderCallStatus,
        error_code: str | None = None,
    ) -> None:
        request_bytes = canonical_json_bytes(request)
        self._ledger.append(
            ProviderCallRecord(
                sequence=len(self._ledger) + 1,
                role=request.role,
                stage=request.stage,
                round_number=request.round_number,
                provider_version=self.provider_version,
                prompt_version=self.prompt_version,
                request_digest=canonical_sha256(request),
                context_roles=tuple(
                    item.source_role
                    for item in request.context
                    if item.source_role is not None
                ),
                input_token_units=_token_units(request_bytes),
                output_token_units=_token_units(output),
                status=status,
                error_code=error_code,
            )
        )

    def _proposal(self, request: ProviderRequest) -> Proposal:
        role_name = request.role.value.replace("_", " ")
        return Proposal(
            objective=request.objective,
            assumptions=(f"The {role_name} has only the supplied local context.",),
            steps=(
                f"Develop an independent {role_name} proposal.",
                "Require deterministic policy evaluation before any side effect.",
            ),
            evidence_needed=("Verifier evidence for each acceptance criterion.",),
            requested_capabilities=("sandbox.write_demo",),
            risks=("Untrusted content cannot grant authority.",),
            budget=BudgetLimits(
                max_provider_calls=8,
                max_tokens=8_000,
                max_bytes=0,
                max_retries=1,
                max_rounds=2,
                max_recursion_depth=0,
                max_concurrency=1,
                max_wall_time_ms=10_000,
                max_lease_ms=10_000,
                max_tool_calls=0,
            ),
            verification=("An independent verifier checks the exact approved effect.",),
        )

    def _critique(self, request: ProviderRequest) -> Critique:
        revise = (
            self._scenario is FakeScenario.REVISE_THEN_PASS
            and request.round_number == 1
        )
        escalate = self._scenario is FakeScenario.ESCALATE
        material = revise or escalate
        return Critique(
            proposal_ref=f"round-{request.round_number}-independent-proposals",
            challenged_item="Evidence and authority boundaries",
            severity=(CritiqueSeverity.MATERIAL if material else CritiqueSeverity.MEDIUM),
            concerns=(
                "Approval remains separate from advisory consensus.",
                (
                    "Material evidence is unresolved."
                    if material
                    else "A bounded verification check is still required."
                ),
            ),
            missing_tests=("Verify the exact scoped effect and retained dissent.",),
            recommended_revision=(
                "Add explicit evidence and verification before synthesis."
                if revise
                else None
            ),
            material=material,
        )

    def _synthesis(self, request: ProviderRequest) -> Synthesis:
        retained = Dissent(
            source_role=AgentRole.SKEPTIC,
            summary="A human approval and independent verification remain mandatory.",
            material=False,
            resolved=False,
        )
        if self._scenario is FakeScenario.ESCALATE:
            return Synthesis(
                outcome=ConsensusOutcome.ESCALATE_TO_OWNER,
                recommendation="Ask the owner to resolve the material disagreement.",
                rationale="The fake scenario intentionally leaves material evidence unresolved.",
                confidence_basis_points=4_000,
                accepted_points=("Do not execute while disagreement remains material.",),
                dissent=(
                    Dissent(
                        source_role=AgentRole.SKEPTIC,
                        summary="Material evidence remains unresolved.",
                        material=True,
                        resolved=False,
                    ),
                ),
            )
        if (
            self._scenario is FakeScenario.REVISE_THEN_PASS
            and request.round_number == 1
        ):
            return Synthesis(
                outcome=ConsensusOutcome.REVISE,
                recommendation="Revise once with explicit verification evidence.",
                rationale="The critique is remediable within the single revision budget.",
                confidence_basis_points=6_000,
                accepted_points=("One bounded revision is warranted.",),
                dissent=(retained,),
            )
        return Synthesis(
            outcome=ConsensusOutcome.PASS,
            recommendation="Proceed only to deterministic policy evaluation and owner approval.",
            rationale="Independent proposals and critique support a narrow, verifiable plan.",
            confidence_basis_points=8_000,
            accepted_points=(
                "The proposal is bounded.",
                "Consensus grants no execution authority.",
            ),
            dissent=(retained,),
        )

    def _local_plan(self, request: ProviderRequest) -> LocalCommanderPlan:
        return LocalCommanderPlan(
            objective=request.objective,
            branch="bounded-demo-branch",
            steps=(
                "Prepare one exact action proposal for deterministic policy evaluation.",
                "Stop before any side effect and wait for the orchestrator.",
            ),
            acceptance_criteria=(
                "The action remains inside the configured sandbox.",
                "Execution requires an exact one-time owner approval.",
            ),
        )

    def _action_proposal(self, request: ProviderRequest) -> ExecutorActionProposal:
        return ExecutorActionProposal(
            action_name="sandbox.write_demo",
            requested_capability="sandbox.write_demo",
            arguments_digest=canonical_sha256(
                {
                    "objective": request.objective,
                    "context": request.context,
                    "effect": "proposal_only",
                }
            ),
        )

    def _verification(self, request: ProviderRequest) -> VerifierAssessment:
        evidence = next(
            (item.content for item in request.context if item.label == "effect_evidence"),
            "",
        )
        normalized = evidence.casefold()
        passed = "verified" in normalized and (
            "sha256=" in normalized or "digest=" in normalized
        )
        return VerifierAssessment(
            passed=passed,
            checks=(
                "Executor response is advisory and reports no provider-side effect.",
                "Verifier role is distinct from the executor role.",
                (
                    "Concrete effect evidence includes a digest and verification marker."
                    if passed
                    else "Concrete effect evidence is incomplete; do not claim verification."
                ),
            ),
        )

    def _valid_response(self, request: ProviderRequest) -> BaseModel:
        if request.stage is ProviderStage.PROPOSAL:
            if request.role not in (AgentRole.STEWARD, AgentRole.STRATEGIST):
                raise ProviderError("proposal stage requires steward or strategist")
            return self._proposal(request)
        if request.stage is ProviderStage.CRITIQUE:
            if request.role is not AgentRole.SKEPTIC:
                raise ProviderError("critique stage requires skeptic")
            return self._critique(request)
        if request.stage is ProviderStage.SYNTHESIS:
            if request.role is not AgentRole.SYNTHESIZER:
                raise ProviderError("synthesis stage requires synthesizer")
            return self._synthesis(request)
        if request.stage is ProviderStage.LOCAL_PLAN:
            if request.role is not AgentRole.LOCAL_COMMANDER:
                raise ProviderError("local plan stage requires local commander")
            return self._local_plan(request)
        if request.stage is ProviderStage.ACTION_PROPOSAL:
            if request.role is not AgentRole.EXECUTOR:
                raise ProviderError("action proposal stage requires executor")
            return self._action_proposal(request)
        if request.stage is ProviderStage.VERIFICATION:
            if request.role is not AgentRole.VERIFIER:
                raise ProviderError("verification stage requires verifier")
            return self._verification(request)
        raise ProviderError("unsupported fake provider stage")

    async def complete(
        self,
        request: ProviderRequest,
        response_model: type[ResponseT],
    ) -> ResponseT:
        """Return one deterministic response after strict boundary validation."""

        if request.provider_version != self.provider_version:
            raise ProviderError("provider version mismatch")
        if request.prompt_version != self.prompt_version:
            raise ProviderError("prompt version mismatch")

        if self._scenario is FakeScenario.TIMEOUT:
            try:
                await asyncio.sleep(self._timeout_delay_seconds)
            except asyncio.CancelledError:
                self._append_record(
                    request,
                    status=ProviderCallStatus.TIMED_OUT,
                    error_code="provider_timeout",
                )
                raise

        if (
            self._scenario is FakeScenario.RETRYABLE_FAILURE
            and not self._retryable_failure_emitted
        ):
            self._retryable_failure_emitted = True
            self._append_record(
                request,
                status=ProviderCallStatus.RETRYABLE_FAILURE,
                error_code="transient_fake_failure",
            )
            raise RetryableProviderError("transient fake failure")

        if (
            self._scenario is FakeScenario.MALFORMED_RESPONSE
            and not self._malformed_response_emitted
        ):
            self._malformed_response_emitted = True
            malformed: dict[str, Any] = {"unexpected": "strict validation must reject this"}
            output = json.dumps(malformed, sort_keys=True, separators=(",", ":")).encode()
            try:
                response_model.model_validate_json(output, strict=True)
            except ValidationError as exc:
                self._append_record(
                    request,
                    output=output,
                    status=ProviderCallStatus.MALFORMED_RESPONSE,
                    error_code="invalid_provider_payload",
                )
                raise MalformedProviderResponse(
                    "provider payload failed strict validation"
                ) from exc
            raise AssertionError("malformed fake payload unexpectedly validated")

        response = self._valid_response(request)
        if not isinstance(response, response_model):
            raise ProviderError("requested response model does not match provider stage")
        output = response.model_dump_json().encode("utf-8")
        try:
            validated = response_model.model_validate_json(output, strict=True)
        except ValidationError as exc:  # defensive: all fake responses should validate
            self._append_record(
                request,
                output=output,
                status=ProviderCallStatus.MALFORMED_RESPONSE,
                error_code="invalid_fake_fixture",
            )
            raise MalformedProviderResponse("fake fixture failed strict validation") from exc
        self._append_record(request, output=output, status=ProviderCallStatus.SUCCEEDED)
        return validated


__all__ = ["FakeProvider", "FakeScenario"]
