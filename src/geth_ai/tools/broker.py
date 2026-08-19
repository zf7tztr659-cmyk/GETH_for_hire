"""The sole policy and capability gateway to MVP tool adapters."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import PurePath
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from geth_ai.domain.enums import PolicyOutcome
from geth_ai.persistence.repositories import (
    ApprovalRepository,
    ToolCallRepository,
)
from geth_ai.policy.actions import ActionSpec
from geth_ai.policy.engine import PolicyContext, PolicyDecision, evaluate_action

from .paths import UncertainFilesystemOutcome
from .protocol import BrokerResult, PrecommitCheck
from .registry import ToolRegistry


class BrokerError(RuntimeError):
    pass


class PolicyDenied(BrokerError):
    def __init__(self, decision: PolicyDecision) -> None:
        super().__init__(decision.explanation)
        self.decision = decision


class ApprovalRequired(BrokerError):
    def __init__(self, decision: PolicyDecision) -> None:
        super().__init__("exact owner approval is required")
        self.decision = decision


class ToolBindingError(BrokerError):
    pass


class CapabilityBroker:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        approvals: ApprovalRepository | None = None,
        tool_calls: ToolCallRepository | None = None,
        policy_evaluator: Callable[[ActionSpec, PolicyContext], PolicyDecision] = evaluate_action,
    ) -> None:
        self.registry = registry
        self.approvals = approvals
        self.tool_calls = tool_calls
        self.policy_evaluator = policy_evaluator

    def dry_run(self, action: ActionSpec, context: PolicyContext) -> PolicyDecision:
        decision = self.policy_evaluator(action, context)
        if decision.outcome is not PolicyOutcome.DENY:
            self._validate_static_binding(action)
        return decision

    def invoke(
        self,
        *,
        action: ActionSpec,
        context: PolicyContext,
        arguments: Mapping[str, Any] | BaseModel,
        actor_id: str,
        approval_id: str | None = None,
        call_id: str | None = None,
        precommit_check: PrecommitCheck | None = None,
    ) -> BrokerResult:
        call_id = call_id or str(uuid4())
        decision = self.policy_evaluator(action, context)
        if decision.outcome is PolicyOutcome.DENY:
            self._record_proposal(
                call_id, action, actor_id, approval_id, state="DENIED"
            )
            raise PolicyDenied(decision)

        try:
            tool = self._validate_static_binding(action)
            parsed = tool.spec.input_model.model_validate(arguments)
            supplied = parsed.model_dump(mode="json")
            if supplied != action.decoded_arguments():
                raise ToolBindingError(
                    "invocation arguments differ from the approved action"
                )
        except (ToolBindingError, ValidationError):
            self._record_proposal(
                call_id, action, actor_id, approval_id, state="DENIED"
            )
            raise

        if decision.outcome is PolicyOutcome.REQUIRE_APPROVAL:
            if approval_id is None or self.approvals is None:
                self._record_proposal(
                    call_id,
                    action,
                    actor_id,
                    approval_id,
                    state="AWAITING_APPROVAL",
                )
                raise ApprovalRequired(decision)
            self.approvals.claim(
                approval_id,
                run_id=str(action.run_id),
                requester_id=str(action.requester_id),
                action_digest=action.digest,
                claimed_by=actor_id,
                action=action,
                at=context.now,
            )

        self._record_proposal(
            call_id,
            action,
            actor_id,
            approval_id,
            state="AUTHORIZED",
        )
        self._record_state(call_id, actor_id, "RUNNING")
        try:
            output = tool.execute(parsed, precommit_check=precommit_check)
        except UncertainFilesystemOutcome as exc:
            self._record_state(call_id, actor_id, "UNCERTAIN", error_summary=str(exc))
            raise
        except Exception as exc:
            self._record_state(call_id, actor_id, "FAILED", error_summary=str(exc))
            raise
        self._record_state(
            call_id,
            actor_id,
            "SUCCEEDED",
            result=output.model_dump(mode="json"),
        )
        if decision.outcome is PolicyOutcome.REQUIRE_APPROVAL:
            assert approval_id is not None and self.approvals is not None
            self.approvals.consume(approval_id, claimed_by=actor_id, at=context.now)
        return BrokerResult(
            output=output,
            policy_outcome=decision.outcome.value,
            action_digest=action.digest,
            call_id=call_id,
        )

    def _validate_static_binding(self, action: ActionSpec) -> Any:
        tool = self.registry.get(action.tool_name, root=action.root)
        if tool is None:
            raise ToolBindingError("tool is not registered")
        if tool.spec.schema_version != action.tool_schema_version:
            raise ToolBindingError("tool schema version changed")
        if tool.spec.risk_class is not action.risk_class:
            raise ToolBindingError("tool risk class changed")
        if action.root not in tool.spec.allowed_roots:
            raise ToolBindingError("action root is not registered for this tool")
        arguments = action.decoded_arguments()
        relative = arguments.get("path")
        if not isinstance(relative, str):
            raise ToolBindingError("tool action lacks a relative path")
        expected_target = str(PurePath(action.root) / PurePath(relative))
        if expected_target != action.target:
            raise ToolBindingError("action target does not match tool arguments")
        return tool

    def _record_proposal(
        self,
        call_id: str,
        action: ActionSpec,
        actor_id: str,
        approval_id: str | None,
        *,
        state: str,
    ) -> None:
        if self.tool_calls is None:
            return
        existing = self.tool_calls.get(call_id)
        if existing is None:
            self.tool_calls.propose(
                call_id=call_id,
                run_id=str(action.run_id),
                actor_id=actor_id,
                tool_name=action.tool_name,
                action_digest=action.digest,
                approval_id=approval_id,
                state=state,
            )
        elif existing.state != state:
            self.tool_calls.change_state(
                call_id=call_id, actor_id=actor_id, state=state
            )

    def _record_state(
        self,
        call_id: str,
        actor_id: str,
        state: str,
        *,
        result: Mapping[str, Any] | None = None,
        error_summary: str | None = None,
    ) -> None:
        if self.tool_calls is not None:
            self.tool_calls.change_state(
                call_id=call_id,
                actor_id=actor_id,
                state=state,
                result=result,
                error_summary=error_summary,
            )
