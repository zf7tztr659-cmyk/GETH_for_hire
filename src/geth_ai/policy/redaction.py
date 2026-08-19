"""Central recursive redaction used before every observable sink."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence, Set
from pathlib import PurePath
from typing import Any

from pydantic import BaseModel

REDACTION_MARKER = "[REDACTED]"

_SENSITIVE_KEY = re.compile(
    r"(?:^|[_-])(?:api[_-]?key|access[_-]?key|secret|client[_-]?secret|"
    r"password|passwd|pwd|token|authorization|auth|credential|cookie|"
    r"private[_-]?key|session)(?:$|[_-])",
    re.IGNORECASE,
)

_TEXT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----.*?"
        r"-----END (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
        re.DOTALL,
    ),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox(?:a|b|p|r|s)-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]+=*", re.IGNORECASE),
    re.compile(
        r"\b[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{8,}\."
        r"[A-Za-z0-9_-]{8,}\b"
    ),
    re.compile(r"(?<=://)[^\s/@:]+:[^\s/@]+(?=@)"),
)

_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?key|secret|client[_-]?secret|password|"
    r"passwd|pwd|token|authorization|credential|private[_-]?key|session)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)

_SENSITIVE_BASENAMES = frozenset(
    {
        ".env",
        ".git-credentials",
        ".netrc",
        ".npmrc",
        ".pypirc",
        "credentials",
        "credentials.json",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "id_rsa",
        "known_hosts",
        "secrets.json",
    }
)
_SENSITIVE_DIRS = frozenset({".aws", ".gnupg", ".ssh"})
_SENSITIVE_SUFFIXES = (".key", ".pem", ".p12", ".pfx", ".jks")


def is_sensitive_key(key: str) -> bool:
    normalized = key.strip().casefold()
    return normalized in {
        "auth",
        "authorization",
        "cookie",
        "password",
        "passwd",
        "pwd",
        "secret",
        "session",
        "token",
    } or _SENSITIVE_KEY.search(normalized) is not None


def is_sensitive_path(path: str | PurePath) -> bool:
    """Conservatively identify known credential locations without opening them."""

    text = str(path).replace("\\", "/")
    parts = tuple(part.casefold() for part in text.split("/") if part not in {"", "."})
    if not parts:
        return False
    basename = parts[-1]
    if basename in _SENSITIVE_BASENAMES or basename.startswith(".env."):
        return True
    if any(part in _SENSITIVE_DIRS for part in parts):
        return True
    if len(parts) >= 2 and parts[-2:] == (".git", "config"):
        return True
    if basename.endswith(_SENSITIVE_SUFFIXES):
        return True
    return "credential" in basename or basename.startswith("secret-")


def redact_text(text: str) -> str:
    redacted = text
    for pattern in _TEXT_PATTERNS:
        redacted = pattern.sub(REDACTION_MARKER, redacted)
    redacted = _ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{REDACTION_MARKER}",
        redacted,
    )
    return redacted


def redact(value: Any) -> Any:
    """Return a recursively redacted, serialization-safe copy.

    Raw bytes are always withheld. Cycles and excessive nesting fail toward a
    marker rather than exposing an object representation or raising an error
    that might itself include the secret.
    """

    return _redact(value, stack=set(), depth=0)


def _redact(value: Any, *, stack: set[int], depth: int) -> Any:
    if depth > 64:
        return REDACTION_MARKER
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return REDACTION_MARKER
    if isinstance(value, BaseException):
        return redact_text(str(value))
    if isinstance(value, BaseModel):
        return _redact(value.model_dump(mode="python"), stack=stack, depth=depth + 1)

    identity = id(value)
    if isinstance(value, Mapping):
        if identity in stack:
            return REDACTION_MARKER
        stack.add(identity)
        try:
            result: dict[str, Any] = {}
            for key, item in value.items():
                safe_key = redact_text(str(key))
                if is_sensitive_key(str(key)) or (
                    str(key).casefold() in {"file", "filename", "locator", "path"}
                    and isinstance(item, (str, PurePath))
                    and is_sensitive_path(item)
                ):
                    result[safe_key] = REDACTION_MARKER
                else:
                    result[safe_key] = _redact(
                        item, stack=stack, depth=depth + 1
                    )
            return result
        finally:
            stack.remove(identity)

    if isinstance(value, Set) and not isinstance(value, (str, bytes, bytearray)):
        if identity in stack:
            return REDACTION_MARKER
        stack.add(identity)
        try:
            return sorted(
                (_redact(item, stack=stack, depth=depth + 1) for item in value),
                key=repr,
            )
        finally:
            stack.remove(identity)

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if identity in stack:
            return REDACTION_MARKER
        stack.add(identity)
        try:
            return [_redact(item, stack=stack, depth=depth + 1) for item in value]
        finally:
            stack.remove(identity)

    return redact_text(str(value))


__all__ = [
    "REDACTION_MARKER",
    "is_sensitive_key",
    "is_sensitive_path",
    "redact",
    "redact_text",
]
