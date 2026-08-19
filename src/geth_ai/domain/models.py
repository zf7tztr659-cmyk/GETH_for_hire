"""Immutable domain values for runs, authority, deliberation, and effects."""

from __future__ import annotations

from pathlib import PurePath
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from geth_ai.domain.base import (
    BasisPoints,
    NonEmptyStr,
    NonNegativeInt,
    Sha256Hex,
    StrictFrozenModel,
    UtcDateTime,
)
from geth_ai.domain.enums import (
    AgentRole,
    ApprovalStatus,
    ConsensusOutcome,
    CritiqueSeverity,
    EvidenceSourceKind,
    MemoryCategory,
    MemoryStatus,
    PolicyOutcome,
    PrincipalKind,
    RiskClass,
    RunState,
    Sensitivity,
    ToolCallState,
    VerificationStatus,
    WorkItemState,
)
from geth_ai.domain.ids import (
    ApprovalId,
    ArtifactId,
    DelegationId,
    EvidenceId,
    GrantId,
    MemoryId,
    PrincipalId,
    RunId,
    ToolCallId,
    WorkItemId,
)


class BudgetExceeded(ValueError):
    """A requested resource charge exceeds its immutable parent budget."""


_BUDGET_FIELDS: tuple[tuple[str, str], ...] = (
    ("max_provider_calls", "provider_calls"),
    ("max_tokens", "tokens"),
    ("max_bytes", "bytes"),
    ("max_retries", "retries"),
    ("max_rounds", "rounds"),
    ("max_recursion_depth", "recursion_depth"),
    ("max_concurrency", "concurrency"),
    ("max_wall_time_ms", "wall_time_ms"),
    ("max_lease_ms", "lease_ms"),
    ("max_tool_calls", "tool_calls"),
)


class BudgetLimits(StrictFrozenModel):
    """Hard upper bounds; zero intentionally means no authority to spend."""

    max_provider_calls: NonNegativeInt = 16
    max_tokens: NonNegativeInt = 16_000
    max_bytes: NonNegativeInt = 1_000_000
    max_retries: NonNegativeInt = 1
    max_rounds: NonNegativeInt = 2
    max_recursion_depth: NonNegativeInt = 1
    max_concurrency: NonNegativeInt = 1
    max_wall_time_ms: NonNegativeInt = 60_000
    max_lease_ms: NonNegativeInt = 30_000
    max_tool_calls: NonNegativeInt = 8

    def is_subset_of(self, parent: BudgetLimits) -> bool:
        return all(
            getattr(self, limit_name) <= getattr(parent, limit_name)
            for limit_name, _ in _BUDGET_FIELDS
        )

    def is_strict_subset_of(self, parent: BudgetLimits) -> bool:
        return self.is_subset_of(parent) and any(
            getattr(self, limit_name) < getattr(parent, limit_name)
            for limit_name, _ in _BUDGET_FIELDS
        )


class BudgetUsage(StrictFrozenModel):
    provider_calls: NonNegativeInt = 0
    tokens: NonNegativeInt = 0
    bytes: NonNegativeInt = 0
    retries: NonNegativeInt = 0
    rounds: NonNegativeInt = 0
    recursion_depth: NonNegativeInt = 0
    concurrency: NonNegativeInt = 0
    wall_time_ms: NonNegativeInt = 0
    lease_ms: NonNegativeInt = 0
    tool_calls: NonNegativeInt = 0


class Budget(StrictFrozenModel):
    limits: BudgetLimits = Field(default_factory=BudgetLimits)
    usage: BudgetUsage = Field(default_factory=BudgetUsage)

    @model_validator(mode="after")
    def usage_cannot_exceed_limits(self) -> Self:
        exceeded = [
            usage_name
            for limit_name, usage_name in _BUDGET_FIELDS
            if getattr(self.usage, usage_name) > getattr(self.limits, limit_name)
        ]
        if exceeded:
            raise ValueError(f"budget exceeded: {', '.join(exceeded)}")
        return self

    def remaining_limits(self) -> BudgetLimits:
        values = {
            limit_name: getattr(self.limits, limit_name)
            - getattr(self.usage, usage_name)
            for limit_name, usage_name in _BUDGET_FIELDS
        }
        return BudgetLimits(**values)

    def can_consume(self, **increments: int) -> bool:
        try:
            self.consume(**increments)
        except (BudgetExceeded, ValueError):
            return False
        return True

    def consume(self, **increments: int) -> Budget:
        known = {usage_name for _, usage_name in _BUDGET_FIELDS}
        unknown = set(increments) - known
        if unknown:
            raise ValueError(f"unknown budget counters: {', '.join(sorted(unknown))}")
        current = self.usage.model_dump(mode="python")
        for name, increment in increments.items():
            if isinstance(increment, bool) or not isinstance(increment, int):
                raise ValueError(f"budget increment for {name} must be an integer")
            if increment < 0:
                raise ValueError(f"budget increment for {name} must be non-negative")
            current[name] += increment
        try:
            return Budget(limits=self.limits, usage=BudgetUsage(**current))
        except ValueError as exc:
            raise BudgetExceeded(str(exc)) from exc

    def child(self, limits: BudgetLimits) -> Budget:
        if not limits.is_subset_of(self.remaining_limits()):
            raise BudgetExceeded("child budget exceeds remaining parent budget")
        return Budget(limits=limits)


