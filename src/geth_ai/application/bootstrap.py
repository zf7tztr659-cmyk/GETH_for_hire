"""Composition root for the offline, local-first Geth runtime."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

from geth_ai.application.approvals import ApprovalService
from geth_ai.application.clock import Clock, SystemClock
from geth_ai.application.consensus import ConsensusCoordinator
from geth_ai.application.emergency import EmergencyStop
from geth_ai.application.health import HealthReport, HealthService
from geth_ai.application.memory import MemoryService
from geth_ai.application.orchestrator import Orchestrator, RepositoryBundle
from geth_ai.application.recovery import RecoveryReport, RecoveryService
from geth_ai.config import Settings
from geth_ai.domain.consensus import DeliberationLimits
from geth_ai.domain.enums import PrincipalKind, RunState
from geth_ai.domain.ids import PrincipalId
from geth_ai.domain.models import BudgetLimits, BudgetUsage, Principal
from geth_ai.persistence import (
    ApprovalRepository,
    ArtifactRepository,
    BudgetRepository,
    Database,
    EventStore,
    MemoryRepository,
    MessageRepository,
    RunRepository,
    ToolCallRepository,
    WorkItemRepository,
)
from geth_ai.providers import FakeProvider, FakeScenario
from geth_ai.tools import (
    CapabilityBroker,
    FilesystemListTool,
    FilesystemReadTool,
    SandboxWriteTextTool,
    ToolRegistry,
)
from geth_ai.tools.paths import open_root_fd

DEFAULT_OWNER_ID = UUID("00000000-0000-0000-0000-000000000001")


class BootstrapError(RuntimeError):
    """The local runtime could not be composed without weakening a boundary."""


@dataclass(frozen=True, slots=True)
class Runtime:
    """Fully wired in-process runtime and its startup observations."""

    settings: Settings
    owner: Principal
    clock: Clock
    database: Database
    events: EventStore
    repositories: RepositoryBundle
    memory_repository: MemoryRepository
    registry: ToolRegistry
    broker: CapabilityBroker
    provider: FakeProvider
    consensus: ConsensusCoordinator
    approval_service: ApprovalService
    memory_service: MemoryService
    emergency_stop: EmergencyStop
    recovery: RecoveryService
    health: HealthService
    orchestrator: Orchestrator
    startup_recovery: RecoveryReport | None
    startup_health: HealthReport

    @property
    def approvals(self) -> ApprovalService:
        return self.approval_service

    @property
    def memory(self) -> MemoryService:
        return self.memory_service

    @property
    def emergency(self) -> EmergencyStop:
        return self.emergency_stop

    @property
    def recovery_report(self) -> RecoveryReport | None:
        return self.startup_recovery

    @property
    def health_report(self) -> HealthReport:
        return self.startup_health


ApplicationRuntime = Runtime


def build_runtime(
    settings: Settings,
    *,
    clock: Clock | None = None,
    scenario: FakeScenario = FakeScenario.PASS,
    recover: bool = True,
) -> Runtime:
    """Build one credential-free local runtime with a stable owner identity."""

    runtime_clock = clock or SystemClock()
    owner = Principal(
        principal_id=PrincipalId(DEFAULT_OWNER_ID),
        kind=PrincipalKind.OWNER,
        display_name="Local human owner",
        created_at=runtime_clock.now(),
    )
    return bootstrap_application(
        settings,
        owner=owner,
        clock=runtime_clock,
        scenario=scenario,
        recover=recover,
    )


def bootstrap_application(
    settings: Settings,
    *,
    owner: Principal,
    clock: Clock | None = None,
    scenario: FakeScenario = FakeScenario.PASS,
    recover: bool = True,
) -> Runtime:
    """Compose repositories and trusted services, then run safe startup recovery."""

    runtime_clock = clock or SystemClock()
    try:
        _require_safe_paths(settings)
        settings.ensure_directories()
        for directory in (settings.data_dir, settings.sandbox_root):
            descriptor = open_root_fd(directory)
            os.close(descriptor)

        database = Database(settings.database_path)
        events = EventStore(database)
        runs = RunRepository(events)
        work_items = WorkItemRepository(events)
        messages = MessageRepository(events)
        approval_repository = ApprovalRepository(events)
        tool_calls = ToolCallRepository(events)
        artifacts = ArtifactRepository(events)
        budgets = BudgetRepository(events)
        memory_repository = MemoryRepository(events)
        repositories = RepositoryBundle(
            runs=runs,
            work_items=work_items,
            messages=messages,
            approvals=approval_repository,
            tool_calls=tool_calls,
            artifacts=artifacts,
            budgets=budgets,
        )

        approval_service = ApprovalService(approval_repository, runtime_clock)
        memory_service = MemoryService(memory_repository, runtime_clock)
        emergency_stop = EmergencyStop(settings.emergency_stop_path)

        registry = ToolRegistry(
            (
                FilesystemListTool(settings.workspace_root),
                FilesystemReadTool(
                    settings.workspace_root,
                    max_bytes=settings.budgets.max_read_bytes,
                ),
                FilesystemReadTool(
                    settings.sandbox_root,
                    max_bytes=settings.budgets.max_read_bytes,
                ),
                SandboxWriteTextTool(
                    settings.sandbox_root,
                    max_bytes=settings.budgets.max_read_bytes,
                ),
            )
        )
        broker = CapabilityBroker(
            registry,
            approvals=approval_repository,
            tool_calls=tool_calls,
        )
        provider = FakeProvider(scenario)
        provider_boundaries: dict[
            str, tuple[RunState, int, int, int, int, int, float]
        ] = {}

        def provider_boundary_check(run_id: str) -> None:
            emergency_stop.require_clear()
            durable_run = runs.require(run_id)
            state = RunState(durable_run.state.casefold())
            if state is RunState.CANCELLED:
                raise RuntimeError("durable run cancellation forbids provider calls")
            if durable_run.lease_expires_at is None or runtime_clock.now() >= _parse_time(
                durable_run.lease_expires_at
            ):
                raise RuntimeError("durable run lease expired before provider call")
            budget = budgets.require(run_id)
            limits = BudgetLimits.model_validate(budget.limits)
            usage = BudgetUsage.model_validate(budget.usage)
            baseline = provider_boundaries.get(run_id)
            if baseline is None or baseline[0] is not state:
                baseline = (
                    state,
                    usage.provider_calls,
                    usage.tokens,
                    usage.retries,
                    len(provider.ledger),
                    provider.total_token_units,
                    runtime_clock.monotonic(),
                )
                provider_boundaries[run_id] = baseline
            current_calls = baseline[1] + len(provider.ledger) - baseline[4]
            current_tokens = (
                baseline[2] + provider.total_token_units - baseline[5]
            )
            current_retries = baseline[3] + sum(
                call.status.value == "retryable_failure"
                for call in provider.ledger[baseline[4] :]
            )
            elapsed_ms = int((runtime_clock.monotonic() - baseline[6]) * 1_000)
            if current_calls >= limits.max_provider_calls:
                raise RuntimeError("durable provider-call budget exhausted")
            if current_tokens >= limits.max_tokens:
                raise RuntimeError("durable provider-token budget exhausted")
            if current_retries > limits.max_retries:
                raise RuntimeError("durable provider-retry budget exhausted")
            if elapsed_ms >= limits.max_wall_time_ms:
                raise RuntimeError("durable active wall-time budget exhausted")

        consensus = ConsensusCoordinator(
            provider,
            limits=DeliberationLimits(
                max_rounds=settings.budgets.consensus_rounds,
                max_revisions=1 if settings.budgets.consensus_rounds > 1 else 0,
                max_recursion_depth=0,
            ),
            budget=_budget_limits(settings),
            timeout_seconds=settings.budgets.provider_timeout_seconds,
            boundary_check=provider_boundary_check,
        )
        orchestrator = Orchestrator(
            settings=settings,
            owner=owner,
            clock=runtime_clock,
            events=events,
            repositories=repositories,
            approvals=approval_service,
            broker=broker,
            provider=provider,
            consensus=consensus,
            emergency_stop=emergency_stop,
        )
        recovery_service = RecoveryService(
            settings=settings,
            clock=runtime_clock,
            events=events,
            runs=runs,
            approvals=approval_repository,
            tool_calls=tool_calls,
            artifacts=artifacts,
            emergency_stop=emergency_stop,
        )
        health_service = HealthService(
            settings=settings,
            events=events,
            provider=provider,
            emergency_stop=emergency_stop,
        )
        startup_recovery = recovery_service.recover() if recover else None
        startup_health = health_service.check()
        if provider.ledger:
            raise BootstrapError("startup unexpectedly invoked the provider")
    except BootstrapError:
        raise
    except Exception as exc:
        raise BootstrapError("local runtime bootstrap failed closed") from exc

    return Runtime(
        settings=settings,
        owner=owner,
        clock=runtime_clock,
        database=database,
        events=events,
        repositories=repositories,
        memory_repository=memory_repository,
        registry=registry,
        broker=broker,
        provider=provider,
        consensus=consensus,
        approval_service=approval_service,
        memory_service=memory_service,
        emergency_stop=emergency_stop,
        recovery=recovery_service,
        health=health_service,
        orchestrator=orchestrator,
        startup_recovery=startup_recovery,
        startup_health=startup_health,
    )


def _budget_limits(settings: Settings) -> BudgetLimits:
    values = settings.budgets
    return BudgetLimits(
        max_provider_calls=values.provider_calls,
        max_tokens=values.token_units,
        max_bytes=values.max_run_read_bytes,
        max_retries=values.retries,
        max_rounds=values.consensus_rounds,
        max_recursion_depth=values.recursion_depth,
        max_concurrency=values.concurrency,
        max_wall_time_ms=values.wall_seconds * 1_000,
        max_lease_ms=values.lease_seconds * 1_000,
        max_tool_calls=values.tool_calls,
    )


def _require_safe_paths(settings: Settings) -> None:
    data = settings.data_dir
    workspace = settings.workspace_root
    if not data.is_absolute() or not workspace.is_absolute():
        raise BootstrapError("workspace and application data paths must be absolute")
    try:
        workspace_resolved = workspace.resolve(strict=True)
    except OSError as exc:
        raise BootstrapError("workspace root is unavailable") from exc
    _reject_existing_symlink_components(workspace)
    _reject_existing_symlink_components(data)
    data_resolved = data.resolve(strict=False)
    if data_resolved == workspace_resolved or data_resolved.is_relative_to(
        workspace_resolved
    ):
        raise BootstrapError("application data directory must be outside the workspace")


def _reject_existing_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise BootstrapError("configured paths cannot contain symbolic links")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


__all__ = [
    "ApplicationRuntime",
    "BootstrapError",
    "DEFAULT_OWNER_ID",
    "Runtime",
    "bootstrap_application",
    "build_runtime",
]
