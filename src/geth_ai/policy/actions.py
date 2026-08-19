"""Exact, canonically bound action specifications."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import PurePath
from typing import Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from geth_ai.domain.base import (
    NonEmptyStr,
    NonNegativeInt,
    Sha256Hex,
    StrictFrozenModel,
    UtcDateTime,
)
from geth_ai.domain.canonical import (
    canonical_json_bytes,
    canonical_json_text,
    require_canonical_json,
)
from geth_ai.domain.enums import RiskClass
from geth_ai.domain.ids import PrincipalId, RunId, WorkItemId
from geth_ai.domain.models import BudgetLimits


def _canonical_absolute_path(value: str) -> str:
    if "\x00" in value:
        raise ValueError("path cannot contain NUL")
    path = PurePath(value)
    if not path.is_absolute() or ".." in path.parts or str(path) != value:
        raise ValueError("path must be absolute and canonically normalized")
    return value


class ActionSpec(StrictFrozenModel):
    """Everything whose change must invalidate an approval.

    Callers should normally use :meth:`build` so argument text and digest cannot
    diverge. ``arguments_json`` is authoritative and immutable; decoded values
    are fresh copies.
    """

    schema_version: Literal[1] = 1
    normalization_version: Literal["canonical-json-v1"] = "canonical-json-v1"
    tool_name: NonEmptyStr
    tool_schema_version: NonEmptyStr
    policy_version: NonEmptyStr
    requester_id: PrincipalId
    owner_id: PrincipalId
    run_id: RunId
    work_item_id: WorkItemId | None = None
    risk_class: RiskClass
    root: NonEmptyStr
    target: NonEmptyStr
    arguments_json: NonEmptyStr
    arguments_sha256: Sha256Hex
    expected_prior_state: Literal["absent", "regular_file", "not_applicable"]
    overwrite: bool = False
    content_sha256: Sha256Hex | None = None
    content_length: NonNegativeInt | None = None
    budget: BudgetLimits
    created_at: UtcDateTime
    expires_at: UtcDateTime
    nonce: str = Field(min_length=16, max_length=256)

    @field_validator("root", "target")
    @classmethod
    def canonical_paths(cls, value: str) -> str:
        return _canonical_absolute_path(value)

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        arguments = require_canonical_json(self.arguments_json)
        if not isinstance(arguments, dict):
            raise ValueError("action arguments must be a JSON object")
        actual_args_hash = hashlib.sha256(
            self.arguments_json.encode("utf-8")
        ).hexdigest()
        if actual_args_hash != self.arguments_sha256:
            raise ValueError("action argument hash mismatch")
        root = PurePath(self.root)
        target = PurePath(self.target)
        if not target.is_relative_to(root):
            raise ValueError("action target is outside its bound root")
        if self.expires_at <= self.created_at:
            raise ValueError("action expiry must follow creation")
        if (self.content_sha256 is None) != (self.content_length is None):
            raise ValueError("content digest and length must be present together")
        return self

    @classmethod
    def build(
        cls,
        *,
        tool_name: str,
        tool_schema_version: str,
        policy_version: str,
        requester_id: PrincipalId,
        owner_id: PrincipalId,
        run_id: RunId,
        work_item_id: WorkItemId | None,
        risk_class: RiskClass,
        root: str,
        target: str,
        arguments: Mapping[str, Any],
        expected_prior_state: Literal[
            "absent", "regular_file", "not_applicable"
        ],
        overwrite: bool,
        budget: BudgetLimits,
        created_at: UtcDateTime,
        expires_at: UtcDateTime,
        nonce: str,
        content_sha256: str | None = None,
        content_length: int | None = None,
    ) -> ActionSpec:
        text = canonical_json_text(arguments)
        if tool_name == "sandbox.write_text" and isinstance(
            arguments.get("content"), str
        ):
            content = arguments["content"].encode("utf-8")
            derived_sha256 = hashlib.sha256(content).hexdigest()
            derived_length = len(content)
            if content_sha256 is not None and content_sha256 != derived_sha256:
                raise ValueError("provided content digest does not match arguments")
            if content_length is not None and content_length != derived_length:
                raise ValueError("provided content length does not match arguments")
            content_sha256 = derived_sha256
            content_length = derived_length
        return cls(
            tool_name=tool_name,
            tool_schema_version=tool_schema_version,
            policy_version=policy_version,
            requester_id=requester_id,
            owner_id=owner_id,
            run_id=run_id,
            work_item_id=work_item_id,
            risk_class=risk_class,
            root=root,
            target=target,
            arguments_json=text,
            arguments_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            expected_prior_state=expected_prior_state,
            overwrite=overwrite,
            content_sha256=content_sha256,
            content_length=content_length,
            budget=budget,
            created_at=created_at,
            expires_at=expires_at,
            nonce=nonce,
        )

    def decoded_arguments(self) -> dict[str, Any]:
        value = require_canonical_json(self.arguments_json)
        if not isinstance(value, dict):  # defensive; model validator proves this
            raise ValueError("action arguments must be a JSON object")
        return value

    @property
    def digest(self) -> str:
        return action_digest(self)


def canonical_action_bytes(action: ActionSpec) -> bytes:
    return canonical_json_bytes(action.model_dump(mode="python"))


def action_digest(action: ActionSpec) -> str:
    return hashlib.sha256(canonical_action_bytes(action)).hexdigest()


__all__ = [
    "ActionSpec",
    "action_digest",
    "canonical_action_bytes",
    "canonical_json_bytes",
]
