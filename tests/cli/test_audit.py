"""Inspectable audit output and honest integrity limitations."""

from __future__ import annotations

from typing import Protocol

import pytest
from typer.testing import Result


class CliHarness(Protocol):
    def invoke(self, *arguments: str) -> Result: ...

    def field(self, result: Result, label: str) -> str: ...


@pytest.mark.requirement("AUD-003")
def test_audit_show_is_ordered_minimized_and_optionally_verbose(
    cli_harness: CliHarness,
) -> None:
    canary = "sk-proj-AUDITCANARYABCDEFGHIJKLMNOPQRSTUVWXYZ"
    started = cli_harness.invoke("run", f"Audit this objective with {canary}")
    assert started.exit_code == 0, started.output
    run_id = cli_harness.field(started, "Run")

    concise = cli_harness.invoke("audit", "show", run_id)
    assert concise.exit_code == 0, concise.output
    assert canary not in concise.output
    assert "RunCreated" in concise.stdout
    assert "RunStateChanged" in concise.stdout
    assert "ConsensusCompleted" in concise.stdout
    assert "PolicyEvaluated" in concise.stdout
    assert "ApprovalRequested" in concise.stdout
    sequences = [int(line.split(maxsplit=1)[0]) for line in concise.stdout.splitlines()]
    assert sequences == list(range(1, len(sequences) + 1))

    verbose = cli_harness.invoke("--verbose", "audit", "show", run_id)
    assert verbose.exit_code == 0, verbose.output
    assert canary not in verbose.output
    assert "payload" not in concise.stdout.casefold()
    assert "objective_summary" in verbose.stdout
    assert "[REDACTED]" in verbose.stdout


@pytest.mark.requirement("AUD-004")
def test_verify_reports_unanchored_chain_limitations(cli_harness: CliHarness) -> None:
    assert cli_harness.invoke("init").exit_code == 0
    verified = cli_harness.invoke("audit", "verify")

    assert verified.exit_code == 0, verified.output
    assert "Audit chain: VALID" in verified.stdout
    assert "Events: 0" in verified.stdout
    assert "Limitation:" in verified.stdout
    limitation = verified.stdout.casefold()
    assert "tamper-evident, not tamper-proof" in limitation
    assert "replace/recompute" in limitation
    assert "tail truncation" in limitation
    assert "independently retained head checkpoint" in limitation
