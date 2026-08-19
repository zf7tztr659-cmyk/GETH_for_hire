"""Closed vocabularies for Geth's deterministic trusted core."""

from __future__ import annotations

from enum import StrEnum


class AgentRole(StrEnum):
    STEWARD = "steward"
    STRATEGIST = "strategist"
    SKEPTIC = "skeptic"
    SYNTHESIZER = "synthesizer"
    LOCAL_COMMANDER = "local_commander"
    EXECUTOR = "executor"
    VERIFIER = "verifier"


class PrincipalKind(StrEnum):
    OWNER = "owner"
    PARTNER = "partner"
    AGENT = "agent"
    SYSTEM = "system"


class RunState(StrEnum):
    RECEIVED = "received"
    TRIAGED = "triaged"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class WorkItemState(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class RiskClass(StrEnum):
    """Every action class named by SAF-002; there is no catch-all allow case."""

    LOCAL_READ = "local_read"
    REVERSIBLE_WORKSPACE_WRITE = "reversible_workspace_write"
    PROCESS_EXECUTION = "process_execution"
    NETWORK_ACCESS = "network_access"
    SECRET_ACCESS = "secret_or_credential_access"
    EXTERNAL_COMMUNICATION = "external_communication"
    FINANCIAL_ACTION = "financial_or_cost_bearing_action"
    DESTRUCTIVE_ACTION = "destructive_or_irreversible_action"
    POLICY_CONFIGURATION_CHANGE = "policy_or_configuration_change"


class PolicyOutcome(StrEnum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


class PolicyReason(StrEnum):
    ALLOWED_BOUNDED_READ = "allowed_bounded_read"
    OWNER_APPROVAL_REQUIRED = "owner_approval_required"
    UNKNOWN_TOOL = "unknown_tool"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    RISK_MISMATCH = "risk_mismatch"
    UNKNOWN_ACTOR = "unknown_actor"
    OWNER_MISMATCH = "owner_mismatch"
    POLICY_VERSION_MISMATCH = "policy_version_mismatch"
    TOOL_SCHEMA_MISMATCH = "tool_schema_mismatch"
    ACTION_EXPIRED = "action_expired"
    STOP_REQUESTED = "stop_requested"
    BUDGET_EXHAUSTED = "budget_exhausted"
    ROOT_NOT_ALLOWED = "root_not_allowed"
    TARGET_NOT_CONTAINED = "target_not_contained"
    SENSITIVE_PATH = "sensitive_path"
    INVALID_WRITE_BINDING = "invalid_write_binding"


class ConsensusOutcome(StrEnum):
    PASS = "pass"
    REVISE = "revise"
    ESCALATE_TO_OWNER = "escalate_to_owner"


class ConsensusState(StrEnum):
    COLLECTING = "collecting"
    CRITIQUING = "critiquing"
    POLICY_CHECK = "policy_check"
    SYNTHESIZING = "synthesizing"
    PASS = "pass"
    REVISE = "revise"
    ESCALATE_TO_OWNER = "escalate_to_owner"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    CLAIMED = "claimed"
    CONSUMED = "consumed"
    DENIED = "denied"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ToolCallState(StrEnum):
    PROPOSED = "proposed"
    POLICY_EVALUATED = "policy_evaluated"
    AWAITING_APPROVAL = "awaiting_approval"
    AUTHORIZED = "authorized"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    UNCERTAIN = "uncertain"
    DENIED = "denied"


class EvidenceSourceKind(StrEnum):
    OWNER_STATEMENT = "owner_statement"
    LOCAL_FILE = "local_file"
    TOOL_RESULT = "tool_result"
    PROVIDER_OUTPUT = "provider_output"
    VERIFICATION = "verification"


class Sensitivity(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class CritiqueSeverity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    MATERIAL = "material"


class MemoryCategory(StrEnum):
    FACT = "fact"
    OUTCOME = "outcome"
    OWNER_FEEDBACK = "owner_feedback"
    IMPROVEMENT_CANDIDATE = "improvement_candidate"


class MemoryStatus(StrEnum):
    CANDIDATE = "candidate"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    FORGOTTEN = "forgotten"


class VerificationStatus(StrEnum):
    PENDING = "pending"
    VERIFIED = "verified"
    FAILED = "failed"


__all__ = [
    "AgentRole",
    "ApprovalStatus",
    "ConsensusOutcome",
    "ConsensusState",
    "CritiqueSeverity",
    "EvidenceSourceKind",
    "MemoryCategory",
    "MemoryStatus",
    "PolicyOutcome",
    "PolicyReason",
    "PrincipalKind",
    "RiskClass",
    "RunState",
    "Sensitivity",
    "ToolCallState",
    "VerificationStatus",
    "WorkItemState",
]
