"""Temporal client/worker connectors and platform-wide workflow conventions (W0.7).

Temporal is the durable workflow engine (architecture PART XIX). Phase code works
through this module and the conventions below — never through string literals or
ad-hoc retry objects at call sites:

* **Task-queue names are defined once, here.** :class:`TaskQueue` holds the nine
  phase queues (``ph1.market`` … ``ph9.analytics``) and :func:`task_queue` is the
  typed accessor over them. The platform queues (:func:`platform_task_queues`) carry
  cross-phase workflows: ``pipeline`` runs the PART XIV pipeline workflow (R3), and
  ``pipeline.acceptance`` is the queue the Gate R3 acceptance tests serve with
  controlled activities, so a deployed pipeline worker never picks up a test run.
  Call sites never spell a queue name as a literal.
* **Workflow IDs are business keys, not random UUIDs.** Build them with
  :func:`business_workflow_id` (e.g. ``music.track.track_01HQ``) so that two
  dispatches of the same business intent address the same execution and
  Temporal's duplicate-start arbitration applies.
  ``workflow.info().workflow_id`` is that same key inside workflow code.
* **Every workflow that performs an external side effect is idempotent by
  contract.** A side-effecting activity takes an explicit ``idempotency_key``
  argument built with :func:`idempotency_key` from the business workflow id and
  a stable step name. The activity forwards the key to the provider where the
  provider supports one, and uses it for local dedupe where it does not. Keys
  derive from business identity only — never from clocks, attempt numbers or
  randomness.
* **Default retry policy** (:data:`DEFAULT_RETRY_POLICY`): initial interval 1s,
  backoff coefficient 2.0, maximum interval 60s, maximum attempts 5, and the
  permanent failure classes in :data:`NON_RETRYABLE_ERROR_TYPES`
  (:class:`NonRetryableError` and its subclasses) fail fast instead of burning
  attempts. Temporal matches non-retryable types by exact class name, so every
  subclass is listed there; :class:`CapabilityUnavailable` marks a step that today's
  runtime cannot perform at all (the owning remediation stage is in the message).
* **Global constraint "No silent transformation" (P0 §2):** a creative failure
  must never be retried as a transport failure (or the reverse). Transport
  errors are retried under the policy above; a rejected creation is a business
  outcome that policy re-decides, never something an automatic retry silently
  re-rolls.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Final

from temporalio.api.enums.v1 import TaskQueueType
from temporalio.api.taskqueue.v1 import TaskQueue as ProtoTaskQueue
from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.worker import Worker

from .config import Settings, load_settings

__all__ = [
    "DEFAULT_NAMESPACE",
    "DEFAULT_RETRY_POLICY",
    "NON_RETRYABLE_ERROR_TYPES",
    "CapabilityUnavailable",
    "CreativeRejection",
    "NonRetryableError",
    "Phase",
    "PolicyRejection",
    "TaskQueue",
    "ValidationFailure",
    "WorkerSet",
    "all_task_queues",
    "business_workflow_id",
    "connect_client",
    "create_worker",
    "idempotency_key",
    "platform_task_queues",
    "queue_is_polling",
    "queue_pollers",
    "task_queue",
]

DEFAULT_NAMESPACE: Final[str] = "default"
"""Temporal namespace used by the local/self-hosted stack (compose auto-setup default)."""


class Phase(StrEnum):
    """Phase tokens (P0 §4): the nine pipeline phases, in order."""

    MARKET = "ph1"
    AR = "ph2"
    EXPERIMENTS = "ph3"
    MUSIC = "ph4"
    QA = "ph5"
    MEDIA = "ph6"
    YOUTUBE = "ph7"
    DISTRIBUTION = "ph8"
    ANALYTICS = "ph9"


class TaskQueue(StrEnum):
    """Task-queue names, defined once for the whole platform.

    One per phase, plus the platform queues (:func:`platform_task_queues`). The member
    value is the queue name that workers poll and callers pass to
    ``start_workflow``/``execute_workflow``.
    """

    MARKET = "ph1.market"
    AR = "ph2.ar"
    EXPERIMENTS = "ph3.experiments"
    MUSIC = "ph4.music"
    QA = "ph5.qa"
    MEDIA = "ph6.media"
    YOUTUBE = "ph7.youtube"
    DISTRIBUTION = "ph8.distribution"
    ANALYTICS = "ph9.analytics"
    PIPELINE = "pipeline"
    """The PART XIV pipeline workflow, one run per P2 directive (R3)."""
    PIPELINE_ACCEPTANCE = "pipeline.acceptance"
    """Gate R3 acceptance runs with controlled activities; no deployed worker polls it."""


_PLATFORM_QUEUES: Final[tuple[TaskQueue, ...]] = (TaskQueue.PIPELINE,)


_TASK_QUEUES: Final[Mapping[Phase, TaskQueue]] = MappingProxyType(
    {
        Phase.MARKET: TaskQueue.MARKET,
        Phase.AR: TaskQueue.AR,
        Phase.EXPERIMENTS: TaskQueue.EXPERIMENTS,
        Phase.MUSIC: TaskQueue.MUSIC,
        Phase.QA: TaskQueue.QA,
        Phase.MEDIA: TaskQueue.MEDIA,
        Phase.YOUTUBE: TaskQueue.YOUTUBE,
        Phase.DISTRIBUTION: TaskQueue.DISTRIBUTION,
        Phase.ANALYTICS: TaskQueue.ANALYTICS,
    }
)


def task_queue(phase: Phase) -> TaskQueue:
    """Typed accessor for the phase→queue mapping (call sites never index a bare dict)."""
    return _TASK_QUEUES[phase]


def all_task_queues() -> tuple[TaskQueue, ...]:
    """Every phase queue, in pipeline order (the dev worker's default scope)."""
    return tuple(_TASK_QUEUES[phase] for phase in Phase)


def platform_task_queues() -> tuple[TaskQueue, ...]:
    """The deployed cross-phase queues (today: the pipeline workflow's queue)."""
    return _PLATFORM_QUEUES


@dataclass(frozen=True, slots=True)
class WorkerSet:
    """One phase's workflow and activity registrations for one task queue.

    Lives here, not in :mod:`plm_platform.worker`, so that a registration module
    resolved by ``python -m plm_platform.worker`` sees the same class object as
    the entry point (``-m`` re-imports its own file as ``__main__``).
    """

    task_queue: TaskQueue
    workflows: Sequence[type] = ()
    activities: Sequence[Callable[..., Any]] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.task_queue, TaskQueue):
            raise TypeError(
                "WorkerSet.task_queue must be a TaskQueue member, got "
                f"{type(self.task_queue).__name__}: {self.task_queue!r}"
            )


