"""Guard rail: nothing under ``arbiter/`` may import from ``tests/``.

Production code must never depend on test modules, fixtures or mocks wired
through the test tree. This scans every production module's AST, including
imports nested inside functions, so a late local import cannot slip through.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ARBITER_ROOT = PROJECT_ROOT / "arbiter"


def _is_tests_module(module: str) -> bool:
    return module == "tests" or module.startswith("tests.")


def scan_source(source: str, filename: str = "<string>") -> List[Tuple[int, str]]:
    """Return ``(lineno, statement)`` for every import of the ``tests`` package."""
    tree = ast.parse(source, filename=filename)
    found: List[Tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_tests_module(alias.name):
                    found.append((node.lineno, f"import {alias.name}"))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level == 0 and _is_tests_module(module):
                found.append((node.lineno, f"from {module} import ..."))
    return found


def test_scanner_detects_plain_and_nested_imports() -> None:
    source = (
        "from tests.branch2.test_decision_ladder import _scenario_twin\n"
        "import tests.e2e\n"
        "def inner() -> None:\n"
        "    from tests.branch2 import x\n"
    )
    hits = scan_source(source)
    assert len(hits) == 3
    assert [line for line, _ in hits] == [1, 2, 4]


def test_scanner_ignores_unrelated_imports() -> None:
    source = (
        "import tests_helpers\n"
        "from arbiter.engine import ladder\n"
        "from . import sibling\n"
    )
    assert scan_source(source) == []


def test_no_production_module_imports_tests() -> None:
    offenders: dict[str, List[Tuple[int, str]]] = {}
    for path in sorted(ARBITER_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        hits = scan_source(source, filename=str(path))
        if hits:
            offenders[str(path.relative_to(PROJECT_ROOT))] = hits

    assert offenders == {}, (
        "production modules must not import from tests/: "
        f"{offenders}"
    )
