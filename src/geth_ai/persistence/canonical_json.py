"""Stable, sink-safe JSON encoding for durable hashes and records."""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import math
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID

type JsonValue = None | bool | int | str | list[JsonValue] | dict[str, JsonValue]
type Redactor = Callable[[Any], Any]


class CanonicalizationError(ValueError):
    """Raised when a value cannot be represented without ambiguity."""


def default_redactor(value: Any) -> Any:
    """Use the central policy redactor without importing it at module import time."""

    from geth_ai.policy.redaction import redact

    return redact(value)


def _datetime_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise CanonicalizationError("naive datetimes are not canonical")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def to_jsonable(value: Any) -> JsonValue:
    """Convert supported values to a deterministic JSON-compatible tree.

    Floats are rejected because NaN/Infinity and cross-runtime formatting make them
    unsuitable for authorization or audit digests. Raw bytes are represented only
    by length and digest so the audit layer cannot accidentally persist content.
    """

    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalizationError("non-finite floats are not canonical")
        raise CanonicalizationError("floats must be normalized to integer or string")
    if isinstance(value, datetime):
        return _datetime_text(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (UUID, Path)):
        return str(value)
    if isinstance(value, Enum):
        return to_jsonable(value.value)
    if isinstance(value, bytes):
        return {
            "$bytes_sha256": hashlib.sha256(value).hexdigest(),
            "$length": len(value),
        }
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return to_jsonable(dataclasses.asdict(value))
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return to_jsonable(model_dump(mode="json"))
    if isinstance(value, Mapping):
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalizationError("canonical JSON object keys must be strings")
            result[key] = to_jsonable(item)
        return result
    if isinstance(value, (set, frozenset)):
        encoded = [to_jsonable(item) for item in value]
        return sorted(encoded, key=canonical_dumps)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [to_jsonable(item) for item in value]
    raise CanonicalizationError(f"unsupported canonical value: {type(value).__name__}")


def canonical_dumps(value: Any) -> str:
    return json.dumps(
        to_jsonable(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_bytes(value: Any) -> bytes:
    return canonical_dumps(value).encode("utf-8")


def redact_and_canonicalize(value: Any, redactor: Redactor | None = None) -> str:
    cleaned = (redactor or default_redactor)(to_jsonable(value))
    return canonical_dumps(cleaned)


def sha256_hex(value: bytes | str) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def encode_opaque_bytes(value: bytes) -> str:
    """Encode non-sensitive internal bytes when JSON storage is explicitly needed."""

    return base64.urlsafe_b64encode(value).decode("ascii")
