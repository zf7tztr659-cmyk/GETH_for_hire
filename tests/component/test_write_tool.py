from __future__ import annotations

from pathlib import Path

import pytest

from geth_ai.tools import PathViolation, SandboxWriteTextTool, WriteTextInput


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


@pytest.mark.requirement("SAF-009")
def test_write_creates_exactly_one_exact_file_and_never_overwrites(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    tool = SandboxWriteTextTool(root)
    output = tool.execute(WriteTextInput(path="demo.txt", content="hello\n"))

    assert output.path == "demo.txt"
    assert _snapshot(root) == {"demo.txt": b"hello\n"}
    with pytest.raises(PathViolation):
        tool.execute(WriteTextInput(path="demo.txt", content="changed"))
    assert _snapshot(root) == {"demo.txt": b"hello\n"}


@pytest.mark.requirement("SAF-009")
def test_failed_precommit_check_has_zero_effect(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    tool = SandboxWriteTextTool(root)

    def cancelled() -> None:
        raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        tool.execute(
            WriteTextInput(path="demo.txt", content="hello"),
            precommit_check=cancelled,
        )
    assert _snapshot(root) == {}
