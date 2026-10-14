"""A narrow static guard against the fail-open patterns the R0 review found (ADR-0025).

This is a supplement, not the acceptance criterion. The seam contract tests prove
behaviourally that each consumer refuses missing facts. This scan only stops the
specific shapes found in the review from coming back:

1. invented identity placeholders (``exp_unknown``, ``arm_placeholder``, ``*_default``);
2. a missing gate disposition defaulted, or defaulted to ``PASSED``;
3. an ISRC generated anywhere except the persisted per-recording allocator;
4. mock media bytes anywhere except the mock client.

A legitimate exception goes in ``ALLOWED`` with its reason. Keep that list short.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME = [REPO_ROOT / "services", REPO_ROOT / "packages"]

PLACEHOLDER = re.compile(
    r"^(exp|fam|arm|gen|dir|pkg|rm|art|spec)_(unknown|default|placeholder)$|^arm_placeholder$"
)

ALLOWED: dict[str, str] = {
    # path (posix, repo-relative) -> reason
    "packages/plm_contracts/seams/lineage.py": "defines the placeholders it refuses",
}


def _runtime_files() -> Iterator[Path]:
    for root in RUNTIME:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            yield path


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _string_constants(tree: ast.AST) -> Iterator[ast.Constant]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node


def test_no_invented_identity_placeholders() -> None:
    offenders = [
        f"{_rel(path)}:{node.lineno} {node.value!r}"
        for path in _runtime_files()
        if _rel(path) not in ALLOWED
        for node in _string_constants(ast.parse(path.read_text(encoding="utf-8")))
        if PLACEHOLDER.match(node.value)
    ]
    assert offenders == [], "an identifier must come from the seam, never be invented"


def _defaulted_gets(tree: ast.AST) -> Iterator[tuple[int, str, object]]:
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and len(node.args) == 2
            and isinstance(node.args[0], ast.Constant)
        ):
            default = node.args[1]
            value = default.value if isinstance(default, ast.Constant) else ast.unparse(default)
            yield node.lineno, str(node.args[0].value), value


def test_no_defaulted_gate_disposition() -> None:
    offenders = [
        f"{_rel(path)}:{line} .get({key!r}, {default!r})"
        for path in _runtime_files()
        for line, key, default in _defaulted_gets(ast.parse(path.read_text(encoding="utf-8")))
        if key == "disposition" or default == "PASSED"
    ]
    assert offenders == [], "a missing gate disposition must block, not default (R8)"


def test_isrcs_come_only_from_the_allocator() -> None:
    allowed = {
        "services/distribution/identifiers/allocator.py",
        "services/distribution/identifiers/isrc.py",
        "services/distribution/domain/ids.py",
    }
    offenders = []
    for path in _runtime_files():
        if _rel(path) in allowed:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if name in {"generate_deterministic_isrc", "preserve_or_allocate_isrc"}:
                    offenders.append(f"{_rel(path)}:{node.lineno} {name}()")
            if isinstance(node, ast.JoinedStr) and ast.unparse(node).startswith("f'USPLM"):
                offenders.append(f"{_rel(path)}:{node.lineno} {ast.unparse(node)}")
    assert offenders == [], "an ISRC is allocated once per recording, never generated ad hoc"


def test_mock_media_bytes_live_only_in_the_mock_client() -> None:
    offenders = []
    for path in _runtime_files():
        if path.name == "mock.py" and "client" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, bytes)
                and node.value.startswith(b"MOCK_")
            ):
                offenders.append(f"{_rel(path)}:{node.lineno}")
    assert offenders == [], "placeholder media must never reach a real upload path"


def test_the_guard_detects_what_it_claims_to() -> None:
    """Guards against a vacuous pass: each detector fires on the pattern it targets."""
    assert PLACEHOLDER.match("arm_unknown") and PLACEHOLDER.match("exp_default")
    assert not PLACEHOLDER.match("arm_test_01")
    sample = ast.parse('m.get("disposition", "PASSED")\nx.get("title", "PASSED")\nx.get("a", 1)')
    hits = [(key, default) for _, key, default in _defaulted_gets(sample)]
    assert ("disposition", "PASSED") in hits and ("title", "PASSED") in hits
    assert len([h for h in hits if h[0] == "disposition" or h[1] == "PASSED"]) == 2
