"""Read-only local runtime health checks used by the doctor command."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from geth_ai.application.emergency import EmergencyStop
from geth_ai.config import Settings
from geth_ai.persistence import EventStore
from geth_ai.persistence.migrations import LATEST_SCHEMA_VERSION
from geth_ai.providers import FakeProvider, ProviderProtocol
from geth_ai.tools.paths import open_root_fd


class HealthLevel(StrEnum):
    """Severity of one doctor check or the aggregate report."""

    OK = "ok"
    DEGRADED = "degraded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class HealthCheck:
    """One concise, non-sensitive health observation."""

    name: str
    level: HealthLevel
    summary: str

    @property
    def healthy(self) -> bool:
        return self.level is HealthLevel.OK


@dataclass(frozen=True, slots=True)
class HealthReport:
    """A provider-free snapshot of local runtime readiness."""

    level: HealthLevel
    checks: tuple[HealthCheck, ...]

    @property
    def healthy(self) -> bool:
        return self.level is HealthLevel.OK

    def get(self, name: str) -> HealthCheck:
        for check in self.checks:
            if check.name == name:
                return check
        raise KeyError(name)


@dataclass(slots=True)
class HealthService:
    """Inspect local state without invoking a provider, tool, or network adapter."""

    settings: Settings
    events: EventStore
    provider: ProviderProtocol
    emergency_stop: EmergencyStop

    def check(self) -> HealthReport:
        """Run all doctor checks and convert local failures into a fail-closed report."""

        provider_calls_before = len(self.provider.ledger)
        checks: tuple[HealthCheck, ...] = (
            self._directories(),
            self._database_schema(),
            self._fts(),
            self._sandbox(),
            self._emergency_stop(),
            self._audit_chain(),
            self._provider_mode(),
            self._budget_defaults(),
        )
        provider_calls_after = len(self.provider.ledger)
        if provider_calls_after != provider_calls_before:
            checks = checks + (
                HealthCheck(
                    "provider_idle",
                    HealthLevel.FAILED,
                    "doctor unexpectedly changed the provider ledger",
                ),
            )
        else:
            checks = checks + (
                HealthCheck(
                    "provider_idle",
                    HealthLevel.OK,
                    "doctor made no provider calls",
                ),
            )
        level = _aggregate(checks)
        return HealthReport(level=level, checks=checks)

    def _directories(self) -> HealthCheck:
        try:
            workspace = _real_directory(self.settings.workspace_root)
            data = _real_directory(self.settings.data_dir)
            sandbox = _real_directory(self.settings.sandbox_root)
            if data.is_relative_to(workspace):
                raise ValueError("application data is inside the workspace")
            if not os.access(workspace, os.R_OK | os.X_OK):
                raise PermissionError("workspace is not readable")
            for directory in (data, sandbox):
                if not os.access(directory, os.R_OK | os.W_OK | os.X_OK):
                    raise PermissionError("runtime directory is not accessible")
        except Exception as exc:
            return _failed("directories", exc)
        return HealthCheck(
            "directories",
            HealthLevel.OK,
            "workspace and private runtime directories are available",
        )

    def _database_schema(self) -> HealthCheck:
        try:
            connection = self.events.database.connect()
            try:
                rows = connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall()
                versions = tuple(int(row["version"]) for row in rows)
                expected = tuple(range(1, LATEST_SCHEMA_VERSION + 1))
                if versions != expected:
                    raise RuntimeError("database schema version is unsupported")
                quick_check = connection.execute("PRAGMA quick_check").fetchone()
                if quick_check is None or str(quick_check[0]).casefold() != "ok":
                    raise RuntimeError("database integrity check failed")
            finally:
                connection.close()
        except Exception as exc:
            return _failed("database_schema", exc)
        return HealthCheck(
            "database_schema",
            HealthLevel.OK,
            f"SQLite schema {LATEST_SCHEMA_VERSION} is current",
        )

    def _fts(self) -> HealthCheck:
        try:
            connection = self.events.database.connect()
            try:
                connection.execute("SELECT count(*) FROM memory_fts").fetchone()
            finally:
                connection.close()
        except Exception as exc:
            return _failed("fts", exc)
        return HealthCheck("fts", HealthLevel.OK, "SQLite FTS5 memory index is available")

    def _sandbox(self) -> HealthCheck:
        try:
            descriptor = open_root_fd(self.settings.sandbox_root)
            os.close(descriptor)
        except Exception as exc:
            return _failed("sandbox", exc)
        return HealthCheck(
            "sandbox",
            HealthLevel.OK,
            "sandbox root opens without following links",
        )

    def _emergency_stop(self) -> HealthCheck:
        try:
            active = self.emergency_stop.is_active()
        except Exception as exc:
            return _failed("emergency_stop", exc)
        if active:
            return HealthCheck(
                "emergency_stop",
                HealthLevel.DEGRADED,
                "global emergency stop is active; execution is disabled",
            )
        return HealthCheck(
            "emergency_stop",
            HealthLevel.OK,
            "global emergency stop is clear",
        )

    def _audit_chain(self) -> HealthCheck:
        try:
            report = self.events.verify()
        except Exception as exc:
            return _failed("audit_chain", exc)
        if not report.valid:
            return HealthCheck(
                "audit_chain",
                HealthLevel.FAILED,
                f"audit chain validation failed ({len(report.errors)} errors)",
            )
        return HealthCheck(
            "audit_chain",
            HealthLevel.OK,
            f"audit chain is valid ({report.event_count} events)",
        )

    def _provider_mode(self) -> HealthCheck:
        if self.settings.provider != "fake" or not isinstance(self.provider, FakeProvider):
            return HealthCheck(
                "provider_mode",
                HealthLevel.FAILED,
                "runtime provider is outside the offline MVP allowlist",
            )
        return HealthCheck(
            "provider_mode",
            HealthLevel.OK,
            f"offline deterministic provider is configured ({self.provider.provider_version})",
        )

    def _budget_defaults(self) -> HealthCheck:
        budgets = self.settings.budgets
        valid = (
            budgets.provider_calls > 0
            and budgets.tool_calls > 0
            and 0 <= budgets.retries <= 1
            and 1 <= budgets.consensus_rounds <= 2
            and budgets.recursion_depth <= 1
            and budgets.concurrency > 0
            and budgets.token_units > 0
            and 0 < budgets.max_read_bytes <= budgets.max_run_read_bytes
            and budgets.wall_seconds > 0
            and budgets.lease_seconds > 0
            and budgets.provider_timeout_seconds > 0
            and budgets.tool_timeout_seconds > 0
            and budgets.approval_ttl_seconds > 0
        )
        if not valid:
            return HealthCheck(
                "budget_defaults",
                HealthLevel.FAILED,
                "runtime budget defaults exceed MVP safety bounds",
            )
        return HealthCheck(
            "budget_defaults",
            HealthLevel.OK,
            "bounded provider, retry, tool, byte, time, and concurrency defaults are active",
        )


def _real_directory(path: Path) -> Path:
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ValueError("configured path is not a real directory")
    return path.resolve(strict=True)


def _failed(name: str, _error: Exception) -> HealthCheck:
    # Health output intentionally does not echo paths, SQL, or exception values.
    return HealthCheck(name, HealthLevel.FAILED, f"{name.replace('_', ' ')} check failed")


def _aggregate(checks: tuple[HealthCheck, ...]) -> HealthLevel:
    if any(check.level is HealthLevel.FAILED for check in checks):
        return HealthLevel.FAILED
    if any(check.level is HealthLevel.DEGRADED for check in checks):
        return HealthLevel.DEGRADED
    return HealthLevel.OK


__all__ = [
    "HealthCheck",
    "HealthLevel",
    "HealthReport",
    "HealthService",
]
