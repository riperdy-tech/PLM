"""Runtime code never imports test code.

``packages/`` and ``services/`` ship; ``tests/`` does not. A runtime import from
``tests`` works in an editable checkout and fails in any real install.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_TREES = ("packages", "services")


def _imports_tests(tree: ast.AST) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            if node.level == 0 and (node.module == "tests" or node.module.startswith("tests.")):
                lines.append(node.lineno)
        elif isinstance(node, ast.Import) and any(
            a.name == "tests" or a.name.startswith("tests.") for a in node.names
        ):
            lines.append(node.lineno)
    return lines


def test_no_runtime_module_imports_from_tests() -> None:
    offenders = [
        f"{path.relative_to(REPO_ROOT)}:{line}"
        for tree in RUNTIME_TREES
        for path in sorted((REPO_ROOT / tree).rglob("*.py"))
        for line in _imports_tests(ast.parse(path.read_text(encoding="utf-8")))
    ]
    assert offenders == []
