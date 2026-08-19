"""Smoke and lifecycle coverage for the complete documented CLI surface."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import pytest
from typer.testing import Result


class CliHarness(Protocol):
    data_dir: Path

    def invoke(self, *arguments: str) -> Result: ...

    def field(self, result: Result, label: str) -> str: ...

    def sandbox_snapshot(self) -> dict[str, bytes]: ...


def _assert_success(result: Result) -> None:
    assert result.exit_code == 0, result.output


@pytest.mark.requirement("CLI-001")
def test_full_console_command_and_error_matrix(cli_harness: CliHarness) -> None:
    initialized = cli_harness.invoke("init")
    _assert_success(initialized)
    assert "Geth initialized." in initialized.stdout
    assert "Provider: fake (offline)" in initialized.stdout
    _assert_success(cli_harness.invoke("init"))

    demo = cli_harness.invoke("demo")
    _assert_success(demo)
    assert demo.stdout.splitlines()[0] == "AWAITING_APPROVAL"
    demo_approval = cli_harness.field(demo, "Approval")

    run = cli_harness.invoke("run", "Create an exact CLI artifact", "--provider", "fake")
    _assert_success(run)
    run_id = cli_harness.field(run, "Run")
    approval_id = cli_harness.field(run, "Approval")
    target = cli_harness.field(run, "Target").removeprefix("sandbox/")

    listed = cli_harness.invoke("task", "list")
    _assert_success(listed)
    assert run_id in listed.stdout
    shown = cli_harness.invoke("task", "show", run_id)
    _assert_success(shown)
    assert f"Run: {run_id}" in shown.stdout
    assert "state: awaiting_approval" in shown.stdout.casefold()
    assert f"- {approval_id} PENDING" in shown.stdout

    completed = cli_harness.invoke("approve", approval_id)
    _assert_success(completed)
    assert "Approving exact action:" in completed.stdout
    assert "COMPLETED" in completed.stdout.splitlines()
    assert "Verified: yes" in completed.stdout
    snapshot = cli_harness.sandbox_snapshot()
    assert set(snapshot) == {target}
    with_replay = cli_harness.invoke("approve", approval_id)
    assert with_replay.exit_code != 0
    assert "not awaiting approval" in with_replay.output
    assert cli_harness.sandbox_snapshot() == snapshot

    denied_run = cli_harness.invoke("run", "Prepare an artifact that I will deny")
    _assert_success(denied_run)
    denied_approval = cli_harness.field(denied_run, "Approval")
    before_deny = cli_harness.sandbox_snapshot()
    denied = cli_harness.invoke("deny", denied_approval)
    _assert_success(denied)
    assert denied.stdout.splitlines()[0] == "BLOCKED"
    assert cli_harness.sandbox_snapshot() == before_deny

    cancelled_run = cli_harness.invoke("run", "Prepare an artifact that I will cancel")
    _assert_success(cancelled_run)
    cancelled_id = cli_harness.field(cancelled_run, "Run")
    cancelled_approval = cli_harness.field(cancelled_run, "Approval")
    before_cancel = cli_harness.sandbox_snapshot()
    cancelled = cli_harness.invoke("cancel", cancelled_id)
    _assert_success(cancelled)
    assert cancelled.stdout.splitlines()[0] == "CANCELLED"
    assert cli_harness.invoke("approve", cancelled_approval).exit_code != 0
    assert cli_harness.sandbox_snapshot() == before_cancel

    remembered = cli_harness.invoke(
        "memory",
        "remember",
        run_id,
        "owner retained CLI observation",
    )
    _assert_success(remembered)
    memory_id = cli_harness.field(remembered, "Remembered")
    searched = cli_harness.invoke("memory", "search", "retained CLI observation")
    _assert_success(searched)
    assert memory_id in searched.stdout
    assert "owner retained CLI observation" in searched.stdout
    exported = cli_harness.invoke("memory", "export")
    _assert_success(exported)
    assert memory_id in exported.stdout
    forgotten = cli_harness.invoke("memory", "forget", memory_id)
    _assert_success(forgotten)
    assert f"Forgotten: {memory_id}" in forgotten.stdout
    no_matches = cli_harness.invoke("memory", "search", "retained CLI observation")
    _assert_success(no_matches)
    assert "No memory matches." in no_matches.stdout

    _assert_success(cli_harness.invoke("deny", demo_approval))

    invalid_commands = (
        ("task", "show", "missing-run"),
        ("approve", "missing-approval"),
        ("deny", "missing-approval"),
        ("cancel", "missing-run"),
        ("audit", "show", "missing-run"),
        ("memory", "forget", "missing-memory"),
    )
    for arguments in invalid_commands:
        invalid = cli_harness.invoke(*arguments)
        assert invalid.exit_code != 0
        assert "Error:" in invalid.output

    unsupported = cli_harness.invoke(
        "run",
        "Do not contact a live provider",
        "--provider",
        "openai",
    )
    assert unsupported.exit_code != 0
    assert "supports only the deterministic fake provider" in unsupported.output
