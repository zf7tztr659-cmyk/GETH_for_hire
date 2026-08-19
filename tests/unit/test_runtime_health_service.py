from __future__ import annotations

from pathlib import Path

import pytest

from geth_ai.application.bootstrap import build_runtime
from geth_ai.application.clock import FrozenClock
from geth_ai.application.health import HealthLevel, HealthService
from geth_ai.config import BudgetDefaults, Settings


@pytest.mark.requirement("OPS-004")
@pytest.mark.requirement("PRV-002")
def test_doctor_checks_local_runtime_without_provider_calls(
    isolated_settings: Settings,
    frozen_clock: FrozenClock,
) -> None:
    runtime = build_runtime(isolated_settings, clock=frozen_clock, recover=False)

    report = runtime.health.check()

    assert report.level is HealthLevel.OK
    assert report.healthy
    assert {check.name for check in report.checks} == {
        "directories",
        "database_schema",
        "fts",
        "sandbox",
        "emergency_stop",
        "audit_chain",
        "provider_mode",
        "budget_defaults",
        "provider_idle",
    }
    assert all(check.level is HealthLevel.OK for check in report.checks)
    assert runtime.provider.ledger == ()


@pytest.mark.requirement("OPR-001")
@pytest.mark.requirement("OPS-004")
def test_doctor_reports_active_emergency_stop_as_degraded(
    isolated_settings: Settings,
    frozen_clock: FrozenClock,
) -> None:
    runtime = build_runtime(isolated_settings, clock=frozen_clock, recover=False)
    runtime.emergency.activate(actor="owner", at=frozen_clock.now())

    report = runtime.health.check()

    assert report.level is HealthLevel.DEGRADED
    assert not report.healthy
    assert report.get("emergency_stop").level is HealthLevel.DEGRADED
    assert runtime.provider.ledger == ()


@pytest.mark.requirement("AUD-002")
@pytest.mark.requirement("OPS-004")
def test_doctor_reports_audit_corruption_without_mutating_or_calling_provider(
    isolated_settings: Settings,
    frozen_clock: FrozenClock,
) -> None:
    runtime = build_runtime(isolated_settings, clock=frozen_clock, recover=False)
    runtime.repositories.runs.create(
        run_id="health-audit-run",
        owner_id=str(runtime.owner.principal_id),
        objective_summary="health audit fixture",
        created_at=frozen_clock.now(),
    )
    connection = runtime.database.connect()
    try:
        connection.execute("DROP TRIGGER events_reject_update")
        connection.execute("UPDATE events SET payload_json = '{}' WHERE sequence = 1")
    finally:
        connection.close()
    event_count = len(runtime.events.list())

    report = runtime.health.check()

    assert report.level is HealthLevel.FAILED
    assert report.get("audit_chain").level is HealthLevel.FAILED
    assert len(runtime.events.list()) == event_count
    assert runtime.provider.ledger == ()


@pytest.mark.requirement("MEM-002")
@pytest.mark.requirement("OPS-004")
def test_doctor_fails_closed_when_fts_is_missing(
    isolated_settings: Settings,
    frozen_clock: FrozenClock,
) -> None:
    runtime = build_runtime(isolated_settings, clock=frozen_clock, recover=False)
    connection = runtime.database.connect()
    try:
        connection.execute("DROP TABLE memory_fts")
    finally:
        connection.close()

    report = runtime.health.check()

    assert report.level is HealthLevel.FAILED
    assert report.get("fts").level is HealthLevel.FAILED
    assert runtime.provider.ledger == ()


@pytest.mark.requirement("OPS-004")
def test_doctor_rejects_runtime_budget_defaults_outside_mvp_bounds(
    isolated_settings: Settings,
    frozen_clock: FrozenClock,
) -> None:
    runtime = build_runtime(isolated_settings, clock=frozen_clock, recover=False)
    unsafe_settings = isolated_settings.model_copy(
        update={"budgets": BudgetDefaults(retries=2, consensus_rounds=3)}
    )
    health = HealthService(
        settings=unsafe_settings,
        events=runtime.events,
        provider=runtime.provider,
        emergency_stop=runtime.emergency,
    )

    report = health.check()

    assert report.level is HealthLevel.FAILED
    assert report.get("budget_defaults").level is HealthLevel.FAILED
    assert runtime.provider.ledger == ()


@pytest.mark.requirement("OPS-004")
def test_doctor_rejects_application_data_inside_workspace(
    tmp_path: Path,
    frozen_clock: FrozenClock,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    safe_settings = Settings.from_environment(
        data_dir=tmp_path / "safe-data",
        workspace_root=workspace,
    )
    runtime = build_runtime(safe_settings, clock=frozen_clock, recover=False)
    nested = workspace / "nested-data"
    nested.mkdir()
    (nested / "sandbox").mkdir()
    unsafe_settings = Settings(data_dir=nested, workspace_root=workspace)
    health = HealthService(
        settings=unsafe_settings,
        events=runtime.events,
        provider=runtime.provider,
        emergency_stop=runtime.emergency,
    )

    report = health.check()

    assert report.level is HealthLevel.FAILED
    assert report.get("directories").level is HealthLevel.FAILED
    assert runtime.provider.ledger == ()