def _canonical_absolute_root(value: str) -> str:
    if "\x00" in value:
        raise ValueError("root cannot contain NUL")
    path = PurePath(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("authority root must be absolute and normalized")
    if str(path) != value:
        raise ValueError("authority root must use its canonical spelling")
    return value


class Principal(StrictFrozenModel):
    schema_version: Literal[1] = 1
    principal_id: PrincipalId
    kind: PrincipalKind
    display_name: NonEmptyStr
    created_at: UtcDateTime
    active: bool = True

    @property
    def can_approve(self) -> bool:
        return self.active and self.kind is PrincipalKind.OWNER


class Delegation(StrictFrozenModel):
    schema_version: Literal[1] = 1
    delegation_id: DelegationId
    issuer_id: PrincipalId
    subject_id: PrincipalId
    parent_delegation_id: DelegationId | None = None
    capabilities: frozenset[NonEmptyStr] = Field(min_length=1)
    roots: tuple[NonEmptyStr, ...] = Field(min_length=1)
    budget: BudgetLimits
    issued_at: UtcDateTime
    expires_at: UtcDateTime
    may_delegate: bool = False
    revoked: bool = False

    @field_validator("roots")
    @classmethod
    def roots_are_canonical(cls, roots: tuple[str, ...]) -> tuple[str, ...]:
        canonical = tuple(_canonical_absolute_root(root) for root in roots)
        if len(set(canonical)) != len(canonical):
            raise ValueError("delegation roots must be unique")
        return canonical

    @model_validator(mode="after")
    def valid_window_and_parties(self) -> Self:
        if self.issuer_id == self.subject_id:
            raise ValueError("a delegation must have distinct issuer and subject")
        if self.expires_at <= self.issued_at:
            raise ValueError("delegation expiry must follow issuance")
        return self

    def is_active_at(self, at: UtcDateTime) -> bool:
        return not self.revoked and self.issued_at <= at < self.expires_at


class EvidenceRef(StrictFrozenModel):
    schema_version: Literal[1] = 1
    evidence_id: EvidenceId
    claim: NonEmptyStr
    source_kind: EvidenceSourceKind
    source_locator: NonEmptyStr
    content_sha256: Sha256Hex
    observed_at: UtcDateTime
    uncertainty_basis_points: BasisPoints
    uncertainty_reason: NonEmptyStr
    sensitivity: Sensitivity = Sensitivity.INTERNAL


class Proposal(StrictFrozenModel):
    schema_version: Literal[1] = 1
    objective: NonEmptyStr
    assumptions: tuple[NonEmptyStr, ...] = ()
    steps: tuple[NonEmptyStr, ...] = Field(min_length=1)
    evidence_needed: tuple[NonEmptyStr, ...] = ()
    requested_capabilities: tuple[NonEmptyStr, ...] = ()
    risks: tuple[NonEmptyStr, ...] = ()
    budget: BudgetLimits
    verification: tuple[NonEmptyStr, ...] = Field(min_length=1)


class Critique(StrictFrozenModel):
    schema_version: Literal[1] = 1
    proposal_ref: NonEmptyStr
    challenged_item: NonEmptyStr
    severity: CritiqueSeverity
    concerns: tuple[NonEmptyStr, ...] = Field(min_length=1)
    missing_tests: tuple[NonEmptyStr, ...] = ()
    recommended_revision: NonEmptyStr | None = None
    material: bool = False


class Dissent(StrictFrozenModel):
    schema_version: Literal[1] = 1
    source_role: AgentRole
    summary: NonEmptyStr
    material: bool
    resolved: bool = False


class Synthesis(StrictFrozenModel):
    schema_version: Literal[1] = 1
    outcome: ConsensusOutcome
    recommendation: NonEmptyStr
    rationale: NonEmptyStr
    confidence_basis_points: BasisPoints
    accepted_points: tuple[NonEmptyStr, ...] = ()
    dissent: tuple[Dissent, ...] = ()

    @model_validator(mode="after")
    def material_dissent_cannot_be_hidden_by_pass(self) -> Self:
        if self.outcome is ConsensusOutcome.PASS and any(
            item.material and not item.resolved for item in self.dissent
        ):
            raise ValueError("PASS cannot conceal unresolved material dissent")
        return self


class Run(StrictFrozenModel):
    schema_version: Literal[1] = 1
    run_id: RunId
    owner_id: PrincipalId
    objective: NonEmptyStr
    workspace_root: NonEmptyStr
    state: RunState = RunState.RECEIVED
    version: NonNegativeInt = 0
    budget: Budget = Field(default_factory=Budget)
    created_at: UtcDateTime
    updated_at: UtcDateTime
    lease_expires_at: UtcDateTime
    cancelled_at: UtcDateTime | None = None
    terminal_reason: NonEmptyStr | None = None

    @field_validator("workspace_root")
    @classmethod
    def workspace_is_canonical(cls, value: str) -> str:
        return _canonical_absolute_root(value)

    @model_validator(mode="after")
    def timestamps_and_cancel_state_are_consistent(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("run updated_at cannot precede created_at")
        if self.lease_expires_at <= self.created_at:
            raise ValueError("run lease must extend beyond creation")
        if self.state is RunState.CANCELLED and self.cancelled_at is None:
            raise ValueError("cancelled run requires cancelled_at")
        if self.cancelled_at is not None and self.state is not RunState.CANCELLED:
            raise ValueError("cancelled_at is only valid for CANCELLED")
        return self


class WorkItem(StrictFrozenModel):
    schema_version: Literal[1] = 1
    work_item_id: WorkItemId
    run_id: RunId
    objective: NonEmptyStr
    acceptance_criteria: tuple[NonEmptyStr, ...] = Field(min_length=1)
    assigned_role: AgentRole
    dependencies: tuple[WorkItemId, ...] = ()
    state: WorkItemState = WorkItemState.PENDING
    lease_expires_at: UtcDateTime


class ApprovalRequest(StrictFrozenModel):
    schema_version: Literal[1] = 1
    approval_id: ApprovalId
    action_digest: Sha256Hex
    run_id: RunId
    work_item_id: WorkItemId | None = None
    requester_id: PrincipalId
    owner_id: PrincipalId
    nonce: NonEmptyStr
    created_at: UtcDateTime
    expires_at: UtcDateTime
    status: ApprovalStatus = ApprovalStatus.PENDING

    @model_validator(mode="after")
    def expiry_follows_creation(self) -> Self:
        if self.expires_at <= self.created_at:
            raise ValueError("approval expiry must follow creation")
        return self


class CapabilityGrant(StrictFrozenModel):
    schema_version: Literal[1] = 1
    grant_id: GrantId
    approval_id: ApprovalId
    action_digest: Sha256Hex
    run_id: RunId
    requester_id: PrincipalId
    owner_id: PrincipalId
    expires_at: UtcDateTime
    status: ApprovalStatus = ApprovalStatus.APPROVED

    @model_validator(mode="after")
    def status_is_a_grant_state(self) -> Self:
        allowed = {
            ApprovalStatus.APPROVED,
            ApprovalStatus.CLAIMED,
            ApprovalStatus.CONSUMED,
            ApprovalStatus.EXPIRED,
            ApprovalStatus.CANCELLED,
        }
        if self.status not in allowed:
            raise ValueError("invalid capability grant state")
        return self


class ToolCall(StrictFrozenModel):
    schema_version: Literal[1] = 1
    tool_call_id: ToolCallId
    run_id: RunId
    work_item_id: WorkItemId | None = None
    action_digest: Sha256Hex
    risk_class: RiskClass
    policy_outcome: PolicyOutcome
    state: ToolCallState
    attempt: NonNegativeInt = 0
    started_at: UtcDateTime | None = None
    finished_at: UtcDateTime | None = None
    result_sha256: Sha256Hex | None = None
    error_code: NonEmptyStr | None = None

    @model_validator(mode="after")
    def call_times_are_ordered(self) -> Self:
        if (
            self.started_at is not None
            and self.finished_at is not None
            and self.finished_at < self.started_at
        ):
            raise ValueError("tool call finish cannot precede start")
        return self


class Artifact(StrictFrozenModel):
    schema_version: Literal[1] = 1
    artifact_id: ArtifactId
    run_id: RunId
    producing_call_id: ToolCallId
    artifact_type: NonEmptyStr
    safe_locator: NonEmptyStr
    content_sha256: Sha256Hex
    size_bytes: NonNegativeInt
    verification_status: VerificationStatus = VerificationStatus.PENDING


class MemoryCandidate(StrictFrozenModel):
    schema_version: Literal[1] = 1
    memory_id: MemoryId
    category: MemoryCategory
    content: NonEmptyStr
    sensitivity: Sensitivity
    provenance: tuple[EvidenceRef, ...] = Field(min_length=1)
    source_run_id: RunId
    status: MemoryStatus = MemoryStatus.CANDIDATE
    supersedes_id: MemoryId | None = None


__all__ = [
    "ApprovalRequest",
    "Artifact",
    "Budget",
    "BudgetExceeded",
    "BudgetLimits",
    "BudgetUsage",
    "CapabilityGrant",
    "Critique",
    "Delegation",
    "Dissent",
    "EvidenceRef",
    "MemoryCandidate",
    "Principal",
    "Proposal",
    "Run",
    "StrictFrozenModel",
    "Synthesis",
    "ToolCall",
    "WorkItem",
]
