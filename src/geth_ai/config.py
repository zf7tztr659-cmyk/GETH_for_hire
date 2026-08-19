"""Runtime configuration with platform-local state defaults."""

from __future__ import annotations

import os
import sys
from contextlib import suppress
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator


def platform_data_dir() -> Path:
    """Return a per-user state directory that is outside the repository by default."""

    override = os.environ.get("GETH_AI_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "GethAI"
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "GethAI"
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "geth-ai"


class BudgetDefaults(BaseModel):
    """Parent limits that all role and tool work must share."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_calls: int = Field(default=12, ge=1, le=100)
    tool_calls: int = Field(default=3, ge=1, le=20)
    retries: int = Field(default=1, ge=0, le=5)
    consensus_rounds: int = Field(default=2, ge=1, le=4)
    recursion_depth: int = Field(default=1, ge=0, le=4)
    concurrency: int = Field(default=3, ge=1, le=8)
    token_units: int = Field(default=32_000, ge=1_000, le=1_000_000)
    max_read_bytes: int = Field(default=1_048_576, ge=1, le=16_777_216)
    max_run_read_bytes: int = Field(default=4_194_304, ge=1, le=67_108_864)
    wall_seconds: int = Field(default=60, ge=1, le=3_600)
    lease_seconds: int = Field(default=60, ge=1, le=3_600)
    provider_timeout_seconds: int = Field(default=10, ge=1, le=120)
    tool_timeout_seconds: int = Field(default=5, ge=1, le=120)
    approval_ttl_seconds: int = Field(default=600, ge=30, le=86_400)


class Settings(BaseModel):
    """Validated paths and bounded defaults for one explicit CLI process."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True, extra="forbid")

    data_dir: Path
    workspace_root: Path
    provider: str = "fake"
    budgets: BudgetDefaults = Field(default_factory=BudgetDefaults)

    @field_validator("provider")
    @classmethod
    def fake_provider_only(cls, value: str) -> str:
        if value != "fake":
            raise ValueError("the MVP supports only the deterministic fake provider")
        return value

    @classmethod
    def from_environment(
        cls,
        *,
        data_dir: Path | None = None,
        workspace_root: Path | None = None,
        provider: str = "fake",
        budgets: BudgetDefaults | None = None,
    ) -> Settings:
        workspace = workspace_root or Path(os.environ.get("GETH_AI_WORKSPACE", Path.cwd()))
        return cls(
            data_dir=(data_dir or platform_data_dir()).expanduser().resolve(),
            workspace_root=workspace.expanduser().resolve(),
            provider=provider,
            budgets=budgets or BudgetDefaults(),
        )

    @property
    def database_path(self) -> Path:
        return self.data_dir / "geth-ai.sqlite3"

    @property
    def sandbox_root(self) -> Path:
        return self.data_dir / "sandbox"

    @property
    def emergency_stop_path(self) -> Path:
        return self.data_dir / "EMERGENCY_STOP"

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.sandbox_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        for directory in (self.data_dir, self.sandbox_root):
            with suppress(OSError):
                directory.chmod(0o700)
