"""Static guard: runtime code below the edge never reads ambient time or randomness.

AGENTS.md: no wall-clock reads or process-randomised hashing in anything that must
replay. Time arrives through `plm_platform.clock.Clock` or an explicit `now`, seeds
through `stable_seed`. Only edge modules (HTTP routes, CLIs, smoke scripts, workers,
connectors that stamp an external observation) may construct `SystemClock` or read the
clock themselves.

This is a supplement, not the proof. The behavioural proof is
`test_cross_process_determinism.py`.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCANNED = ("packages", "services")

EDGE_PARTS = {"api", "acceptance", "jobs", "dashboard"}
EDGE_FILES = {
    "cli.py",
    "worker.py",
    "heartbeat.py",
}
EDGE_MODULES = {
    # The one place the wall clock is defined.
    "packages/plm_platform/clock.py",
    # Correlation ids identify a request, they are never replayed.
    "packages/plm_platform/correlation.py",
    # Run timing for logs and metrics, not decision inputs.
    "packages/plm_platform/observability.py",
    "services/experiments/observability/logging.py",
    # Connectors stamp when an external fact was observed; the stamp is the input.
    "services/market_intelligence/connectors/youtube.py",
    "services/market_intelligence/connectors/trends.py",
}

AMBIENT_TIME = {("datetime", "now"), ("datetime", "utcnow"), ("datetime", "today"),
                ("date", "today"), ("time", "time"), ("time", "time_ns")}
GLOBAL_RANDOM = {"random", "randint", "randrange", "choice", "choices", "shuffle",
                 "sample", "uniform", "gauss", "seed"}


def _is_edge(relative: Path) -> bool:
    posix = relative.as_posix()
    if posix in EDGE_MODULES or relative.name in EDGE_FILES:
        return True
    return bool(EDGE_PARTS.intersection(relative.parts[:-1]))


def _violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.function_stack: list[str] = []

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.function_stack.append(node.name)
            self.generic_visit(node)
            self.function_stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

        def visit_Call(self, node: ast.Call) -> None:
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                pair = (func.value.id, func.attr)
                if pair in AMBIENT_TIME:
                    found.append(f"{path}:{node.lineno} {pair[0]}.{pair[1]}()")
                if func.value.id == "random" and func.attr in GLOBAL_RANDOM:
                    found.append(f"{path}:{node.lineno} global random.{func.attr}()")
                if func.value.id == "uuid" and func.attr in {"uuid1", "uuid4"}:
                    found.append(f"{path}:{node.lineno} uuid.{func.attr}()")
            if isinstance(func, ast.Name):
                if func.id in {"uuid1", "uuid4"}:
                    found.append(f"{path}:{node.lineno} {func.id}()")
                if func.id == "hash" and "__hash__" not in self.function_stack:
                    found.append(f"{path}:{node.lineno} builtin hash() is per-process")
                if func.id == "SystemClock":
                    found.append(f"{path}:{node.lineno} SystemClock() below the edge")
            self.generic_visit(node)

    Visitor().visit(tree)
    return found


def test_no_ambient_time_or_randomness_below_the_edge() -> None:
    violations: list[str] = []
    for root in SCANNED:
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            relative = path.relative_to(REPO_ROOT)
            if _is_edge(relative):
                continue
            violations.extend(_violations(path))
    assert not violations, "ambient time/randomness in replayable code:\n" + "\n".join(violations)


def test_guard_detects_each_pattern(tmp_path: Path) -> None:
    sample = tmp_path / "sample.py"
    sample.write_text(
        "import random, time, uuid\n"
        "from datetime import datetime\n"
        "a = datetime.now()\n"
        "b = time.time()\n"
        "c = random.random()\n"
        "d = uuid.uuid4()\n"
        "e = hash('x')\n"
        "f = random.Random(3).random()\n"
        "class K:\n"
        "    def __hash__(self):\n"
        "        return hash(1)\n",
        encoding="utf-8",
    )
    found = _violations(sample)
    assert len(found) == 5, found
