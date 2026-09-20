"""Runnable Temporal worker (W0.7): ``python -m plm_platform.worker``.

The unit of deployment is the task queue: each phase ships its workflows and
activities as a :class:`WorkerSet` for its own queue, and one worker process may
serve several queues (the default is all nine, in pipeline order).

Usage::

    python -m plm_platform.worker                          # serve every phase queue
    python -m plm_platform.worker --task-queue ph4.music \
        --load services.music_factory.worker:REGISTRATIONS
    python -m plm_platform.worker --healthcheck --task-queue ph4.music

``--load module:attribute`` resolves a :class:`WorkerSet` (or a sequence of
them) at import time; a set is skipped, with a warning, when this process does
not serve its queue. Every worker also registers the
:class:`~plm_platform.heartbeat.PlatformHeartbeat` workflow, so a fresh checkout
can start a worker before any phase exists (Temporal requires at least one
registration).

``--healthcheck`` exits 0 only when it can reach Temporal *and* every selected
queue has a live poller — process liveness alone is not health. Exit codes: 0
served until a clean shutdown (or healthcheck ok), 1 runtime/healthcheck
failure, 2 configuration or registration error.

Shutdown is graceful: SIGINT/SIGTERM (POSIX) set a stop event and each worker is
shut down explicitly, so in-flight workflow and activity tasks drain before the
process exits.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import logging
import signal
import sys
from collections.abc import Callable, Coroutine, Sequence
from typing import Any, Final

from temporalio.worker import Worker

from .config import Settings, SettingsError, load_settings
from .heartbeat import PlatformHeartbeat
from .temporal import (
    DEFAULT_NAMESPACE,
    Phase,
    TaskQueue,
    WorkerSet,
    all_task_queues,
    connect_client,
    create_worker,
    queue_is_polling,
    task_queue,
)

__all__ = [
    "BUILTIN_WORKFLOWS",
    "HEALTHCHECK_TIMEOUT_SECONDS",
    "WorkerSet",
    "build_parser",
    "load_worker_sets",
    "main",
    "parse_task_queue",
    "registrations_for",
]

logger = logging.getLogger(__name__)

HEALTHCHECK_TIMEOUT_SECONDS: Final[float] = 8.0
"""Hard cap for ``--healthcheck``; keep below the compose healthcheck timeout."""

BUILTIN_WORKFLOWS: Final[tuple[type, ...]] = (PlatformHeartbeat,)
"""Platform workflows every worker registers (see the module docstring)."""


def parse_task_queue(value: str) -> TaskQueue:
    """Accept a queue value (``ph4.music``) or a bare phase token (``ph4``)."""
    try:
        return TaskQueue(value)
    except ValueError:
        pass
    try:
        return task_queue(Phase(value))
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"unknown task queue {value!r}: expected one of "
            f"{', '.join(queue.value for queue in all_task_queues())} "
            "(or a phase token like 'ph4')"
        ) from None


def load_worker_sets(spec: str) -> tuple[WorkerSet, ...]:
    """Resolve a ``module:attribute`` spec to the :class:`WorkerSet`s it names."""
    module_name, separator, attribute_name = spec.partition(":")
    if not separator or not module_name or not attribute_name:
        raise ValueError(f"invalid registration spec {spec!r}: expected 'module:attribute'")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ValueError(f"cannot import registration module {module_name!r}: {exc}") from exc
    try:
        attribute = getattr(module, attribute_name)
    except AttributeError:
        raise ValueError(f"module {module_name!r} has no attribute {attribute_name!r}") from None
    if isinstance(attribute, WorkerSet):
        return (attribute,)
    if isinstance(attribute, (list, tuple)) and all(
        isinstance(item, WorkerSet) for item in attribute
    ):
        return tuple(attribute)
    raise ValueError(
        f"{module_name}:{attribute_name} is not a WorkerSet or a sequence of WorkerSet"
    )


def registrations_for(
    queue: TaskQueue,
    sets: Sequence[WorkerSet],
) -> tuple[tuple[type, ...], tuple[Callable[..., Any], ...]]:
    """Merge the builtins with every set registered for ``queue`` (no duplicates)."""
    workflows: list[type] = list(BUILTIN_WORKFLOWS)
    activities: list[Callable[..., Any]] = []
    for worker_set in sets:
        if worker_set.task_queue is not queue:
            continue
        workflows.extend(worker_set.workflows)
        activities.extend(worker_set.activities)
    return _unique(workflows), _unique(activities)


def _unique[T](items: Sequence[T]) -> tuple[T, ...]:
    seen: set[int] = set()
    unique: list[T] = []
    for item in items:
        if id(item) in seen:
            continue
        seen.add(id(item))
        unique.append(item)
    return tuple(unique)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m plm_platform.worker",
        description="Run a Temporal worker for one or more phase task queues.",
    )
    parser.add_argument(
        "--task-queue",
        action="append",
        type=parse_task_queue,
        metavar="QUEUE",
        help="task queue to serve, repeatable (default: every phase queue)",
    )
    parser.add_argument(
        "--load",
        action="append",
        default=[],
        metavar="MODULE:ATTRIBUTE",
        help="registration set to load, repeatable (see packages/plm_platform/README.md)",
    )
    parser.add_argument(
        "--namespace",
        default=DEFAULT_NAMESPACE,
        help=f"Temporal namespace (default: {DEFAULT_NAMESPACE})",
    )
    parser.add_argument(
        "--identity",
        default=None,
        help="worker identity visible to DescribeTaskQueue (default: host:pid)",
    )
    parser.add_argument(
        "--healthcheck",
        action="store_true",
        help="verify that every selected queue has a live poller, then exit",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
    )
    try:
        settings = load_settings()
    except SettingsError as exc:
        logger.error("worker cannot start: %s", exc)
        return 2
    logging.getLogger().setLevel(settings.log_level.value)

    try:
        sets = tuple(
            worker_set for spec in args.load for worker_set in load_worker_sets(spec)
        )
    except ValueError as exc:
        logger.error("worker registration failed: %s", exc)
        return 2

    queues = tuple(args.task_queue) if args.task_queue else all_task_queues()
    for worker_set in sets:
        if worker_set.task_queue not in queues:
            logger.warning(
                "registration for %s ignored: this process serves %s",
                worker_set.task_queue.value,
                ", ".join(queue.value for queue in queues),
            )

    if args.healthcheck:
        return _run(_healthcheck(settings, queues, namespace=args.namespace))
    return _run(
        _serve_and_report(
            settings, queues, sets, namespace=args.namespace, identity=args.identity
        )
    )


async def _serve_and_report(
    settings: Settings,
    queues: Sequence[TaskQueue],
    sets: Sequence[WorkerSet],
    *,
    namespace: str,
    identity: str | None,
) -> int:
    await _serve(settings, queues, sets, namespace=namespace, identity=identity)
    return 0


async def _serve(
    settings: Settings,
    queues: Sequence[TaskQueue],
    sets: Sequence[WorkerSet],
    *,
    namespace: str,
    identity: str | None,
) -> None:
    client = await connect_client(settings, namespace=namespace)
    workers = []
    for queue in queues:
        workflows, activities = registrations_for(queue, sets)
        options: dict[str, Any] = {} if identity is None else {"identity": identity}
        workers.append(
            create_worker(
                client,
                queue,
                workflows=workflows,
                activities=activities,
                **options,
            )
        )
        logger.info(
            "worker registered: queue=%s workflows=%d activities=%d",
            queue.value,
            len(workflows),
            len(activities),
        )
    await _run_until_stopped(workers)


async def _run_until_stopped(workers: Sequence[Worker]) -> None:
    """Run every worker; stop on signal, a fatal worker error, or shutdown."""
    stop = asyncio.Event()
    _install_stop_signals(stop)
    run_tasks = [asyncio.create_task(worker.run()) for worker in workers]
    stop_task = asyncio.create_task(stop.wait())
    try:
        done, _ = await asyncio.wait(
            [*run_tasks, stop_task], return_when=asyncio.FIRST_COMPLETED
        )
        for task in done:
            if task is stop_task or task.cancelled():
                continue
            error = task.exception()
            if error is not None:
                raise error
            logger.warning("a worker exited without a shutdown request")
    finally:
        stop_task.cancel()
        await asyncio.gather(stop_task, return_exceptions=True)
        outcomes = await asyncio.gather(
            *(worker.shutdown() for worker in workers), return_exceptions=True
        )
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                logger.error("worker shutdown failed: %s", outcome)
        await asyncio.gather(*run_tasks, return_exceptions=True)
        logger.info("worker shutdown complete")


def _install_stop_signals(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError, ValueError):
            # Windows proactor loop: Ctrl+C arrives as KeyboardInterrupt instead
            # (handled in _run); SIGTERM is not installable there.
            logger.debug("signal %s left to the runtime default", sig.name)


async def _healthcheck(
    settings: Settings,
    queues: Sequence[TaskQueue],
    *,
    namespace: str,
) -> int:
    try:
        async with asyncio.timeout(HEALTHCHECK_TIMEOUT_SECONDS):
            client = await connect_client(settings, namespace=namespace)
            silent = [
                queue.value
                for queue in queues
                if not await queue_is_polling(client, queue, namespace=namespace)
            ]
    except TimeoutError:
        logger.error(
            "worker healthcheck failed: Temporal at %s did not answer within %.0fs",
            settings.temporal_host,
            HEALTHCHECK_TIMEOUT_SECONDS,
        )
        return 1
    if silent:
        logger.error(
            "worker healthcheck failed: no pollers for task queue(s) %s",
            ", ".join(silent),
        )
        return 1
    logger.info(
        "worker healthcheck ok: pollers present for %s",
        ", ".join(queue.value for queue in queues),
    )
    return 0


def _run(coro: Coroutine[Any, Any, int]) -> int:
    try:
        return asyncio.run(coro)
    except KeyboardInterrupt:
        # Windows: Ctrl+C cancels the main task, which shuts the workers down
        # gracefully before asyncio.run re-raises the interrupt here.
        logger.info("worker interrupted; shutdown complete")
        return 0
    except Exception:
        logger.exception("worker stopped with an error")
        return 1


if __name__ == "__main__":
    sys.exit(main())
