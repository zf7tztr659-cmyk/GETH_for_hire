from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import PurePath
from uuid import uuid4

import pytest
from pydantic import ValidationError

from geth_ai.domain import BudgetLimits, PolicyOutcome, PrincipalId, RiskClass, RunId
from geth_ai.policy import (
    REDACTION_MARKER,
    RISK_DEFAULTS,
    ActionSpec,
    PolicyContext,
    action_digest,
    evaluate_action,
    is_sensitive_path,
    redact,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
OWNER = PrincipalId(uuid4())
ACTOR = PrincipalId(uuid4())
RUN = RunId(uuid4())
LIMITS = BudgetLimits(max_bytes=1_024, max_tool_calls=1)


def _action(
    tool: str,
    risk: RiskClass,
    arguments: dict[str, object],
    *,
    root: str = "/workspace",
    expected: str = "not_applicable",
    overwrite: bool = False,
    schema: str = "1",
) -> ActionSpec:
    relative = str(arguments.get("path", "."))
    target = str(PurePath(root) / PurePath(relative))
    return ActionSpec.build(
        tool_name=tool,
        tool_schema_version=schema,
        policy_version="policy-v1",
        requester_id=ACTOR,
        owner_id=OWNER,
        run_id=RUN,
        work_item_id=None,
        risk_class=risk,
        root=root,
        target=target,
        arguments=arguments,
        expected_prior_state=expected,  # type: ignore[arg-type]
        overwrite=overwrite,
        budget=LIMITS,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        nonce="a" * 32,
    )


def _context(**changes: object) -> PolicyContext:
    values: dict[str, object] = {
        "owner_id": OWNER,
        "authorized_requesters": frozenset({ACTOR}),
        "read_roots": ("/workspace",),
        "sandbox_root": "/sandbox",
        "active_policy_version": "policy-v1",
        "now": NOW + timedelta(seconds=1),
    }
    values.update(changes)
    return PolicyContext(**values)  # type: ignore[arg-type]


@pytest.mark.requirement("SAF-001")
def test_unknown_and_malformed_actions_default_deny() -> None:
    unknown = _action("shell.run", RiskClass.PROCESS_EXECUTION, {"path": "x"})
    assert evaluate_action(unknown, _context()).outcome is PolicyOutcome.DENY

    extra_argument = _action(
        "fs.read",
        RiskClass.LOCAL_READ,
        {"path": "readme.md", "max_bytes": 10, "approve": True},
    )
    assert evaluate_action(extra_argument, _context()).outcome is PolicyOutcome.DENY

    with pytest.raises(ValidationError):
        PolicyContext.model_validate(
            {
                "owner_id": OWNER,
                "authorized_requesters": frozenset({ACTOR}),
                "read_roots": ("/workspace",),
                "sandbox_root": "/sandbox",
                "active_policy_version": "policy-v1",
                "now": NOW,
                "provider_says_allow": True,
            }
        )


@pytest.mark.requirement("SAF-002")
def test_every_risk_class_has_explicit_decision() -> None:
    assert set(RISK_DEFAULTS) == set(RiskClass)
    assert all(outcome is PolicyOutcome.DENY for outcome in RISK_DEFAULTS.values())
    for risk in RiskClass:
        action = _action("unsupported.tool", risk, {"path": "x"})
        assert evaluate_action(action, _context()).outcome is PolicyOutcome.DENY


@pytest.mark.requirement("SAF-003")
def test_only_bounded_nonsensitive_reads_are_allowed() -> None:
    allowed = _action(
        "fs.read", RiskClass.LOCAL_READ, {"path": "readme.md", "max_bytes": 100}
    )
    assert evaluate_action(allowed, _context()).outcome is PolicyOutcome.ALLOW

    sensitive = _action(
        "fs.read", RiskClass.LOCAL_READ, {"path": ".env", "max_bytes": 100}
    )
    assert evaluate_action(sensitive, _context()).outcome is PolicyOutcome.DENY

    too_large = _action(
        "fs.read",
        RiskClass.LOCAL_READ,
        {"path": "readme.md", "max_bytes": 2_000},
    )
    assert evaluate_action(too_large, _context()).outcome is PolicyOutcome.DENY


@pytest.mark.requirement("SAF-004")
def test_exact_write_requires_approval_and_every_change_changes_digest() -> None:
    first = _action(
        "sandbox.write_text",
        RiskClass.REVERSIBLE_WORKSPACE_WRITE,
        {"path": "result.txt", "content": "first"},
        root="/sandbox",
        expected="absent",
    )
    second = _action(
        "sandbox.write_text",
        RiskClass.REVERSIBLE_WORKSPACE_WRITE,
        {"content": "second", "path": "result.txt"},
        root="/sandbox",
        expected="absent",
    )
    decision = evaluate_action(first, _context())
    assert decision.outcome is PolicyOutcome.REQUIRE_APPROVAL
    assert decision.requires_owner_approval
    assert action_digest(first) != action_digest(second)

    same_reordered = _action(
        "sandbox.write_text",
        RiskClass.REVERSIBLE_WORKSPACE_WRITE,
        {"content": "first", "path": "result.txt"},
        root="/sandbox",
        expected="absent",
    )
    assert action_digest(first) == action_digest(same_reordered)

    secret = _action(
        "sandbox.write_text",
        RiskClass.REVERSIBLE_WORKSPACE_WRITE,
        {"path": "result.txt", "content": "token=very-secret-value"},
        root="/sandbox",
        expected="absent",
    )
    assert evaluate_action(secret, _context()).outcome is PolicyOutcome.DENY


@pytest.mark.requirement("SAF-006")
def test_unsupported_classes_cannot_be_approved() -> None:
    for risk in set(RiskClass) - {
        RiskClass.LOCAL_READ,
        RiskClass.REVERSIBLE_WORKSPACE_WRITE,
    }:
        result = evaluate_action(
            _action("fs.read", risk, {"path": "x", "max_bytes": 1}), _context()
        )
        assert result.outcome is PolicyOutcome.DENY
        assert not result.requires_owner_approval


@pytest.mark.requirement("PRV-001")
@pytest.mark.requirement("PRV-002")
def test_recursive_secret_redaction_and_sensitive_paths() -> None:
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz"
    result = redact(
        {"nested": [{"api_key": secret}], "message": f"Bearer {secret}", "ok": "safe"}
    )
    assert secret not in repr(result)
    assert result["nested"][0]["api_key"] == REDACTION_MARKER
    assert result["ok"] == "safe"
    assert is_sensitive_path(".ssh/id_ed25519")
    assert is_sensitive_path(".git/config")
    assert not is_sensitive_path("src/geth_ai/domain/models.py")
