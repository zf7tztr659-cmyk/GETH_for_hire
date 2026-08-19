"""Narrow local filesystem adapters."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from pydantic import BaseModel

from geth_ai.domain.enums import RiskClass
from geth_ai.policy.redaction import is_sensitive_path, redact_text

from .paths import (
    FileLimitExceeded,
    NonRegularFileViolation,
    PathViolation,
    atomic_write_new,
    open_directory_fd,
    open_regular_file_fd,
    open_root_fd,
    validate_relative_path,
)
from .protocol import (
    FileEntry,
    ListDirectoryInput,
    ListDirectoryOutput,
    PrecommitCheck,
    ReadFileInput,
    ReadFileOutput,
    ToolSpec,
    WriteTextInput,
    WriteTextOutput,
)


class FilesystemListTool:
    def __init__(self, root: str | Path, *, max_entries: int = 1_000) -> None:
        self.root = _canonical_root(root)
        self.max_entries = max_entries
        self._spec = ToolSpec(
            name="fs.list",
            schema_version="1",
            input_model=ListDirectoryInput,
            output_model=ListDirectoryOutput,
            risk_class=RiskClass.LOCAL_READ,
            allowed_roots=(str(self.root),),
            timeout_seconds=5,
            call_cost=1,
            idempotent=True,
            reversible=True,
            description="List bounded non-sensitive entries below one configured root.",
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def execute(
        self, value: BaseModel, *, precommit_check: PrecommitCheck | None = None
    ) -> ListDirectoryOutput:
        request = ListDirectoryInput.model_validate(value)
        relative = validate_relative_path(request.path, allow_root=True)
        root_fd = open_root_fd(self.root)
        try:
            directory = open_directory_fd(root_fd, relative.parts)
            try:
                entries: list[FileEntry] = []
                for name in sorted(os.listdir(directory)):
                    child_path = name if relative.text == "." else f"{relative.text}/{name}"
                    if is_sensitive_path(child_path):
                        continue
                    info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode):
                        continue
                    if stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                        kind = "file"
                    elif stat.S_ISDIR(info.st_mode):
                        kind = "directory"
                    else:
                        continue
                    entries.append(
                        FileEntry(path=child_path, kind=kind, byte_length=info.st_size)
                    )
                    if len(entries) > self.max_entries:
                        raise FileLimitExceeded("directory entry budget exceeded")
                return ListDirectoryOutput(entries=tuple(entries))
            finally:
                os.close(directory)
        finally:
            os.close(root_fd)


class FilesystemReadTool:
    def __init__(self, root: str | Path, *, max_bytes: int = 1_048_576) -> None:
        self.root = _canonical_root(root)
        self.max_bytes = max_bytes
        self._spec = ToolSpec(
            name="fs.read",
            schema_version="1",
            input_model=ReadFileInput,
            output_model=ReadFileOutput,
            risk_class=RiskClass.LOCAL_READ,
            allowed_roots=(str(self.root),),
            timeout_seconds=5,
            call_cost=1,
            idempotent=True,
            reversible=True,
            description="Read one bounded UTF-8 regular file without following links.",
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def execute(
        self, value: BaseModel, *, precommit_check: PrecommitCheck | None = None
    ) -> ReadFileOutput:
        request = ReadFileInput.model_validate(value)
        if request.max_bytes > self.max_bytes:
            raise FileLimitExceeded("requested byte limit exceeds tool budget")
        relative = validate_relative_path(request.path)
        root_fd = open_root_fd(self.root)
        try:
            descriptor = open_regular_file_fd(root_fd, relative.parts)
            try:
                info = os.fstat(descriptor)
                if info.st_size > request.max_bytes:
                    raise FileLimitExceeded("file exceeds byte budget")
                chunks: list[bytes] = []
                remaining = request.max_bytes + 1
                while remaining:
                    chunk = os.read(descriptor, min(65_536, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                content = b"".join(chunks)
                if len(content) > request.max_bytes:
                    raise FileLimitExceeded("file grew beyond byte budget")
            finally:
                os.close(descriptor)
        finally:
            os.close(root_fd)
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise NonRegularFileViolation("only UTF-8 text files are readable") from exc
        return ReadFileOutput(
            path=relative.text,
            content=redact_text(text),
            byte_length=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )


class SandboxWriteTextTool:
    def __init__(self, root: str | Path, *, max_bytes: int = 1_048_576) -> None:
        path = Path(root)
        if not path.is_absolute():
            path = path.absolute()
        _reject_symlink_components(path.parent)
        if not path.exists():
            os.mkdir(path, 0o700)
        self.root = _canonical_root(path)
        descriptor = open_root_fd(self.root)
        try:
            os.fchmod(descriptor, 0o700)
        finally:
            os.close(descriptor)
        self.max_bytes = max_bytes
        self._spec = ToolSpec(
            name="sandbox.write_text",
            schema_version="1",
            input_model=WriteTextInput,
            output_model=WriteTextOutput,
            risk_class=RiskClass.REVERSIBLE_WORKSPACE_WRITE,
            allowed_roots=(str(self.root),),
            timeout_seconds=5,
            call_cost=1,
            idempotent=True,
            reversible=True,
            description="Create one exact UTF-8 file in the dedicated sandbox; no overwrite.",
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def execute(
        self, value: BaseModel, *, precommit_check: PrecommitCheck | None = None
    ) -> WriteTextOutput:
        request = WriteTextInput.model_validate(value)
        relative = validate_relative_path(request.path)
        if redact_text(request.content) != request.content:
            raise PathViolation("secret-shaped content cannot be written")
        content = request.content.encode("utf-8")
        if len(content) > self.max_bytes:
            raise FileLimitExceeded("write exceeds byte budget")
        root_fd = open_root_fd(self.root)
        try:
            atomic_write_new(
                root_fd=root_fd,
                parts=relative.parts,
                content=content,
                precommit_check=precommit_check,
            )
        finally:
            os.close(root_fd)
        return WriteTextOutput(
            path=relative.text,
            byte_length=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )


def _canonical_root(root: str | Path) -> Path:
    path = Path(root)
    if not path.is_absolute():
        path = path.absolute()
    _reject_symlink_components(path)
    descriptor = open_root_fd(path)
    os.close(descriptor)
    return path


def _reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            raise PathViolation("configured root parent must already exist") from None
        if stat.S_ISLNK(info.st_mode):
            raise PathViolation("configured root cannot contain symlink components")
