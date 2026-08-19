"""Deterministic, closed-world action policy."""

from __future__ import annotations

from pathlib import PurePath
from typing import Literal, Self

from pydantic import field_validator, model_validator

from geth_ai.domain.base import NonEmptyStr, StrictFrozenModel, UtcDateTime
from geth_ai.domain.enums import PolicyOutcome, PolicyReason, RiskClass
from geth_ai.domain.ids import PrincipalId
from geth_ai.policy.actions import ActionSpec
from geth_ai.policy.redaction import is_sensitive_path, redact_text

SUPPORTED_TOOL_SCHEMAS: dict[str, str] = {
    "fs.list": "1",
    "fs.read": "1",
    "sandbox.write_text": "1",
}

RISK_DEFAULTS: dict[RiskClass, PolicyOutcome] = {
    RiskClass.LOCAL_READ: PolicyOutcome.DENY,
    RiskClass.REVERSIBLE_WORKSPACE_WRITE: PolicyOutcome.DENY,
    RiskClass.PROCESS_EXECUTION: PolicyOutcome.DENY,
    RiskClass.NETWORK_ACCESS: PolicyOutcome.DENY,
    RiskClass.SECRET_ACCESS: PolicyOutcome.DENY,
    RiskClass.EXTERNAL_COMMUNICATION: PolicyOutcome.DENY,
    RiskClass.FINANCIAL_ACTION: PolicyOutcome.DENY,
    RiskClass.DESTRUCTIVE_ACTION: PolicyOutcome.DENY,
    RiskClass.POLICY_CONFIGURATION_CHANGE: PolicyOutcome.DENY,
}


def _canonical_root(value: str) -> str:
    path = PurePath(value)
    if "\x00" in value or not path.is_absolute() or ".." in path.parts:
        raise ValueError("policy root must be a canonical absolute path")
    if str(path) != value:
        raise ValueError("policy root must use its canonical spelling")
    return value


