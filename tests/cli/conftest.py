"""Isolated helpers for owner-facing CLI acceptance tests."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from geth_ai.cli import app


@dataclass(frozen=True)
class CliHarness:
    data_dir: Path
    workspace: Path
    runner: CliRunner

    def invoke(self, *arguments: str) -> Result:
        return self.runner.invoke(
            app,
            [
                "--data-dir",
                str(self.data_dir),
                "--workspace",
                str(self.workspace),
                *arguments,
            ],
            env={"NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "120"},
        )

    @staticmethod
    def field(result: Result, label: str) -> str:
        match = re.search(rf"^{re.escape(label)}: (.+)$", result.stdout, re.MULTILINE)
        if match is None:
            raise AssertionError(f"missing {label!r} in output:\n{result.stdout}")
        return match.group(1).strip()

    def sandbox_snapshot(self) -> dict[str, bytes]:
        root = self.data_dir / "sandbox"
        if not root.exists():
            return {}
        return {
            str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }


@pytest.fixture
def cli_harness(tmp_path: Path) -> CliHarness:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return CliHarness(
        data_dir=tmp_path / "runtime",
        workspace=workspace,
        runner=CliRunner(),
    )
