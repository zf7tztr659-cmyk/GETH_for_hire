from __future__ import annotations

import os
from pathlib import Path

import pytest

from geth_ai.tools import FilesystemReadTool, PathViolation, ReadFileInput, validate_relative_path


@pytest.mark.requirement("SAF-008")
@pytest.mark.parametrize(
    "value",
    ("", "/etc/passwd", "../outside", "a/../outside", "a//b", "a/./b", "a\\b", "a\x00b"),
)
def test_ambiguous_or_escaping_paths_are_rejected(value: str) -> None:
    with pytest.raises(PathViolation):
        validate_relative_path(value)


@pytest.mark.requirement("SAF-008")
def test_symlinked_parent_and_leaf_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("outside", encoding="utf-8")
    os.symlink(outside, root / "linked-parent")
    os.symlink(outside / "secret.txt", root / "linked-file")
    tool = FilesystemReadTool(root)

    for path in ("linked-parent/secret.txt", "linked-file"):
        with pytest.raises(PathViolation):
            tool.execute(ReadFileInput(path=path, max_bytes=100))