class PolicyContext(StrictFrozenModel):
    schema_version: Literal[1] = 1
    owner_id: PrincipalId
    authorized_requesters: frozenset[PrincipalId]
    read_roots: tuple[NonEmptyStr, ...]
    sandbox_root: NonEmptyStr
    active_policy_version: NonEmptyStr
    now: UtcDateTime
    cancelled: bool = False
    emergency_stopped: bool = False
    budget_exhausted: bool = False

    @field_validator("read_roots")
    @classmethod
    def validate_read_roots(cls, roots: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(_canonical_root(root) for root in roots)
        if len(set(normalized)) != len(normalized):
            raise ValueError("read roots must be unique")
        return normalized

    @field_validator("sandbox_root")
    @classmethod
    def validate_sandbox_root(cls, root: str) -> str:
        return _canonical_root(root)


class PolicyDecision(StrictFrozenModel):
    schema_version: Literal[1] = 1
    outcome: PolicyOutcome
    reason: PolicyReason
    explanation: NonEmptyStr
    action_digest: NonEmptyStr
    policy_version: NonEmptyStr
    requires_owner_approval: bool = False

    @model_validator(mode="after")
    def approval_flag_matches_outcome(self) -> Self:
        required = self.outcome is PolicyOutcome.REQUIRE_APPROVAL
        if self.requires_owner_approval is not required:
            raise ValueError("approval flag does not match policy outcome")
        return self

    @property
    def allowed(self) -> bool:
        return self.outcome is PolicyOutcome.ALLOW


def _decision(
    action: ActionSpec,
    context: PolicyContext,
    outcome: PolicyOutcome,
    reason: PolicyReason,
    explanation: str,
) -> PolicyDecision:
    return PolicyDecision(
        outcome=outcome,
        reason=reason,
        explanation=explanation,
        action_digest=action.digest,
        policy_version=context.active_policy_version,
        requires_owner_approval=outcome is PolicyOutcome.REQUIRE_APPROVAL,
    )


def _deny(
    action: ActionSpec,
    context: PolicyContext,
    reason: PolicyReason,
    explanation: str,
) -> PolicyDecision:
    return _decision(action, context, PolicyOutcome.DENY, reason, explanation)


def _within(child: str, parent: str) -> bool:
    return PurePath(child).is_relative_to(PurePath(parent))


def _relative_path(value: object) -> str | None:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        return None
    path = PurePath(value)
    if path.is_absolute() or ".." in path.parts or str(path) != value:
        return None
    return value


def _target_matches(action: ActionSpec, relative: str) -> bool:
    return str(PurePath(action.root) / PurePath(relative)) == action.target


def _common_checks(
    action: ActionSpec, context: PolicyContext
) -> PolicyDecision | None:
    if context.cancelled or context.emergency_stopped:
        return _deny(
            action, context, PolicyReason.STOP_REQUESTED, "cancellation or stop wins"
        )
    if context.budget_exhausted or action.budget.max_tool_calls < 1:
        return _deny(
            action, context, PolicyReason.BUDGET_EXHAUSTED, "tool budget is exhausted"
        )
    if action.owner_id != context.owner_id:
        return _deny(
            action, context, PolicyReason.OWNER_MISMATCH, "action owner is not bound owner"
        )
    if action.requester_id not in context.authorized_requesters:
        return _deny(
            action, context, PolicyReason.UNKNOWN_ACTOR, "requester is not authorized"
        )
    if action.policy_version != context.active_policy_version:
        return _deny(
            action,
            context,
            PolicyReason.POLICY_VERSION_MISMATCH,
            "action policy version is stale or unknown",
        )
    if context.now < action.created_at or context.now >= action.expires_at:
        return _deny(
            action, context, PolicyReason.ACTION_EXPIRED, "action binding is not active"
        )
    expected_schema = SUPPORTED_TOOL_SCHEMAS.get(action.tool_name)
    if expected_schema is None:
        return _deny(
            action, context, PolicyReason.UNKNOWN_TOOL, "tool is not registered"
        )
    if action.tool_schema_version != expected_schema:
        return _deny(
            action,
            context,
            PolicyReason.TOOL_SCHEMA_MISMATCH,
            "tool schema version is not supported",
        )
    return None


def _evaluate_read(action: ActionSpec, context: PolicyContext) -> PolicyDecision:
    if action.risk_class is not RiskClass.LOCAL_READ:
        return _deny(
            action, context, PolicyReason.RISK_MISMATCH, "read tool has wrong risk class"
        )
    if not any(_within(action.root, root) for root in context.read_roots):
        return _deny(
            action, context, PolicyReason.ROOT_NOT_ALLOWED, "read root is not delegated"
        )
    arguments = action.decoded_arguments()
    allowed_keys = {"path"} if action.tool_name == "fs.list" else {"path", "max_bytes"}
    if set(arguments) != allowed_keys:
        return _deny(
            action,
            context,
            PolicyReason.TOOL_SCHEMA_MISMATCH,
            "read arguments do not exactly match the tool schema",
        )
    relative = _relative_path(arguments.get("path"))
    if relative is None or not _target_matches(action, relative):
        return _deny(
            action,
            context,
            PolicyReason.TARGET_NOT_CONTAINED,
            "read target does not match its normalized relative path",
        )
    if is_sensitive_path(relative):
        return _deny(
            action, context, PolicyReason.SENSITIVE_PATH, "sensitive paths are denied"
        )
    if action.tool_name == "fs.read":
        maximum = arguments.get("max_bytes")
        if type(maximum) is not int or maximum <= 0 or maximum > action.budget.max_bytes:
            return _deny(
                action,
                context,
                PolicyReason.BUDGET_EXHAUSTED,
                "read byte limit exceeds the bound budget",
            )
    if (
        action.expected_prior_state != "not_applicable"
        or action.overwrite
        or action.content_sha256 is not None
    ):
        return _deny(
            action,
            context,
            PolicyReason.RISK_MISMATCH,
            "read action contains write-only binding fields",
        )
    return _decision(
        action,
        context,
        PolicyOutcome.ALLOW,
        PolicyReason.ALLOWED_BOUNDED_READ,
        "bounded non-sensitive local read is allowed",
    )


def _evaluate_write(action: ActionSpec, context: PolicyContext) -> PolicyDecision:
    if action.risk_class is not RiskClass.REVERSIBLE_WORKSPACE_WRITE:
        return _deny(
            action, context, PolicyReason.RISK_MISMATCH, "write tool has wrong risk class"
        )
    if action.root != context.sandbox_root:
        return _deny(
            action,
            context,
            PolicyReason.ROOT_NOT_ALLOWED,
            "write root is not the dedicated sandbox",
        )
    arguments = action.decoded_arguments()
    if set(arguments) != {"path", "content"}:
        return _deny(
            action,
            context,
            PolicyReason.TOOL_SCHEMA_MISMATCH,
            "write arguments do not exactly match the tool schema",
        )
    relative = _relative_path(arguments.get("path"))
    content = arguments.get("content")
    if relative is None or not _target_matches(action, relative):
        return _deny(
            action,
            context,
            PolicyReason.TARGET_NOT_CONTAINED,
            "write target does not match its normalized relative path",
        )
    if is_sensitive_path(relative):
        return _deny(
            action, context, PolicyReason.SENSITIVE_PATH, "sensitive paths are denied"
        )
    if not isinstance(content, str):
        return _deny(
            action,
            context,
            PolicyReason.INVALID_WRITE_BINDING,
            "write content must be text",
        )
    if redact_text(content) != content:
        return _deny(
            action,
            context,
            PolicyReason.INVALID_WRITE_BINDING,
            "secret-shaped content cannot enter an approval or write",
        )
    payload = content.encode("utf-8")
    import hashlib

    if (
        action.expected_prior_state != "absent"
        or action.overwrite
        or action.content_sha256 != hashlib.sha256(payload).hexdigest()
        or action.content_length != len(payload)
        or len(payload) > action.budget.max_bytes
    ):
        return _deny(
            action,
            context,
            PolicyReason.INVALID_WRITE_BINDING,
            "write digest, length, no-overwrite, precondition, or budget is invalid",
        )
    return _decision(
        action,
        context,
        PolicyOutcome.REQUIRE_APPROVAL,
        PolicyReason.OWNER_APPROVAL_REQUIRED,
        "exact one-use owner approval is required",
    )


def evaluate_action(action: ActionSpec, context: PolicyContext) -> PolicyDecision:
    """Evaluate an already validated action; unknowns never fall through."""

    common = _common_checks(action, context)
    if common is not None:
        return common
    if action.tool_name in {"fs.list", "fs.read"}:
        return _evaluate_read(action, context)
    if action.tool_name == "sandbox.write_text":
        return _evaluate_write(action, context)
    return _deny(
        action,
        context,
        PolicyReason.UNSUPPORTED_CAPABILITY,
        "capability is unsupported in the MVP",
    )


__all__ = [
    "PolicyContext",
    "PolicyDecision",
    "RISK_DEFAULTS",
    "SUPPORTED_TOOL_SCHEMAS",
    "evaluate_action",
]
