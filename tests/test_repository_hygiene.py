from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.requirement("QLT-003")
def test_prime_directive_pdf_hash_is_unchanged() -> None:
    source = ROOT / "Prime directive-2.pdf"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == (
        "a3b7aaf2563a11daf11c8d98dc127266917c9a88914e3b7783c77047eff13cab"
    )


@pytest.mark.requirement("OPS-005")
def test_repository_has_no_runtime_database_or_secret_artifacts() -> None:
    forbidden_suffixes = (".sqlite3", ".sqlite3-wal", ".sqlite3-shm", ".pem", ".key")
    forbidden = [
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and "src/geth_ai.egg-info" not in path.as_posix()
        and path.name != "Prime directive-2.pdf"
        and (
            path.name.endswith(forbidden_suffixes)
            or path.name in {".env", "credentials.json"}
        )
    ]
    assert forbidden == []
