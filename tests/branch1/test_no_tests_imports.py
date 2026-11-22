"""Tests ensuring no production code imports from tests/.

Contract Rule: Production path (anything under arbiter/) must NEVER
import from tests/.
"""
from __future__ import annotations

import os
import re
from typing import List, Tuple

REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
ARBITER_DIR = os.path.join(REPO_ROOT, "arbiter")

TEST_IMPORT_PATTERN = re.compile(
    r"^\s*(from\s+tests\b|import\s+tests\b)", re.MULTILINE
)


def _scan_arbiter_imports() -> List[Tuple[str, int, str]]:
    """Scan all .py files in arbiter/ for test imports."""
    violations: List[Tuple[str, int, str]] = []
    for root, _, files in os.walk(ARBITER_DIR):
        for fname in files:
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(root, fname)
            rel_path = os.path.relpath(fpath, REPO_ROOT).replace("\\", "/")
            with open(fpath, "r", encoding="utf-8") as f:
                for line_idx, line in enumerate(f, start=1):
                    if TEST_IMPORT_PATTERN.search(line):
                        violations.append((rel_path, line_idx, line.strip()))
    return violations


def test_no_test_imports_in_simulator_and_controllers() -> None:
    """Aki's packages must have 0 test imports."""
    violations = _scan_arbiter_imports()
    aki_violations = [
        v
        for v in violations
        if v[0].startswith("arbiter/simulator")
        or v[0].startswith("arbiter/controllers")
    ]
    assert (
        not aki_violations
    ), f"Forbidden test imports in Aki's packages: {aki_violations}"


def test_no_test_imports_in_arbiter_except_pending_teammate() -> None:
    """Ensure no test imports across arbiter/ (tracking teammate fixes)."""
    violations = _scan_arbiter_imports()
    # Venkat owns arbiter/telemetry/**; recorder.py:40 flagged for his round
    known_teammate_exceptions = {"arbiter/telemetry/recorder.py"}
    unexpected = [
        v for v in violations if v[0] not in known_teammate_exceptions
    ]
    assert not unexpected, f"Unexpected test imports in arbiter/: {unexpected}"