class NonRetryableError(Exception):
    """Base for permanent failures: another attempt cannot change the outcome.

    Activities raise this (or a subclass) for validation failures, rights or
    policy rejections and creative rejections. Temporal surfaces it as a
    non-retryable ``ApplicationError`` whose type is the class name;
    :data:`NON_RETRYABLE_ERROR_TYPES` mirrors those names for callers that build
    their own :class:`~temporalio.common.RetryPolicy`.
    """


class ValidationFailure(NonRetryableError):
    """Malformed or invalid input — the request itself must change, not the attempt."""


class PolicyRejection(NonRetryableError):
    """Rights, licensing, account or platform policy refused the operation."""


class CreativeRejection(NonRetryableError):
    """The creative artifact was rejected on its merits — never silently re-rolled."""


class CapabilityUnavailable(NonRetryableError):
    """The runtime cannot perform this step at all today; retrying cannot change that.

    Raised instead of faking data when a step's provider or endpoint does not exist yet.
    ``owning_stage`` names the remediation stage that delivers it (e.g. ``R4``); the
    caller turns it into a hold with an explicit reason code, never into a pass.
    """

    def __init__(self, capability: str, *, owning_stage: str, reason: str) -> None:
        if not capability.strip() or not owning_stage.strip() or not reason.strip():
            raise ValueError("capability, owning_stage and reason are all required")
        self.capability = capability
        self.owning_stage = owning_stage
        self.reason = reason
        super().__init__(f"{capability} unavailable until stage {owning_stage}: {reason}")


NON_RETRYABLE_ERROR_TYPES: Final[tuple[str, ...]] = (
    "ValidationFailure",
    "PolicyRejection",
    "CreativeRejection",
    "CapabilityUnavailable",
)
"""Permanent failure class names for ``RetryPolicy(non_retryable_error_types=...)``."""

