"""Deployed registrations for the ``pipeline`` task queue (edge: builds clients from config).

``python -m plm_platform.worker --task-queue pipeline --load
plm_platform.pipeline.worker:REGISTRATIONS`` (compose service ``pipeline-worker``).

``REGISTRATIONS`` is built on first access, from configuration:

* phase steps over HTTP (:class:`~plm_platform.pipeline.http.HttpPhaseSteps`), with base
  URLs from ``PLM_SERVICE_URL__<PHASE>`` (compose service names by default), the
  service token ``PLM_SERVICE_TOKEN`` and the publishing / release / FX / learning
  configuration from ``PLM_PIPELINE_CONFIG_FILE`` (:mod:`.config`; a missing section
  makes the step that needs it refuse);
* platform operations over ``ops``: the budget ledger in ``PLM_BUDGET_LEDGER_CURRENCY``,
  read for the run's write costs (unset: the outcome step refuses, never free spend; the
  phases' guards reserve, not the pipeline), and the exception queue with the reviewer
  directory from ``PLM_PIPELINE_REVIEWERS_FILE`` (a JSON ``{"actor": ["role", ...]}``;
  unset: nobody holds a role, so no reviewable hold can resume).
"""

from __future__ import annotations

import os
from typing import Any, Final

import httpx

from ..config import load_settings
from ..db import build_engine, session_factory
from ..exceptions import NO_REVIEWERS, ExceptionQueue, StaticReviewerDirectory
from ..execution import load_execution_settings
from ..ledger import BudgetLedger
from ..service_urls import service_urls
from ..temporal import TaskQueue, WorkerSet
from .activities import PlatformOperations, build_activities
from .config import load_pipeline_config
from .http import HttpPhaseSteps, PhaseClient
from .workflow import PipelineWorkflow

__all__ = ["REVIEWERS_FILE_VAR", "build_registrations"]  # REGISTRATIONS: module __getattr__

REVIEWERS_FILE_VAR: Final[str] = "PLM_PIPELINE_REVIEWERS_FILE"
PHASE_TIMEOUT_SECONDS: Final[float] = 30.0


def build_registrations() -> tuple[WorkerSet, ...]:
    settings = load_settings()
    token = None if settings.service_token is None else settings.service_token.get_secret_value()
    clients = {
        phase: PhaseClient(phase, httpx.Client(base_url=url, timeout=PHASE_TIMEOUT_SECONDS),
                           token=token)
        for phase, url in service_urls().items()
    }
    factory = session_factory(build_engine(settings))
    currency = load_execution_settings().budget_currency
    ledger = None if currency is None else BudgetLedger(factory, currency=currency)
    reviewers_file = os.environ.get(REVIEWERS_FILE_VAR, "").strip()
    reviewers = (StaticReviewerDirectory.from_json_file(reviewers_file) if reviewers_file
                 else NO_REVIEWERS)
    platform = PlatformOperations(factory, queue=ExceptionQueue(reviewers=reviewers),
                                  ledger=ledger)
    return (
        WorkerSet(
            task_queue=TaskQueue.PIPELINE,
            workflows=(PipelineWorkflow,),
            activities=tuple(build_activities(
                HttpPhaseSteps(clients, config=load_pipeline_config()), platform)),
        ),
    )


def __getattr__(name: str) -> Any:
    if name == "REGISTRATIONS":
        return build_registrations()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
