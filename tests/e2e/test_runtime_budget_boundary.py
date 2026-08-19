from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from geth_ai.application.bootstrap import build_runtime
from geth_ai.application.orchestrator import OrchestrationError
from geth_ai.config import BudgetDefaults, Settings


@pytest.mark.requirement("OPS-001")
def test_cross_stage_provider_budget_stops_before_an_extra_call(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    settings = Settings.from_environment(
        data_dir=tmp_path / "data",
        workspace_root=workspace,
        budgets=BudgetDefaults(provider_calls=4),
    )
    runtime = build_runtime(settings)

    with pytest.raises(OrchestrationError, match="action planning failed"):
        asyncio.run(runtime.orchestrator.start_run("Stay within the global call budget"))

    assert len(runtime.provider.ledger) == 4
    assert runtime.repositories.approvals.list_for_run(
        runtime.repositories.runs.list()[0].run_id
    ) == ()
    assert list(runtime.settings.sandbox_root.iterdir()) == []
