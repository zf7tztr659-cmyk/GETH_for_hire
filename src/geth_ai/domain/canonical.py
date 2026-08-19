"""Small, deterministic JSON encoding used for security bindings."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence, Set
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import PurePath
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class CanonicalizationError(ValueError):
    """Raised when a value cannot be represented without ambiguity."""


def _datetime_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CanonicalizationError("naive datetime is not canonical")
    utc = value.astimezone(UTC)
    return utc.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _normalize(value: Any, *, stack: set[int], depth: int) -> Any:
    if depth > 64:
        raise CanonicalizationError("maximum canonicalization depth exceeded")
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        raise CanonicalizationError("floating-point values are not canonical")
    if isinstance(value, Enum):
        return _normalize(value.value, stack=stack, depth=depth + 1)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return _datetime_text(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, PurePath):
        return str(value)
    if isinstance(value, BaseModel):
        return _normalize(
            value.model_dump(mode="python"), stack=stack, depth=depth + 1
        )
    if isinstance(value, bytes):
        raise CanonicalizationError("raw bytes must be represented by a digest")

    identity = id(value)
    if isinstance(value, Mapping):
        if identity in stack:
            raise CanonicalizationError("cyclic mapping is not canonical")
        stack.add(identity)
        try:
            normalized: dict[str, Any] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise CanonicalizationError("canonical JSON keys must be strings")
                normalized[key] = _normalize(item, stack=stack, depth=depth + 1)
            return normalized
        finally:
            stack.remove(identity)

    if isinstance(value, Set) and not isinstance(value, (str, bytes, bytearray)):
        if identity in stack:
            raise CanonicalizationError("cyclic set is not canonical")
        stack.add(identity)
        try:
            items = [_normalize(item, stack=stack, depth=depth + 1) for item in value]
            return sorted(items, key=lambda item: canonical_json_bytes(item))
        finally:
            stack.remove(identity)

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if identity in stack:
            raise CanonicalizationError("cyclic sequence is not canonical")
        stack.add(identity)
        try:
            return [_normalize(item, stack=stack, depth=depth + 1) for item in value]
        finally:
            stack.remove(identity)

    raise CanonicalizationError(f"unsupported canonical type: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize supported values with one stable UTF-8 representation."""

    normalized = _normalize(value, stack=set(), depth=0)
    try:
        text = json.dumps(
            normalized,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise CanonicalizationError(str(exc)) from exc
    return text.encode("utf-8")


def canonical_json_text(value: Any) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def require_canonical_json(text: str) -> Any:
    """Parse JSON and reject alternate spellings or non-canonical structure."""

    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CanonicalizationError("invalid JSON") from exc
    if canonical_json_text(value) != text:
        raise CanonicalizationError("JSON text is not canonical")
    return value


__all__ = [
    "CanonicalizationError",
    "canonical_json_bytes",
    "canonical_json_text",
    "canonical_sha256",
    "require_canonical_json",
]