DEFAULT_RETRY_POLICY: Final[RetryPolicy] = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=60),
    maximum_attempts=5,
    non_retryable_error_types=list(NON_RETRYABLE_ERROR_TYPES),
)
"""The platform default: retry transient transport failure only, five attempts, capped."""


def business_workflow_id(*parts: str) -> str:
    """Join business key parts into a deterministic workflow id (``a.b.c``).

    Workflow ids are business keys, not random UUIDs: two dispatches of the same
    business intent produce the same id, so Temporal can arbitrate duplicates.
    Parts must be non-empty and may not contain ``.``, ``:`` or whitespace.
    """
    if not parts:
        raise ValueError("business_workflow_id needs at least one part")
    for part in parts:
        if not part or any(char in part for char in ".: \t\n"):
            raise ValueError(
                f"invalid workflow id part {part!r}: use non-empty text without '.', ':' "
                "or whitespace, e.g. business_workflow_id('music', 'track', track_id)"
            )
    return ".".join(parts)


def idempotency_key(workflow_id: str, step: str) -> str:
    """Deterministic per-step key passed to side-effecting activities.

    Derived from the business workflow id and a stable step name — never from
    clocks, attempt numbers or randomness — so a retried activity can detect the
    earlier effect. See the module docstring for the full contract.
    """
    if not workflow_id.strip() or not step.strip():
        raise ValueError("idempotency_key needs a non-empty workflow id and step name")
    if ":" in step:
        raise ValueError(f"step name {step!r} may not contain ':' (the key separator)")
    return f"{workflow_id}:{step}"


async def connect_client(
    settings: Settings | None = None,
    *,
    namespace: str = DEFAULT_NAMESPACE,
) -> Client:
    """Connected Temporal client for ``TEMPORAL_HOST`` (via :mod:`plm_platform.config`).

    ``settings`` defaults to :func:`plm_platform.config.load_settings`; nothing in
    this module reads the environment directly.
    """
    resolved = load_settings() if settings is None else settings
    return await Client.connect(resolved.temporal_host, namespace=namespace)


def create_worker(
    client: Client,
    task_queue: TaskQueue,
    *,
    workflows: Sequence[type] = (),
    activities: Sequence[Callable[..., Any]] = (),
    **options: Any,
) -> Worker:
    """Worker factory: one task queue, the given registrations, optional tuning.

    ``options`` passes through to :class:`temporalio.worker.Worker`; the queue is
    always a :class:`TaskQueue` member, never a literal. A worker must register at
    least one workflow or activity (Temporal requirement), enforced here with a
    clear error instead of the SDK's generic one.
    """
    if not workflows and not activities:
        raise ValueError(
            "a Temporal worker must register at least one workflow or activity "
            "(plm_platform.worker adds the PlatformHeartbeat workflow automatically)"
        )
    return Worker(
        client,
        task_queue=task_queue.value,
        workflows=list(workflows),
        activities=list(activities),
        **options,
    )


async def queue_pollers(
    client: Client,
    task_queue: TaskQueue,
    *,
    namespace: str = DEFAULT_NAMESPACE,
) -> tuple[str, ...]:
    """Worker identities currently polling ``task_queue`` for workflow tasks.

    Used by healthchecks and tests to prove a worker is *alive and polling*,
    which process liveness alone cannot show.
    """
    request = DescribeTaskQueueRequest(
        namespace=namespace,
        task_queue=ProtoTaskQueue(name=task_queue.value),
        task_queue_type=TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW,
    )
    response = await client.workflow_service.describe_task_queue(request)
    return tuple(poller.identity for poller in response.pollers)


async def queue_is_polling(
    client: Client,
    task_queue: TaskQueue,
    *,
    namespace: str = DEFAULT_NAMESPACE,
    identity_prefix: str | None = None,
) -> bool:
    """True when any (or any matching ``identity_prefix``) poller serves the queue."""
    pollers = await queue_pollers(client, task_queue, namespace=namespace)
    if identity_prefix is None:
        return bool(pollers)
    return any(identity.startswith(identity_prefix) for identity in pollers)
