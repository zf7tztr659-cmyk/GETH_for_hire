from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from geth_ai.domain.enums import RiskClass
from geth_ai.tools import FilesystemListTool, FilesystemReadTool, SandboxWriteTextTool
from geth_ai.tools.protocol import PrecommitCheck, ToolSpec
from geth_ai.tools.registry import ToolRegistry


class _EmptyModel(BaseModel):
    pass


class _IncompleteTool:
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="broken",
            schema_version="1",
            input_model=_EmptyModel,
            output_model=_EmptyModel,
            risk_class=RiskClass.LOCAL_READ,
            allowed_roots=(),
            timeout_seconds=1,
            call_cost=1,
            idempotent=True,
            reversible=True,
            description="missing roots",
        )

    def execute(
        self, value: BaseModel, *, precommit_check: PrecommitCheck | None = None
    ) -> BaseModel:
        return value


@pytest.mark.requirement("SAF-007")
def test_registry_rejects_incomplete_tools_and_exposes_only_closed_mvp_set(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="allowed root"):
        ToolRegistry((_IncompleteTool(),))

    workspace = tmp_path / "workspace"
    sandbox = tmp_path / "sandbox"
    workspace.mkdir()
    sandbox.mkdir()
    registry = ToolRegistry(
        (
            FilesystemListTool(workspace),
            FilesystemReadTool(workspace),
            FilesystemReadTool(sandbox),
            SandboxWriteTextTool(sandbox),
        )
    )

    assert registry.names() == ("fs.list", "fs.read", "sandbox.write_text")
    assert registry.get("fs.read") is None
    assert registry.require("fs.read", root=str(workspace)).spec.allowed_roots == (
        str(workspace),
    )
    assert registry.require("fs.read", root=str(sandbox)).spec.allowed_roots == (
        str(sandbox),
    )
    assert "shell.run" not in registry.names()
    assert "network.fetch" not in registry.names()
