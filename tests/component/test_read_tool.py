from __future__ import annotations

from pathlib import Path

import pytest

from geth_ai.tools import (
    FileLimitExceeded,
    FilesystemListTool,
    FilesystemReadTool,
    ListDirectoryInput,
    ReadFileInput,
    SensitivePathViolation,
)


@pytest.mark.requirement("SAF-003")
def test_bounded_read_returns_redacted_utf8_and_digest(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "note.txt").write_text("token=very-secret-value", encoding="utf-8")
    tool = FilesystemReadTool(root, max_bytes=100)
    output = tool.execute(ReadFileInput(path="note.txt", max_bytes=100))

    assert output.content == "token=[REDACTED]"
    assert output.byte_length == len(b"token=very-secret-value")
    with pytest.raises(FileLimitExceeded):
        tool.execute(ReadFileInput(path="note.txt", max_bytes=3))


@pytest.mark.requirement("PRV-002")
def test_sensitive_paths_are_denied_and_hidden_from_listing(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / ".env").write_text("TOKEN=secret", encoding="utf-8")
    (root / "public.txt").write_text("safe", encoding="utf-8")

    with pytest.raises(SensitivePathViolation):
        FilesystemReadTool(root).execute(ReadFileInput(path=".env", max_bytes=100))
    listing = FilesystemListTool(root).execute(ListDirectoryInput(path="."))
    assert [entry.path for entry in listing.entries] == ["public.txt"]
