"""Provider-free local health command acceptance tests."""

from __future__ import annotations

from typing import Protocol

import pytest
from typer.testing import Result


class CliHarness(Protocol):
    def invoke(self, *arguments: str) -> Result: ...


@pytest.mark.requirement("OPS-004")
def test_doctor_reports_health_without_provider_or_network(
    cli_harness: CliHarness,
) -> None:
    result = cli_harness.invoke("doctor")

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[0] == "Geth doctor: HEALTHY"
    for name in (
        "directories",
        "database_schema",
        "fts",
        "sandbox",
        "emergency_stop",
        "audit_chain",
        "provider_mode",
        "budget_defaults",
        "provider_idle",
    ):
        assert f"[OK] {name}:" in result.stdout
    assert "doctor made no provider calls" in result.stdout

    audit = cli_harness.invoke("audit", "verify")
    assert audit.exit_code == 0
    assert "Events: 0" in audit.stdout
    tasks = cli_harness.invoke("task", "list")
    assert tasks.exit_code == 0
    assert tasks.stdout.strip() == "No tasks."
