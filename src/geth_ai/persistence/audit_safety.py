"""Event-specific minimization before values enter the immutable journal.

The generic redactor is defense in depth. These functions enforce stronger,
schema-aware guarantees: approval events never carry executable arguments, and
tool-result events contain metadata rather than file content.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import PurePath
from typing import Any

from geth_ai.policy.redaction import REDACTION_MARKER, is_sensitive_path, redact

from .canonical_json import canonical_dumps, sha256_hex, to_jsonable

OMITTED_ERROR_SUMMARY = "tool error details omitted at audit boundary"

_APPROVAL_EVENT_FIELDS = (
    "approval_id",
    "work_item_id",
    "requester_id",
    "owner_id",
    "action_digest",
    "policy_version",
    "expires_at",
    "nonce",
)
_ACTION_METADATA_FIELDS = (
    "schema_version",
    "normalization_version",
    "tool_name",
    "tool_schema_version",
    "policy_version",
    "requester_id",
    "owner_id",
    "run_id",
    "work_item_id",
    "risk_class",
    "root",
    "target",
    "relative_path",
    "arguments_sha256",
    "expected_prior_state",
    "overwrite",
    "content_sha256",
    "content_length",
    "budget",
    "created_at",
    "expires_at",
)
_HEX = frozenset("0123456789abcdef")


class UnsafeApprovalAction(ValueError):
    pass


def reject_secret_shaped_action(action: Mapping[str, Any]) -> None:
    """Reject actions that central redaction would alter before exact storage."""

    normalized = to_jsonable(action)
    if not isinstance(normalized, dict):
        raise UnsafeApprovalAction("action must be an object")
    if canonical_dumps(redact(normalized)) != canonical_dumps(normalized):
        raise UnsafeApprovalAction("secret-shaped action arguments are not persistable")

    arguments = _decoded_arguments(normalized)
    if arguments is not None and canonical_dumps(redact(arguments)) != canonical_dumps(
        arguments
    ):
        raise UnsafeApprovalAction("secret-shaped action arguments are not persistable")
    for key in ("root", "target"):
        value = normalized.get(key)
        if isinstance(value, str) and is_sensitive_path(value):
            raise UnsafeApprovalAction("sensitive action paths are not persistable")


def approval_action_metadata(action: Mapping[str, Any] | Any) -> dict[str, Any]:
    """Describe an action without preserving executable/raw arguments."""

    normalized = to_jsonable(action)
    if not isinstance(normalized, dict):
        return {}
    metadata = {
        key: normalized[key]
        for key in _ACTION_METADATA_FIELDS
        if key in normalized
    }
    for key in ("root", "target", "relative_path"):
        value = metadata.get(key)
        if isinstance(value, str):
            metadata[key] = _safe_path(value)
    arguments = _decoded_arguments(normalized)
    if arguments is None:
        arguments = normalized

    path = arguments.get("path")
    if isinstance(path, str):
        metadata["relative_path"] = _safe_path(path)
    content = arguments.get("content")
    if isinstance(content, str):
        encoded = content.encode("utf-8")
        metadata.setdefault("content_sha256", sha256_hex(encoded))
        metadata.setdefault("content_length", len(encoded))
    return metadata


def safe_tool_result(tool_name: str, result: Mapping[str, Any] | Any) -> dict[str, Any]:
    """Retain only adapter postcondition metadata, never returned file content."""

    normalized = to_jsonable(result)
    if not isinstance(normalized, dict):
        return {"status": "result_metadata_omitted"}

    safe: dict[str, Any] = {}
    path = normalized.get("path")
    if isinstance(path, str):
        safe["path"] = _safe_path(path)
    for key in ("sha256", "digest"):
        value = normalized.get(key)
        if _is_sha256(value):
            safe[key] = value
    for key in ("byte_length", "size_bytes"):
        value = normalized.get(key)
        if type(value) is int and value >= 0:
            safe[key] = value
    if type(normalized.get("created")) is bool:
        safe["created"] = normalized["created"]
    status = normalized.get("status")
    if status in {
        "created",
        "failed",
        "ok",
        "success",
        "verified",
    }:
        safe["status"] = status

    raw_entries = normalized.get("entries")
    if tool_name == "fs.list" and isinstance(raw_entries, list):
        entries: list[dict[str, Any]] = []
        for entry in raw_entries:
            if not isinstance(entry, dict):
                continue
            item: dict[str, Any] = {}
            entry_path = entry.get("path")
            if isinstance(entry_path, str):
                item["path"] = _safe_path(entry_path)
            if entry.get("kind") in {"directory", "file"}:
                item["kind"] = entry["kind"]
            length = entry.get("byte_length")
            if type(length) is int and length >= 0:
                item["byte_length"] = length
            entries.append(item)
        safe["entries"] = entries
    return safe


def sanitize_event_payload(
    event_type: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    """Apply a mandatory event schema before generic redaction and hashing."""

    if event_type == "ApprovalRequested":
        approval_payload = {key: payload.get(key) for key in _APPROVAL_EVENT_FIELDS}
        source = payload.get("action_metadata", payload.get("action", {}))
        approval_payload["action_metadata"] = approval_action_metadata(source)
        return approval_payload
    if event_type == "ToolCallStateChanged":
        tool_name = str(payload.get("tool_name", "unknown"))
        result = payload.get("result")
        error = payload.get("error_summary")
        safe_result = None if result is None else safe_tool_result(tool_name, result)
        tool_payload: dict[str, Any] = {
            "call_id": payload.get("call_id"),
            "tool_name": tool_name,
            "state": payload.get("state"),
            "result": safe_result,
            "error_summary": None,
        }
        if error is not None:
            tool_payload["error_summary"] = OMITTED_ERROR_SUMMARY
            existing_digest = payload.get("error_summary_sha256")
            tool_payload["error_summary_sha256"] = (
                existing_digest
                if _is_sha256(existing_digest)
                else sha256_hex(str(error))
            )
        return tool_payload
    return dict(payload)


def _decoded_arguments(action: Mapping[str, Any]) -> dict[str, Any] | None:
    direct = action.get("arguments")
    if isinstance(direct, dict):
        return direct
    text = action.get("arguments_json")
    if isinstance(text, str):
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            return None
        return decoded if isinstance(decoded, dict) else None
    return None


def _safe_path(value: str) -> str:
    if (
        not value
        or len(value) > 4_096
        or "\x00" in value
        or "\n" in value
        or "\r" in value
        or is_sensitive_path(PurePath(value))
    ):
        return REDACTION_MARKER
    return value


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and set(value).issubset(_HEX)
    )


__all__ = [
    "OMITTED_ERROR_SUMMARY",
    "UnsafeApprovalAction",
    "approval_action_metadata",
    "reject_secret_shaped_action",
    "safe_tool_result",
    "sanitize_event_payload",
]
