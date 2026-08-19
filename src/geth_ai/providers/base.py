"""Provider boundary for bounded, typed model-like calls.

Providers receive already-redacted immutable requests and return a response that
must pass strict Pydantic validation.  The protocol deliberately exposes no
tool, approval, policy, persistence, or network capability.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from geth_ai.domain.enums import AgentRole

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|auth(?:orization)?|password|secret)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+\-/]+=*")
_COMMON_KEY = re.compile(r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]{8,}\b")
_URI_CREDENTIAL = re.compile(r"(?P<scheme>https?://)[^\s/@:]+:[^\s/@]+@", re.IGNORECASE)


def redact_text(value: str) -> str:
    """Redact common credential shapes before a value crosses the provider boundary."""

    redacted = _SECRET_ASSIGNMENT.sub(r"\1\2[REDACTED]", value)
    redacted = _BEARER.sub("Bearer [REDACTED]", redacted)
    redacted = _COMMON_KEY.sub("[REDACTED]", redacted)
    return _URI_CREDENTIAL.sub(r"\g<scheme>[REDACTED]@", redacted)


class ProviderStage(StrEnum):
    """Typed stages understood by the MVP provider adapter."""

    PROPOSAL = "proposal"
    CRITIQUE = "critique"
    SYNTHESIS = "synthesis"
    LOCAL_PLAN = "local_plan"
    ACTION_PROPOSAL = "action_proposal"
    VERIFICATION = "verification"


class LocalCommanderPlan(BaseModel):
    """A bounded branch plan; it carries no authority to perform its steps."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    objective: str = Field(min_length=1)
    branch: str = Field(min_length=1)
    steps: tuple[str, ...] = Field(min_length=1)
    acceptance_criteria: tuple[str, ...] = Field(min_length=1)


class ExecutorActionProposal(BaseModel):
    """An advisory exact-action proposal, never an execution result or approval."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    action_name: str = Field(min_length=1)
    requested_capability: str = Field(min_length=1)
    arguments_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    advisory_only: Literal[True] = True
    side_effect_performed: Literal[False] = False


class VerifierAssessment(BaseModel):
    """An independent assessment of supplied evidence, with no tool capability."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    passed: bool
    checks: tuple[str, ...] = Field(min_length=1)
    independent: Literal[True] = True
    side_effect_performed: Literal[False] = False


class ProviderContext(BaseModel):
    """One immutable, redacted context item supplied to a role."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    label: str = Field(min_length=1, max_length=80)
    content: str = Field(max_length=32_000)
    source_role: AgentRole | None = None

    @field_validator("label", "content", mode="before")
    @classmethod
    def redact_strings(cls, value: object) -> object:
        return redact_text(value) if isinstance(value, str) else value


class ProviderRequest(BaseModel):
    """The complete typed request; all human/model text is redacted on construction."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    run_id: str = Field(min_length=1, max_length=128)
    role: AgentRole
    stage: ProviderStage
    round_number: int = Field(ge=1, le=2)
    objective: str = Field(min_length=1, max_length=32_000)
    context: tuple[ProviderContext, ...] = ()
    provider_version: str = Field(min_length=1, max_length=80)
    prompt_version: str = Field(min_length=1, max_length=80)

    @field_validator("run_id", "objective", "provider_version", "prompt_version", mode="before")
    @classmethod
    def redact_strings(cls, value: object) -> object:
        return redact_text(value) if isinstance(value, str) else value


class ProviderCallStatus(StrEnum):
    """Observable outcome of one fake-provider call."""

    SUCCEEDED = "succeeded"
    RETRYABLE_FAILURE = "retryable_failure"
    MALFORMED_RESPONSE = "malformed_response"
    TIMED_OUT = "timed_out"


class ProviderCallRecord(BaseModel):
    """Secret-free deterministic call accounting.

    The ledger intentionally stores only a digest and structural context, never
    request or response bodies.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    sequence: int = Field(ge=1)
    role: AgentRole
    stage: ProviderStage
    round_number: int = Field(ge=1, le=2)
    provider_version: str
    prompt_version: str
    request_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_roles: tuple[AgentRole, ...] = ()
    input_token_units: int = Field(ge=0)
    output_token_units: int = Field(ge=0)
    status: ProviderCallStatus
    error_code: str | None = None

    @property
    def total_token_units(self) -> int:
        return self.input_token_units + self.output_token_units


ResponseT = TypeVar("ResponseT", bound=BaseModel)


class ProviderProtocol(Protocol):
    """Small asynchronous adapter boundary for future model providers."""

    @property
    def provider_version(self) -> str: ...

    @property
    def prompt_version(self) -> str: ...

    @property
    def ledger(self) -> tuple[ProviderCallRecord, ...]: ...

    @property
    def total_token_units(self) -> int: ...

    async def complete(
        self,
        request: ProviderRequest,
        response_model: type[ResponseT],
    ) -> ResponseT: ...


class ProviderError(RuntimeError):
    """Base class for bounded provider failures."""


class RetryableProviderError(ProviderError):
    """A transient fake-provider failure that a caller may retry within budget."""


class MalformedProviderResponse(ProviderError):
    """The provider payload did not strictly validate against the requested model."""


class ProviderTimedOut(ProviderError):
    """The provider call exceeded the caller's bounded timeout."""
