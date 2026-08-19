"""Durable reference-monitor orchestration for the offline vertical slice."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4, uuid5

from pydantic import Field

from geth_ai.application.approvals import ApprovalService, action_from_record
from geth_ai.application.clock import Clock
from geth_ai.application.consensus import ConsensusCoordinator, ConsensusResult
from geth_ai.application.emergency import EmergencyStop
from geth_ai.config import Settings
from geth_ai.domain.base import StrictFrozenModel
from geth_ai.domain.enums import (
    AgentRole,
    ConsensusOutcome,
    EvidenceSourceKind,
    PrincipalKind,
    RiskClass,
    RunState,
    Sensitivity,
)
from geth_ai.domain.ids import (
    EvidenceId,
    MessageId,
    PrincipalId,
    RunId,
    WorkItemId,
)
from geth_ai.domain.messages import MessageEnvelope
from geth_ai.domain.models import (
    Budget,
    BudgetLimits,
    BudgetUsage,
    EvidenceRef,
    Principal,
    Synthesis,
)
from geth_ai.domain.transitions import TERMINAL_RUN_STATES, require_transition
from geth_ai.persistence import (
    ApprovalExpiredError,
    ApprovalRepository,
    ArtifactRepository,
    BudgetRepository,
    EventStore,
    MessageRepository,
    RunRecord,
    RunRepository,
    ToolCallRepository,
    WorkItemRepository,
)
from geth_ai.policy.actions import ActionSpec
from geth_ai.policy.engine import PolicyContext
from geth_ai.policy.redaction import redact_text
from geth_ai.providers import ExecutorActionProposal, ProviderProtocol
from geth_ai.tools import CapabilityBroker, ReadFileOutput, WriteTextOutput


class OrchestrationError(RuntimeError):
    pass


class RunResult(StrictFrozenModel):
    run_id: str
    state: RunState
    summary: str
    recommendation: str
    confidence_basis_points: int = Field(ge=0, le=10_000)
    dissent: tuple[str, ...] = ()
    approval_id: str | None = None
    action_digest: str | None = None
    sandbox_target: str | None = None
    verified: bool = False

    @property
    def audit_hint(self) -> str:
        return f"geth-ai audit show {self.run_id}"


@dataclass(slots=True)
class RepositoryBundle:
    runs: RunRepository
    work_items: WorkItemRepository
    messages: MessageRepository
    approvals: ApprovalRepository
    tool_calls: ToolCallRepository
    artifacts: ArtifactRepository
    budgets: BudgetRepository


class Orchestrator:
    """The only component allowed to coordinate state, policy, tools, and roles."""

    POLICY_VERSION = "mvp-policy-v1"

    def __init__(
        self,
        *,
        settings: Settings,
        owner: Principal,
        clock: Clock,
        events: EventStore,
        repositories: RepositoryBundle,
        approvals: ApprovalService,
        broker: CapabilityBroker,
        provider: ProviderProtocol,
        consensus: ConsensusCoordinator,
        emergency_stop: EmergencyStop,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if owner.kind is not PrincipalKind.OWNER or not owner.active:
            raise ValueError("orchestrator requires an active owner principal")
        self.settings = settings
        self.owner = owner
        self.clock = clock
        self.events = events
        self.repositories = repositories
        self.approval_service = approvals
        self.broker = broker
        self.provider = provider
        self.consensus = consensus
        self.emergency_stop = emergency_stop
        self.id_factory = id_factory

    async def start_run(self, objective: str) -> RunResult:
        """Plan one bounded demonstration and stop before its material effect."""

        self.emergency_stop.require_clear()
        safe_objective = redact_text(objective).strip()
        if not safe_objective:
            raise OrchestrationError("objective must contain non-secret text")

        now = self.clock.now()
        run_uuid = self.id_factory()
        run_id = str(run_uuid)
        owner_id = str(self.owner.principal_id)
        limits = self._budget_limits()
        self.repositories.runs.create(
            run_id=run_id,
            owner_id=owner_id,
            objective_summary=safe_objective,
            actor_id=owner_id,
            state=RunState.RECEIVED.value,
            lease_expires_at=now + timedelta(seconds=self.settings.budgets.lease_seconds),
            created_at=now,
        )
        self.repositories.budgets.initialize(
            run_id=run_id,
            actor_id="system:budget",
            limits=limits,
            usage=BudgetUsage(),
            at=now,
        )
        evidence = EvidenceRef(
            evidence_id=EvidenceId(self.id_factory()),
            claim="The owner supplied this objective for bounded analysis.",
            source_kind=EvidenceSourceKind.OWNER_STATEMENT,
            source_locator="owner:objective",
            content_sha256=hashlib.sha256(safe_objective.encode()).hexdigest(),
            observed_at=now,
            uncertainty_basis_points=0,
            uncertainty_reason="This proves provenance, not objective truth.",
            sensitivity=Sensitivity.INTERNAL,
        )
        self.events.append(
            run_id=run_id,
            event_type="EvidenceRecorded",
            actor_id=owner_id,
            payload=evidence.model_dump(mode="json"),
            created_at=now,
        )
        self._transition(run_id, RunState.TRIAGED, actor_id="role:steward")
        self._transition(run_id, RunState.PLANNING, actor_id="system:orchestrator")

        ledger_start = len(self.provider.ledger)
        try:
            consensus_result = await self.consensus.deliberate(
                run_id=run_id,
                objective=safe_objective,
            )
        except Exception as exc:
            self._record_provider_calls(run_id, ledger_start)
            self.events.append(
                run_id=run_id,
                event_type="PlanningFailed",
                actor_id="system:orchestrator",
                payload={"error": exc},
                created_at=self.clock.now(),
            )
            self._transition(
                run_id,
                RunState.FAILED,
                actor_id="system:orchestrator",
                reason="bounded provider planning failed",
            )
            raise OrchestrationError("bounded planning failed; inspect the audit") from exc

        self._record_consensus_messages(run_uuid, consensus_result)
        self._record_provider_calls(run_id, ledger_start)
        self._consume_budget(
            run_id,
            provider_calls=consensus_result.provider_calls,
            tokens=consensus_result.token_units,
            retries=consensus_result.retries_used,
            rounds=consensus_result.rounds,
        )
        self.events.append(
            run_id=run_id,
            event_type="ConsensusCompleted",
            actor_id="role:synthesizer",
            payload={
                "outcome": consensus_result.outcome.value,
                "confidence_basis_points": (
                    consensus_result.final_synthesis.confidence_basis_points
                ),
                "dissent": [
                    item.model_dump(mode="json")
                    for item in consensus_result.retained_dissent
                ],
                "rounds": consensus_result.rounds,
                "retries": consensus_result.retries_used,
            },
            created_at=self.clock.now(),
        )
        if consensus_result.outcome is not ConsensusOutcome.PASS:
            self._transition(
                run_id,
                RunState.BLOCKED,
                actor_id="system:orchestrator",
                reason="advisory consensus escalated to the owner",
            )
            return self._result_from_synthesis(
                run_id,
                state=RunState.BLOCKED,
                synthesis=consensus_result.final_synthesis,
                summary="No action was requested; material disagreement was escalated.",
            )

        plan_ledger_start = len(self.provider.ledger)
        try:
            local_plan, executor_proposal = await self.consensus.plan_action(
                run_id=run_id,
                objective=safe_objective,
                synthesis=consensus_result.final_synthesis,
                round_number=consensus_result.rounds,
            )
        except Exception as exc:
            self._record_provider_calls(run_id, plan_ledger_start)
            self._transition(
                run_id,
                RunState.FAILED,
                actor_id="system:orchestrator",
                reason="bounded action planning failed",
            )
            raise OrchestrationError("bounded action planning failed") from exc
        self._record_role_message(run_uuid, AgentRole.LOCAL_COMMANDER, local_plan)
        self._record_role_message(run_uuid, AgentRole.EXECUTOR, executor_proposal)
        new_calls, new_tokens = self._record_provider_calls(run_id, plan_ledger_start)
        self._consume_budget(run_id, provider_calls=new_calls, tokens=new_tokens)

        work_item_uuid = self.id_factory()
        commander_id = str(self._role_id(run_uuid, AgentRole.LOCAL_COMMANDER))
        self.repositories.work_items.create(
            work_item_id=str(work_item_uuid),
            run_id=run_id,
            actor_id=commander_id,
            objective_summary="Create one exact approved demonstration artifact.",
            acceptance_criteria=local_plan.acceptance_criteria,
            assigned_role=AgentRole.EXECUTOR.value,
            lease_expires_at=self.clock.now()
            + timedelta(seconds=self.settings.budgets.lease_seconds),
            at=self.clock.now(),
        )
        action = self._build_write_action(
            run_uuid=run_uuid,
            work_item_uuid=work_item_uuid,
            limits=limits,
        )
        requester_id = str(action.requester_id)
        decision = self.broker.dry_run(
            action,
            self._policy_context(run_id, requester_id=requester_id),
        )
        self.events.append(
            run_id=run_id,
            event_type="PolicyEvaluated",
            actor_id="system:policy",
            payload={
                "action_digest": action.digest,
                "tool": action.tool_name,
                "risk_class": action.risk_class.value,
                "outcome": decision.outcome.value,
                "reason": decision.reason.value,
                "requires_owner_approval": decision.requires_owner_approval,
            },
            created_at=self.clock.now(),
        )
        if not decision.requires_owner_approval:
            self._transition(
                run_id,
                RunState.BLOCKED,
                actor_id="system:policy",
                reason="write policy failed closed",
            )
            raise OrchestrationError("write did not produce an exact approval request")

        approval_id = str(self.id_factory())
        self.approval_service.request(approval_id=approval_id, action=action)
        self._transition(
            run_id,
            RunState.AWAITING_APPROVAL,
            actor_id="system:orchestrator",
            reason="exact owner approval required",
        )
        return self._result_from_synthesis(
            run_id,
            state=RunState.AWAITING_APPROVAL,
            synthesis=consensus_result.final_synthesis,
            summary="Recommendation ready; one exact sandbox write awaits owner approval.",
            approval_id=approval_id,
            action=action,
        )

    async def approve(self, approval_id: str) -> RunResult:
        """Approve, execute, independently read back, and verify one exact action."""

        self.emergency_stop.require_clear()
        approval = self.repositories.approvals.require(approval_id)
        run = self.repositories.runs.require(approval.run_id)
        if self._run_state(run) is not RunState.AWAITING_APPROVAL:
            raise OrchestrationError("run is not awaiting approval")
        action = action_from_record(approval)
        try:
            self.approval_service.approve(approval_id, owner=self.owner)
        except ApprovalExpiredError as exc:
            self._transition(
                run.run_id,
                RunState.BLOCKED,
                actor_id=str(self.owner.principal_id),
                reason="approval expired",
            )
            raise OrchestrationError("approval expired; no side effect occurred") from exc

        self._transition(
            run.run_id,
            RunState.EXECUTING,
            actor_id=str(self.owner.principal_id),
        )
        if action.work_item_id is not None:
            self.repositories.work_items.change_state(
                work_item_id=str(action.work_item_id),
                actor_id=str(action.requester_id),
                state="active",
                lease_expires_at=self.clock.now()
                + timedelta(seconds=self.settings.budgets.lease_seconds),
                at=self.clock.now(),
            )

        context = self._policy_context(
            run.run_id,
            requester_id=str(action.requester_id),
        )

        def precommit_check() -> None:
            self.emergency_stop.require_clear()
            current = self.repositories.runs.require(run.run_id)
            if self._run_state(current) is RunState.CANCELLED:
                raise OrchestrationError("run was cancelled before tool commit")
            if current.lease_expires_at is None:
                raise OrchestrationError("run has no active execution lease")
            if self.clock.now().isoformat() >= current.lease_expires_at:
                raise OrchestrationError("run lease expired before tool commit")
            if self.clock.now() >= action.expires_at:
                raise OrchestrationError("approval expired before tool commit")
            budget = self.repositories.budgets.require(run.run_id)
            limits = BudgetLimits.model_validate(budget.limits)
            usage = BudgetUsage.model_validate(budget.usage)
            if action.content_length is None:
                raise OrchestrationError("write action lacks an exact content length")
            if usage.tool_calls + 1 > limits.max_tool_calls:
                raise OrchestrationError("tool-call budget exhausted before commit")
            if usage.bytes + action.content_length > limits.max_bytes:
                raise OrchestrationError("byte budget exhausted before commit")

        try:
            write_result = self.broker.invoke(
                action=action,
                context=context,
                arguments=action.decoded_arguments(),
                actor_id=str(action.requester_id),
                approval_id=approval_id,
                call_id=str(self.id_factory()),
                precommit_check=precommit_check,
            )
        except Exception as exc:
            self._transition(
                run.run_id,
                RunState.FAILED,
                actor_id="system:orchestrator",
                reason="approved tool call failed or became uncertain",
            )
            raise OrchestrationError("approved tool call did not complete safely") from exc
        if not isinstance(write_result.output, WriteTextOutput):
            raise OrchestrationError("write adapter returned an unexpected result type")
        self._consume_budget(
            run.run_id,
            tool_calls=1,
            bytes=write_result.output.byte_length,
        )
        self._transition(
            run.run_id,
            RunState.VERIFYING,
            actor_id="system:orchestrator",
        )
        if action.work_item_id is not None:
            self.repositories.work_items.change_state(
                work_item_id=str(action.work_item_id),
                actor_id="role:verifier",
                state="verifying",
                at=self.clock.now(),
            )

        relative_path = str(action.decoded_arguments()["path"])
        read_action = self._build_readback_action(
            run_id=RunId(UUID(run.run_id)),
            relative_path=relative_path,
            expected_bytes=write_result.output.byte_length,
        )
        read_result = self.broker.invoke(
            action=read_action,
            context=self._policy_context(
                run.run_id,
                requester_id=str(read_action.requester_id),
            ),
            arguments=read_action.decoded_arguments(),
            actor_id=str(read_action.requester_id),
            call_id=str(self.id_factory()),
        )
        if not isinstance(read_result.output, ReadFileOutput):
            raise OrchestrationError("read adapter returned an unexpected result type")
        exact_postcondition = (
            read_result.output.path == relative_path
            and read_result.output.sha256 == action.content_sha256
            and read_result.output.byte_length == action.content_length
            and read_result.output.content == action.decoded_arguments()["content"]
        )
        if not exact_postcondition:
            self._transition(
                run.run_id,
                RunState.FAILED,
                actor_id="role:verifier",
                reason="exact postcondition verification failed",
            )
            raise OrchestrationError("independent readback did not match the approved action")
        self._consume_budget(
            run.run_id,
            tool_calls=1,
            bytes=read_result.output.byte_length,
        )

        synthesis = self._load_latest_synthesis(run.run_id)
        if synthesis is None:
            raise OrchestrationError("run has no persisted synthesis")
        executor_proposal = self._load_executor_proposal(run.run_id)
        verifier_ledger_start = len(self.provider.ledger)
        evidence = (
            "verified marker: exact postcondition observed; "
            f"path={relative_path}; sha256={read_result.output.sha256}; "
            f"bytes={read_result.output.byte_length}"
        )
        verifier = await self.consensus.verify_effect(
            run_id=run.run_id,
            objective=run.objective_summary,
            action_proposal=executor_proposal,
            effect_evidence=evidence,
            round_number=self._consensus_round(run.run_id),
        )
        self._record_role_message(UUID(run.run_id), AgentRole.VERIFIER, verifier)
        new_calls, new_tokens = self._record_provider_calls(
            run.run_id, verifier_ledger_start
        )
        self._consume_budget(
            run.run_id,
            provider_calls=new_calls,
            tokens=new_tokens,
        )
        if not verifier.passed:
            self._transition(
                run.run_id,
                RunState.FAILED,
                actor_id="role:verifier",
                reason="independent verifier rejected the postcondition",
            )
            raise OrchestrationError("independent verifier rejected the result")

        self.repositories.artifacts.record(
            artifact_id=str(self.id_factory()),
            run_id=run.run_id,
            actor_id=str(self._role_id(UUID(run.run_id), AgentRole.VERIFIER)),
            kind="sandbox_text",
            locator=f"sandbox/{relative_path}",
            digest=read_result.output.sha256,
            byte_length=read_result.output.byte_length,
            tool_call_id=write_result.call_id,
            verification_status="VERIFIED",
            at=self.clock.now(),
        )
        if action.work_item_id is not None:
            self.repositories.work_items.change_state(
                work_item_id=str(action.work_item_id),
                actor_id="role:verifier",
                state="completed",
                at=self.clock.now(),
            )
        self._transition(
            run.run_id,
            RunState.COMPLETED,
            actor_id="role:verifier",
            reason="exact approved effect independently verified",
        )
        self.events.append(
            run_id=run.run_id,
            event_type="RunOutcomeRecorded",
            actor_id="system:orchestrator",
            payload={
                "recommendation": synthesis.recommendation,
                "confidence_basis_points": synthesis.confidence_basis_points,
                "dissent": [item.summary for item in synthesis.dissent],
                "verified": True,
                "artifact_digest": read_result.output.sha256,
            },
            created_at=self.clock.now(),
        )
        return self._result_from_synthesis(
            run.run_id,
            state=RunState.COMPLETED,
            synthesis=synthesis,
            summary="The exact approved sandbox artifact was created and independently verified.",
            approval_id=approval_id,
            action=action,
            verified=True,
        )

    def deny(self, approval_id: str) -> RunResult:
        approval = self.repositories.approvals.require(approval_id)
        run = self.repositories.runs.require(approval.run_id)
        if self._run_state(run) is not RunState.AWAITING_APPROVAL:
            raise OrchestrationError("run is not awaiting approval")
        action = action_from_record(approval)
        self.approval_service.deny(approval_id, owner=self.owner)
        if action.work_item_id is not None:
            self.repositories.work_items.change_state(
                work_item_id=str(action.work_item_id),
                actor_id=str(self.owner.principal_id),
                state="blocked",
                at=self.clock.now(),
            )
        self._transition(
            run.run_id,
            RunState.BLOCKED,
            actor_id=str(self.owner.principal_id),
            reason="owner denied the exact action",
        )
        synthesis = self._load_latest_synthesis(run.run_id)
        if synthesis is None:
            raise OrchestrationError("run has no persisted synthesis")
        return self._result_from_synthesis(
            run.run_id,
            state=RunState.BLOCKED,
            synthesis=synthesis,
            summary="The owner denied the action; no filesystem effect occurred.",
            approval_id=approval_id,
            action=action,
        )

    def cancel(self, run_id: str) -> RunResult:
        run = self.repositories.runs.require(run_id)
        state = self._run_state(run)
        if state in TERMINAL_RUN_STATES:
            raise OrchestrationError(f"run is already terminal: {state.value}")
        require_transition(state, RunState.CANCELLED)
        for item in self.repositories.work_items.list_for_run(run_id):
            if item.state not in {"completed", "failed", "blocked", "cancelled"}:
                self.repositories.work_items.change_state(
                    work_item_id=item.work_item_id,
                    actor_id=str(self.owner.principal_id),
                    state="cancelled",
                    at=self.clock.now(),
                )
        self.repositories.runs.cancel(
            run_id=run_id,
            actor_id=str(self.owner.principal_id),
            reason="owner cancellation",
            created_at=self.clock.now(),
        )
        synthesis = self._load_latest_synthesis(run_id, required=False)
        if synthesis is None:
            return RunResult(
                run_id=run_id,
                state=RunState.CANCELLED,
                summary="Run cancelled; no new side effect may begin.",
                recommendation="No recommendation was completed.",
                confidence_basis_points=0,
            )
        return self._result_from_synthesis(
            run_id,
            state=RunState.CANCELLED,
            synthesis=synthesis,
            summary="Run cancelled; pending authority was revoked.",
        )

    def describe_run(self, run_id: str) -> dict[str, object]:
        run = self.repositories.runs.require(run_id)
        return {
            "run": run,
            "work_items": self.repositories.work_items.list_for_run(run_id),
            "messages": self.repositories.messages.list_for_run(run_id),
            "approvals": self.repositories.approvals.list_for_run(run_id),
            "events": self.events.list(run_id=run_id),
        }

    def _budget_limits(self) -> BudgetLimits:
        values = self.settings.budgets
        return BudgetLimits(
            max_provider_calls=values.provider_calls,
            max_tokens=values.token_units,
            max_bytes=values.max_run_read_bytes,
            max_retries=values.retries,
            max_rounds=values.consensus_rounds,
            max_recursion_depth=values.recursion_depth,
            max_concurrency=values.concurrency,
            max_wall_time_ms=values.wall_seconds * 1_000,
            max_lease_ms=values.lease_seconds * 1_000,
            max_tool_calls=values.tool_calls,
        )

    def _consume_budget(self, run_id: str, **increments: int) -> None:
        record = self.repositories.budgets.require(run_id)
        budget = Budget(
            limits=BudgetLimits.model_validate(record.limits),
            usage=BudgetUsage.model_validate(record.usage),
        ).consume(**increments)
        self.repositories.budgets.update_usage(
            run_id=run_id,
            actor_id="system:budget",
            usage=budget.usage,
            at=self.clock.now(),
        )

    def _transition(
        self,
        run_id: str,
        target: RunState,
        *,
        actor_id: str,
        reason: str | None = None,
    ) -> RunRecord:
        current = self.repositories.runs.require(run_id)
        current_state = self._run_state(current)
        if target not in {RunState.CANCELLED, RunState.FAILED, RunState.BLOCKED}:
            self.emergency_stop.require_clear()
            if current_state is RunState.CANCELLED:
                raise OrchestrationError("cancelled run cannot transition")
        require_transition(current_state, target)
        lease = None
        if target not in TERMINAL_RUN_STATES:
            lease = self.clock.now() + timedelta(seconds=self.settings.budgets.lease_seconds)
        return self.repositories.runs.transition(
            run_id=run_id,
            state=target.value,
            actor_id=actor_id,
            expected_state=current.state,
            expected_version=current.version,
            reason=reason,
            lease_expires_at=lease,
            created_at=self.clock.now(),
        )

    @staticmethod
    def _run_state(record: RunRecord) -> RunState:
        return RunState(record.state.casefold())

    def _policy_context(self, run_id: str, *, requester_id: str) -> PolicyContext:
        run = self.repositories.runs.require(run_id)
        return PolicyContext(
            owner_id=self.owner.principal_id,
            authorized_requesters=frozenset({PrincipalId(UUID(requester_id))}),
            read_roots=(
                str(self.settings.workspace_root),
                str(self.settings.sandbox_root),
            ),
            sandbox_root=str(self.settings.sandbox_root),
            active_policy_version=self.POLICY_VERSION,
            now=self.clock.now(),
            cancelled=self._run_state(run) is RunState.CANCELLED,
            emergency_stopped=self.emergency_stop.is_active(),
            budget_exhausted=self._tool_budget_exhausted(run_id),
        )

    def _tool_budget_exhausted(self, run_id: str) -> bool:
        record = self.repositories.budgets.require(run_id)
        limits = BudgetLimits.model_validate(record.limits)
        usage = BudgetUsage.model_validate(record.usage)
        return (
            usage.tool_calls >= limits.max_tool_calls
            or usage.bytes >= limits.max_bytes
            or usage.wall_time_ms >= limits.max_wall_time_ms
            or usage.lease_ms >= limits.max_lease_ms
        )

    def _build_write_action(
        self,
        *,
        run_uuid: UUID,
        work_item_uuid: UUID,
        limits: BudgetLimits,
    ) -> ActionSpec:
        now = self.clock.now()
        relative = f"geth-demo-{str(run_uuid)[:8]}.txt"
        content = f"Geth completed exact approved local run {run_uuid}.\n"
        executor = self._role_id(run_uuid, AgentRole.EXECUTOR)
        return ActionSpec.build(
            tool_name="sandbox.write_text",
            tool_schema_version="1",
            policy_version=self.POLICY_VERSION,
            requester_id=executor,
            owner_id=self.owner.principal_id,
            run_id=RunId(run_uuid),
            work_item_id=WorkItemId(work_item_uuid),
            risk_class=RiskClass.REVERSIBLE_WORKSPACE_WRITE,
            root=str(self.settings.sandbox_root),
            target=str(self.settings.sandbox_root / relative),
            arguments={"path": relative, "content": content},
            expected_prior_state="absent",
            overwrite=False,
            budget=limits,
            created_at=now,
            expires_at=now
            + timedelta(seconds=self.settings.budgets.approval_ttl_seconds),
            nonce=str(self.id_factory()),
        )

    def _build_readback_action(
        self,
        *,
        run_id: RunId,
        relative_path: str,
        expected_bytes: int,
    ) -> ActionSpec:
        now = self.clock.now()
        verifier = self._role_id(UUID(str(run_id)), AgentRole.VERIFIER)
        byte_limit = max(expected_bytes, 1)
        return ActionSpec.build(
            tool_name="fs.read",
            tool_schema_version="1",
            policy_version=self.POLICY_VERSION,
            requester_id=verifier,
            owner_id=self.owner.principal_id,
            run_id=run_id,
            work_item_id=None,
            risk_class=RiskClass.LOCAL_READ,
            root=str(self.settings.sandbox_root),
            target=str(self.settings.sandbox_root / relative_path),
            arguments={"path": relative_path, "max_bytes": byte_limit},
            expected_prior_state="not_applicable",
            overwrite=False,
            budget=BudgetLimits(max_bytes=byte_limit, max_tool_calls=1),
            created_at=now,
            expires_at=now + timedelta(seconds=self.settings.budgets.tool_timeout_seconds),
            nonce=str(self.id_factory()),
        )

    def _record_consensus_messages(
        self, run_uuid: UUID, result: ConsensusResult
    ) -> None:
        for index in range(result.rounds):
            proposal_offset = index * 2
            self._record_role_message(
                run_uuid, AgentRole.STEWARD, result.proposals[proposal_offset]
            )
            self._record_role_message(
                run_uuid, AgentRole.STRATEGIST, result.proposals[proposal_offset + 1]
            )
            self._record_role_message(run_uuid, AgentRole.SKEPTIC, result.critiques[index])
            self._record_role_message(
                run_uuid, AgentRole.SYNTHESIZER, result.round_syntheses[index]
            )

    def _record_role_message(
        self,
        run_uuid: UUID,
        role: AgentRole,
        payload: StrictFrozenModel | Mapping[str, object] | object,
    ) -> None:
        existing = self.repositories.messages.list_for_run(str(run_uuid))
        causation = None if not existing else MessageId(UUID(existing[-1].message_id))
        if isinstance(payload, StrictFrozenModel):
            message_payload: Mapping[str, object] | StrictFrozenModel = payload
            payload_type = payload.__class__.__name__
        else:
            dump = getattr(payload, "model_dump", None)
            if not callable(dump):
                raise TypeError("role payload must be a strict model or mapping")
            message_payload = dump(mode="python")
            payload_type = payload.__class__.__name__
        envelope = MessageEnvelope.from_payload(
            message_id=MessageId(self.id_factory()),
            run_id=RunId(run_uuid),
            sender_id=self._role_id(run_uuid, role),
            sender_role=role,
            recipient="orchestrator",
            correlation_id=run_uuid,
            causation_id=causation,
            payload_type=payload_type,
            payload=message_payload,
            created_at=self.clock.now(),
        )
        self.repositories.messages.record_envelope(envelope)

    def _record_provider_calls(self, run_id: str, start: int) -> tuple[int, int]:
        calls = self.provider.ledger[start:]
        for call in calls:
            self.events.append(
                run_id=run_id,
                event_type="ProviderCallRecorded",
                actor_id=f"role:{call.role.value}",
                payload=call.model_dump(mode="json"),
                created_at=self.clock.now(),
            )
        return len(calls), sum(call.total_token_units for call in calls)

    def _load_latest_synthesis(
        self, run_id: str, *, required: bool = True
    ) -> Synthesis | None:
        records = [
            item
            for item in self.repositories.messages.list_for_run(run_id)
            if item.payload_type == "Synthesis"
        ]
        if not records:
            if required:
                raise OrchestrationError("run has no persisted synthesis")
            return None
        return Synthesis.model_validate_json(json.dumps(records[-1].payload))

    def _load_executor_proposal(self, run_id: str) -> ExecutorActionProposal:
        records = [
            item
            for item in self.repositories.messages.list_for_run(run_id)
            if item.payload_type == "ExecutorActionProposal"
        ]
        if not records:
            raise OrchestrationError("run has no persisted executor proposal")
        return ExecutorActionProposal.model_validate_json(
            json.dumps(records[-1].payload)
        )

    def _consensus_round(self, run_id: str) -> int:
        return max(
            1,
            min(
                2,
                sum(
                    1
                    for item in self.repositories.messages.list_for_run(run_id)
                    if item.payload_type == "Synthesis"
                ),
            ),
        )

    @staticmethod
    def _role_id(run_uuid: UUID, role: AgentRole) -> PrincipalId:
        return PrincipalId(uuid5(run_uuid, f"geth-ai-role:{role.value}"))

    def _result_from_synthesis(
        self,
        run_id: str,
        *,
        state: RunState,
        synthesis: Synthesis,
        summary: str,
        approval_id: str | None = None,
        action: ActionSpec | None = None,
        verified: bool = False,
    ) -> RunResult:
        relative_target: str | None = None
        if action is not None:
            try:
                relative_target = str(Path(action.target).relative_to(self.settings.sandbox_root))
            except ValueError:
                relative_target = None
        return RunResult(
            run_id=run_id,
            state=state,
            summary=summary,
            recommendation=synthesis.recommendation,
            confidence_basis_points=synthesis.confidence_basis_points,
            dissent=tuple(item.summary for item in synthesis.dissent),
            approval_id=approval_id,
            action_digest=None if action is None else action.digest,
            sandbox_target=relative_target,
            verified=verified,
        )
