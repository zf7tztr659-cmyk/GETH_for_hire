from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from geth_ai.persistence import (
    ApprovalArgumentsUnavailableError,
    ApprovalError,
    ApprovalExpiredError,
    ApprovalMismatchError,
    ApprovalRecord,
    ApprovalRepository,
    ApprovalUnsafeActionError,
    Database,
    EventStore,
    RunRepository,
)


def _requested(
    repository: ApprovalRepository,
    *,
    expires_at: str = "2030-01-01T00:10:00Z",
) -> ApprovalRecord:
    return repository.request(
        approval_id="approval",
        run_id="run",
        requester_id="executor",
        owner_id="owner",
        action={"content": "hello", "path": "demo.txt"},
        action_digest="a" * 64,
        policy_version="mvp-v1",
        expires_at=expires_at,
        nonce="nonce-1",
        created_at="2030-01-01T00:00:00Z",
    )


@pytest.mark.requirement("SAF-005")
def test_exact_approval_is_one_time_and_bound(tmp_path: Path) -> None:
    repository = ApprovalRepository(EventStore(Database(tmp_path / "state.db")))
    _requested(repository)
    repository.approve("approval", owner_id="owner", at="2030-01-01T00:01:00Z")

    with pytest.raises(ApprovalMismatchError):
        repository.claim(
            "approval",
            run_id="other-run",
            requester_id="executor",
            action_digest="a" * 64,
            claimed_by="worker",
            at="2030-01-01T00:02:00Z",
        )

    repository.claim(
        "approval",
        run_id="run",
        requester_id="executor",
        action_digest="a" * 64,
        claimed_by="worker",
        action={"content": "hello", "path": "demo.txt"},
        at="2030-01-01T00:02:00Z",
    )
    repository.consume("approval", claimed_by="worker", at="2030-01-01T00:03:00Z")
    assert repository.require("approval").status == "CONSUMED"
    with pytest.raises(ApprovalError):
        repository.claim(
            "approval",
            run_id="run",
            requester_id="executor",
            action_digest="a" * 64,
            claimed_by="worker",
            at="2030-01-01T00:04:00Z",
        )


@pytest.mark.requirement("SAF-005")
def test_concurrent_redemption_has_one_winner(tmp_path: Path) -> None:
    repository = ApprovalRepository(EventStore(Database(tmp_path / "state.db")))
    _requested(repository)
    repository.approve("approval", owner_id="owner", at="2030-01-01T00:01:00Z")

    def claim(worker: str) -> bool:
        try:
            repository.claim(
                "approval",
                run_id="run",
                requester_id="executor",
                action_digest="a" * 64,
                claimed_by=worker,
                at="2030-01-01T00:02:00Z",
            )
        except ApprovalError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(claim, ("worker-a", "worker-b")))
    assert sorted(outcomes) == [False, True]


@pytest.mark.requirement("SAF-005")
def test_expired_approval_is_durably_expired(tmp_path: Path) -> None:
    repository = ApprovalRepository(EventStore(Database(tmp_path / "state.db")))
    _requested(repository, expires_at="2030-01-01T00:01:00Z")
    with pytest.raises(ApprovalExpiredError):
        repository.approve("approval", owner_id="owner", at="2030-01-01T00:01:00Z")
    assert repository.require("approval").status == "EXPIRED"


@pytest.mark.requirement("SAF-011")
def test_run_cancellation_revokes_pending_or_approved_authority(tmp_path: Path) -> None:
    store = EventStore(Database(tmp_path / "state.db"))
    approvals = ApprovalRepository(store)
    runs = RunRepository(store)
    runs.create(run_id="run", owner_id="owner", objective_summary="demo")
    _requested(approvals)
    approvals.approve("approval", owner_id="owner", at="2030-01-01T00:01:00Z")

    runs.cancel(run_id="run", actor_id="owner", created_at="2030-01-01T00:02:00Z")
    assert approvals.require("approval").status == "CANCELLED"
    with pytest.raises(ApprovalError):
        approvals.claim(
            "approval",
            run_id="run",
            requester_id="executor",
            action_digest="a" * 64,
            claimed_by="worker",
            at="2030-01-01T00:03:00Z",
        )


