"""Small structured JSON logger with redaction at the sink boundary."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from typing import TextIO

from geth_ai.policy.redaction import redact


class JsonLogger:
    def __init__(self, *, stream: TextIO | None = None, verbose: bool = False) -> None:
        self._stream = stream or sys.stderr
        self._verbose = verbose

    def emit(self, level: str, event: str, fields: Mapping[str, object] | None = None) -> None:
        if level == "debug" and not self._verbose:
            return
        payload: dict[str, object] = {"level": level, "event": event}
        if fields:
            payload["fields"] = dict(fields)
        safe = redact(payload)
        self._stream.write(
            json.dumps(safe, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
        )
        self._stream.flush()
