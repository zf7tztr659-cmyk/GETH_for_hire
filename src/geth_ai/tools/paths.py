"""Directory-descriptor-relative, no-follow filesystem primitives."""

from __future__ import annotations

import errno
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from secrets import token_hex

from geth_ai.policy.redaction import is_sensitive_path


class PathViolation(PermissionError):
    pass


class SensitivePathViolation(PathViolation):
    pass


class NonRegularFileViolation(PathViolation):
    pass


class FileLimitExceeded(PathViolation):
    pass


class UncertainFilesystemOutcome(OSError):
    """The final effect may exist and must be reconciled by digest."""


@dataclass(frozen=True, slots=True)
class ValidatedRelativePath:
    text: str
    parts: tuple[str, ...]


def validate_relative_path(value: str, *, allow_root: bool = False) -> ValidatedRelativePath:
    if not isinstance(value, str) or not value:
        raise PathViolation("path must be a non-empty string")
    if "\x00" in value:
        raise PathViolation("path contains NUL")
    if "\\" in value:
        raise PathViolation("alternate path separators are not allowed")
    if value.startswith("/") or os.path.isabs(value):
        raise PathViolation("absolute paths are not allowed")
    if value == ".":
        if allow_root:
            return ValidatedRelativePath(".", ())
        raise PathViolation("root path is not valid for this operation")
    parts = tuple(value.split("/"))
    if any(part in {"", ".", ".."} for part in parts):
        raise PathViolation("path contains empty, dot, or parent components")
    if any("/" in part for part in parts):
        raise PathViolation("path is not normalized")
    normalized = "/".join(parts)
    if normalized != value:
        raise PathViolation("path is not canonically normalized")
    if is_sensitive_path(normalized):
        raise SensitivePathViolation("sensitive path access is denied")
    return ValidatedRelativePath(normalized, parts)


def open_root_fd(root: str | Path) -> int:
    path = Path(root)
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise PathViolation("configured root is unavailable") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
        raise PathViolation("configured root must be a real directory")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise PathViolation("configured root cannot be opened safely") from exc
    after = os.fstat(descriptor)
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        os.close(descriptor)
        raise PathViolation("configured root identity changed")
    return descriptor


def open_directory_fd(root_fd: int, parts: tuple[str, ...]) -> int:
    current = os.dup(root_fd)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        for component in parts:
            try:
                child = os.open(component, flags, dir_fd=current)
            except OSError as exc:
                raise PathViolation(f"directory component {component!r} is unsafe") from exc
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


def open_regular_file_fd(root_fd: int, parts: tuple[str, ...]) -> int:
    if not parts:
        raise PathViolation("a file path is required")
    parent = open_directory_fd(root_fd, parts[:-1])
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        try:
            descriptor = os.open(parts[-1], flags, dir_fd=parent)
        except OSError as exc:
            raise PathViolation("file cannot be opened without following links") from exc
    finally:
        os.close(parent)
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode):
        os.close(descriptor)
        raise NonRegularFileViolation("only regular files may be read")
    if info.st_nlink != 1:
        os.close(descriptor)
        raise NonRegularFileViolation("multiply-linked files are not readable")
    return descriptor


def atomic_write_new(
    *,
    root_fd: int,
    parts: tuple[str, ...],
    content: bytes,
    precommit_check: Callable[[], None] | None = None,
) -> None:
    if not parts:
        raise PathViolation("a target file path is required")
    parent = open_directory_fd(root_fd, parts[:-1])
    target = parts[-1]
    temp_name = f".geth-tmp-{token_hex(16)}"
    temp_created = False
    installed = False
    try:
        _require_absent(parent, target)
        parent_identity = _identity(parent)
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
        )
        temp_fd = os.open(temp_name, flags, 0o600, dir_fd=parent)
        temp_created = True
        try:
            _write_all(temp_fd, content)
            os.fsync(temp_fd)
            info = os.fstat(temp_fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise PathViolation("temporary target is not a private regular file")
        finally:
            os.close(temp_fd)
        if precommit_check is not None:
            precommit_check()
        if _identity(parent) != parent_identity:
            raise PathViolation("target parent identity changed")
        _require_absent(parent, target)
        try:
            os.link(
                temp_name,
                target,
                src_dir_fd=parent,
                dst_dir_fd=parent,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise PathViolation("target appeared before commit") from exc
        except OSError as exc:
            if exc.errno in {errno.EPERM, errno.EOPNOTSUPP, errno.ENOTSUP}:
                raise PathViolation("platform lacks safe no-overwrite installation") from exc
            raise
        installed = True
        os.fsync(parent)
    except BaseException as exc:
        if installed and not isinstance(exc, UncertainFilesystemOutcome):
            raise UncertainFilesystemOutcome(
                "write may have committed; reconcile the exact target digest"
            ) from exc
        raise
    finally:
        if temp_created:
            try:
                os.unlink(temp_name, dir_fd=parent)
            except FileNotFoundError:
                pass
            except OSError:
                if installed:
                    # The approved target is valid; a leftover temp requires health cleanup.
                    pass
        os.close(parent)


def _require_absent(parent_fd: int, name: str) -> None:
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise PathViolation("target precondition cannot be established") from exc
    raise PathViolation("target must be absent; overwrite is forbidden")


def _identity(descriptor: int) -> tuple[int, int]:
    info = os.fstat(descriptor)
    return info.st_dev, info.st_ino


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short write")
        view = view[written:]