@pytest.mark.requirement("AUD-003")
@pytest.mark.requirement("SAF-004")
def test_approval_audit_omits_arguments_but_live_projection_keeps_exact_action(
    tmp_path: Path,
) -> None:
    store = EventStore(Database(tmp_path / "state.db"))
    approvals = ApprovalRepository(store)
    raw_content = "ordinary exact content that must not enter audit"
    exact_action = {"path": "demo.txt", "content": raw_content}

    created = approvals.request(
        approval_id="approval-safe-audit",
        run_id="run",
        requester_id="executor",
        owner_id="owner",
        action=exact_action,
        action_digest="b" * 64,
        policy_version="mvp-v1",
        expires_at="2030-01-01T00:10:00Z",
        nonce="nonce-safe-audit",
        created_at="2030-01-01T00:00:00Z",
    )
    assert created.action_available
    assert created.action == exact_action
    assert approvals.require_exact_action(created.approval_id) == exact_action

    reopened = ApprovalRepository(EventStore(Database(tmp_path / "state.db")))
    assert reopened.require(created.approval_id).action == exact_action
    assert reopened.require(created.approval_id).action_available

    event = store.list(run_id="run")[0]
    serialized = json.dumps(event.payload, sort_keys=True)
    assert event.event_type == "ApprovalRequested"
    assert "action" not in event.payload
    assert "arguments" not in event.payload
    assert raw_content not in serialized
    metadata = event.payload["action_metadata"]
    assert metadata["relative_path"] == "demo.txt"
    assert metadata["content_length"] == len(raw_content.encode())
    assert metadata["content_sha256"]

    # Canonical history intentionally lacks executable arguments. Rebuild keeps
    # the request inspectable but action_available=false makes approval/claim fail.
    store.rebuild_projections()
    rebuilt = approvals.require(created.approval_id)
    assert not rebuilt.action_available
    assert rebuilt.action == {}
    with pytest.raises(ApprovalArgumentsUnavailableError):
        approvals.require_exact_action(created.approval_id)
    with pytest.raises(ApprovalArgumentsUnavailableError):
        approvals.approve(
            created.approval_id,
            owner_id="owner",
            at="2030-01-01T00:01:00Z",
        )


@pytest.mark.requirement("PRV-001")
def test_secret_shaped_action_is_rejected_before_event_or_projection(
    tmp_path: Path,
) -> None:
    store = EventStore(Database(tmp_path / "state.db"))
    approvals = ApprovalRepository(store)
    with pytest.raises(ApprovalUnsafeActionError):
        approvals.request(
            approval_id="unsafe",
            run_id="run",
            requester_id="executor",
            owner_id="owner",
            action={"path": "demo.txt", "content": "token=very-secret-value"},
            action_digest="c" * 64,
            policy_version="mvp-v1",
            expires_at="2030-01-01T00:10:00Z",
            nonce="nonce-unsafe-action",
            created_at="2030-01-01T00:00:00Z",
        )
    assert store.list(run_id="run") == ()
    assert approvals.get("unsafe") is None


@pytest.mark.requirement("SAF-005")
@pytest.mark.requirement("OPR-001")
def test_rebuilt_approved_request_cannot_be_claimed_without_exact_action(
    tmp_path: Path,
) -> None:
    store = EventStore(Database(tmp_path / "state.db"))
    approvals = ApprovalRepository(store)
    created = approvals.request(
        approval_id="approved-before-rebuild",
        run_id="run",
        requester_id="executor",
        owner_id="owner",
        action={"path": "demo.txt", "content": "ordinary content"},
        action_digest="f" * 64,
        policy_version="mvp-v1",
        expires_at="2030-01-01T00:10:00Z",
        nonce="nonce-approved-rebuild",
        created_at="2030-01-01T00:00:00Z",
    )
    approvals.approve(
        created.approval_id,
        owner_id="owner",
        at="2030-01-01T00:01:00Z",
    )

    store.rebuild_projections()
    rebuilt = approvals.require(created.approval_id)
    assert rebuilt.status == "APPROVED"
    assert not rebuilt.action_available
    with pytest.raises(ApprovalArgumentsUnavailableError):
        approvals.claim(
            created.approval_id,
            run_id="run",
            requester_id="executor",
            action_digest="f" * 64,
            claimed_by="worker",
            action={"path": "demo.txt", "content": "ordinary content"},
            at="2030-01-01T00:02:00Z",
        )
