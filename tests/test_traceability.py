from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENT_PATTERN = re.compile(r"^\| ([A-Z]+-\d{3}) \|", re.MULTILINE)
MARKER_PATTERN = re.compile(r"pytest\.mark\.requirement\([\"']([A-Z]+-\d{3})[\"']\)")


@pytest.mark.requirement("QLT-002")
def test_every_requirement_has_automated_coverage_marker() -> None:
    requirement_ids = set(
        REQUIREMENT_PATTERN.findall((ROOT / "docs/requirements.md").read_text())
    )
    marked_ids: set[str] = set()
    for test_file in (ROOT / "tests").rglob("test_*.py"):
        marked_ids.update(MARKER_PATTERN.findall(test_file.read_text()))

    assert requirement_ids
    missing = sorted(requirement_ids - marked_ids)
    unknown = sorted(marked_ids - requirement_ids)
    assert not missing, f"requirements without automated coverage markers: {missing}"
    assert not unknown, f"unknown requirement markers: {unknown}"
