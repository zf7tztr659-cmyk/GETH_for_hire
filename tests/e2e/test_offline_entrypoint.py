from __future__ import annotations

import threading
import tomllib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from geth_ai.cli import app

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.requirement("QLT-001")
@pytest.mark.requirement("OPS-002")
def test_declared_console_entrypoint_initializes_offline_without_background_work(
    tmp_path: Path,
) -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert project["project"]["requires-python"] == ">=3.12"
    assert project["project"]["scripts"]["geth-ai"] == "geth_ai.cli:app"

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    threads_before = {thread.ident for thread in threading.enumerate()}
    result = CliRunner().invoke(
        app,
        [
            "--data-dir",
            str(tmp_path / "data"),
            "--workspace",
            str(workspace),
            "init",
        ],
    )
    threads_after = {thread.ident for thread in threading.enumerate()}

    assert result.exit_code == 0, result.output
    assert "Provider: fake (offline)" in result.output
    assert threads_after == threads_before
