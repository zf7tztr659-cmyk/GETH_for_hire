"""Durable global emergency-stop latch."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path


class EmergencyStopError(RuntimeError):
    pass


class EmergencyStop:
    def __init__(self, path: Path) -> None:
        self.path = path

    def is_active(self) -> bool:
        try:
            return self.path.exists()
        except OSError as exc:
            raise EmergencyStopError("emergency-stop state is unreadable; failing closed") from exc

    def require_clear(self) -> None:
        if self.is_active():
            raise EmergencyStopError("global emergency stop is active")

    def activate(self, *, actor: str, at: datetime) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temp = self.path.with_name(f".{self.path.name}.tmp")
        payload = {"active": True, "actor": actor, "at": at.isoformat()}
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(temp, flags, 0o600)
        try:
            os.write(descriptor, json.dumps(payload, sort_keys=True).encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temp, self.path)

    def reset(self, *, confirmation: str) -> None:
        if confirmation != "RESET":
            raise EmergencyStopError("reset requires the exact confirmation RESET")
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            raise EmergencyStopError("could not reset emergency stop; failing closed") from exc
