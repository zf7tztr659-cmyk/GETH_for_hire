from __future__ import annotations

import os
import socket
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from geth_ai.application.clock import FrozenClock
from geth_ai.config import Settings
from geth_ai.domain.enums import PrincipalKind
from geth_ai.domain.ids import PrincipalId
from geth_ai.domain.models import Principal


@pytest.fixture(autouse=True)
def offline_and_credential_free(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make accidental network or inherited credential use a hard test failure."""

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network access is forbidden in the Geth test suite")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    for name in tuple(os.environ):
        folded = name.casefold()
        if any(token in folded for token in ("api_key", "access_token", "secret_key")):
            monkeypatch.delenv(name, raising=False)


@pytest.fixture
def frozen_clock() -> FrozenClock:
    return FrozenClock(datetime(2026, 1, 1, 12, 0, tzinfo=UTC))


@pytest.fixture
def owner(frozen_clock: FrozenClock) -> Principal:
    return Principal(
        principal_id=PrincipalId(UUID("00000000-0000-0000-0000-000000000001")),
        kind=PrincipalKind.OWNER,
        display_name="Local human owner",
        created_at=frozen_clock.now(),
    )


@pytest.fixture
def isolated_settings(tmp_path: Path) -> Settings:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    settings = Settings.from_environment(
        data_dir=tmp_path / "app-data",
        workspace_root=workspace,
    )
    settings.ensure_directories()
    return settings
