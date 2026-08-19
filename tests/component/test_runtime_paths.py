from __future__ import annotations

import os
from pathlib import Path

import pytest

from geth_ai.config import Settings, platform_data_dir


@pytest.mark.requirement("OPS-005")
def test_explicit_runtime_state_is_outside_workspace_and_private(tmp_path: Path) -> None:
    workspace = tmp_path / "icloud-workspace"
    workspace.mkdir()
    settings = Settings.from_environment(
        data_dir=tmp_path / "platform-state",
        workspace_root=workspace,
    )

    settings.ensure_directories()

    assert not settings.database_path.is_relative_to(workspace)
    assert not settings.sandbox_root.is_relative_to(workspace)
    if os.name != "nt":
        assert settings.data_dir.stat().st_mode & 0o777 == 0o700
        assert settings.sandbox_root.stat().st_mode & 0o777 == 0o700


@pytest.mark.requirement("OPS-005")
def test_environment_override_controls_platform_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = tmp_path / "selected-state"
    monkeypatch.setenv("GETH_AI_DATA_DIR", str(selected))

    assert platform_data_dir() == selected
