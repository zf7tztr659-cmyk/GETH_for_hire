"""Answer-first output and structured redacted diagnostics."""

from __future__ import annotations

import json
from typing import Protocol

import pytest
from typer.testing import Result


class CliHarness(Protocol):
    def invoke(self, *arguments: str) -> Result: ...

    def field(self, result: Result, label: str) -> str: ...


@pytest.mark.requirement("FUN-009")
def test_answer_first_output_retains_dissent_and_audit_link(
    cli_harness: CliHarness,
) -> None:
    result = cli_harness.invoke("run", "Create one reviewable artifact")

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0] == "AWAITING_APPROVAL"
    assert any(line.startswith("Recommendation: ") for line in lines)
    assert any(line.startswith("Status: ") for line in lines)
    assert any(line.startswith("Confidence: ") and line.endswith("%") for line in lines)
    assert "Dissent:" in lines
    assert any("approval" in line.casefold() for line in lines if line.startswith("- "))
    assert any(line.startswith("Approval: ") for line in lines)
    assert "Action: sandbox.write_text" in lines
    assert any(line.startswith("Target: sandbox/") for line in lines)
    assert any(line.startswith("Digest: ") and len(line.split()[-1]) == 64 for line in lines)
    assert any(line.startswith("Expires: ") for line in lines)
    assert "Verified: no" in lines
    run_id = cli_harness.field(result, "Run")
    assert f"Audit: geth-ai audit show {run_id}" in lines
    assert len(lines) <= 20
    assert not result.stderr


@pytest.mark.requirement("OPS-003")
def test_logs_are_structured_redacted_concise_or_verbose(
    cli_harness: CliHarness,
) -> None:
    canary = "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"
    quiet = cli_harness.invoke("run", f"Review password=hunter2 and {canary}")
    assert quiet.exit_code == 0, quiet.output
    assert canary not in quiet.output
    assert "hunter2" not in quiet.output
    assert not quiet.stderr
    run_id = cli_harness.field(quiet, "Run")

    verbose = cli_harness.invoke("--verbose", "task", "show", run_id)
    assert verbose.exit_code == 0, verbose.output
    assert canary not in verbose.output
    assert "hunter2" not in verbose.output
    assert "[REDACTED]" in verbose.stdout
    log_lines = [line for line in verbose.stderr.splitlines() if line.startswith("{")]
    assert log_lines
    for line in log_lines:
        record = json.loads(line)
        assert record["level"] == "debug"
        assert record["event"] == "runtime_ready"
        assert record["fields"]["provider"] == "fake"

    failed = cli_harness.invoke("task", "show", "missing-run")
    assert failed.exit_code != 0
    error_lines = [line for line in failed.stderr.splitlines() if line.startswith("{")]
    assert error_lines
    error = json.loads(error_lines[-1])
    assert error["level"] == "error"
    assert error["event"] == "command_failed"
    assert "Error:" in failed.stderr
