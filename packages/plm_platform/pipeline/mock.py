"""A controllable :class:`~plm_platform.pipeline.activities.PhaseSteps` for tests.

Every step is scripted (:class:`ScriptedStep`): its outputs, provider ids and reported
cost, and optionally a failure (a typed exception every attempt, a typed exception for
the first N idempotency keys, or a transient error for the first N attempts) or a block
(the first attempt performs its effect, then waits until cancelled, standing in for a
worker that dies mid-activity).

The mock is honest about effects. An *effect* is what a real provider call would leave
behind (a generation, an upload). It is applied at most once per idempotency key: a
retried call with a key that already has an effect returns the stored result and
applies nothing. ``effect_counts`` and ``calls`` let a test prove that a retried or
replayed activity changed the world exactly once. A mock pass proves the workflow and
its persistence, never the phases (AGENTS.md).

**Write guard.** Given an :class:`~plm_platform.execution.ExternalWriteGuard`, the paid
steps (:data:`~.states.PAID_STEP_CAPABILITIES`) behave like a phase whose guard funds a
*live* write: the guard reserves under the step's idempotency key before the effect
(``cost_estimate=None``, so the ledger's rate card prices it), the reservation is
committed with the scripted cost after it, the step reports the ledger entry's amount
and basis, and its receipt (``external_writes``). A refusal raises
:class:`~.phase_client.ExternalWriteRefused` before any effect, exactly as the HTTP
step does on the phase's 409. Without a guard the scripted cost is reported as is.

No clock is read: ``published_at`` derives from the request's ``requested_at``
(``workflow.now()``) plus a scripted offset.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import Any, Final

from plm_contracts import ProviderMode
from plm_contracts.money import Money

from ..execution import ExternalWriteGuard, GuardDecision
from ..ledger import BudgetLedger
from ..temporal import Phase
from .phase_client import ExternalWriteRefused
from .states import PAID_STEP_CAPABILITIES, Step
from .types import MoneyAmount, StepRequest, StepResult

__all__ = ["MockPhaseSteps", "ScriptedStep", "TransientStepError", "default_scripts"]

_STEP_PHASE: Final[Mapping[Step, Phase]] = {
    Step.GENERATE: Phase.MUSIC,
    Step.UPLOAD: Phase.YOUTUBE,
    Step.SUBMIT_DISTRIBUTION: Phase.DISTRIBUTION,
}


class TransientStepError(RuntimeError):
    """A scripted retryable failure (a transport error before any effect)."""


@dataclass
class ScriptedStep:
    outputs: dict[str, Any] = field(default_factory=dict)
    provider_ids: dict[str, str] = field(default_factory=dict)
    cost: MoneyAmount | None = None
    raises: Exception | None = None
    """Raised on every attempt, before any effect (e.g. ``CapabilityUnavailable``)."""
    raises_for_first_keys: int = 0
    raises_first: Exception | None = None
    """Raised, before any effect, for the first N distinct idempotency keys of the step
    (e.g. a ``PlaylistSupplyShort`` for the first two assemble rounds)."""
    transient_failures: int = 0
    """The first N attempts raise :class:`TransientStepError` before any effect."""
    block_after_effect_on_first_attempt: bool = False
    """Attempt 1 applies the effect, then waits until cancelled (never returns)."""


def default_scripts(*, outcome_horizon: str = "24h",
                    published_offset: timedelta = timedelta(hours=-24)) -> dict[Step, ScriptedStep]:
    """A complete happy path. ``published_offset`` is added to the processing step's
    ``requested_at`` to give the provider-reported ``published_at``."""
    return {
        Step.RESEARCH: ScriptedStep(
            outputs={"experiment_id": "exp-mock", "outcome_horizon": outcome_horizon,
                     "arm_id": "arm-mock"},
            provider_ids={"experiment_id": "exp-mock"},
        ),
        Step.POLICY_CHECK: ScriptedStep(
            outputs={"validated": True, "package_ids": ["pkg-mock"]},
            provider_ids={"package_id": "pkg-mock"},
        ),
        Step.GENERATE: ScriptedStep(
            outputs={"candidate_ids": ["cand-mock-1"]},
            provider_ids={"plan_id": "plan-mock", "candidate_id": "cand-mock-1"},
            cost=MoneyAmount(amount="2.50", currency="USD"),
        ),
        Step.QA: ScriptedStep(outputs={"disposition": "PASSED", "holds": []},
                              provider_ids={"gate_decision_id": "gate-mock"}),
        Step.ASSEMBLE: ScriptedStep(outputs={"media_package_id": "media-mock"},
                                    provider_ids={"media_package_id": "media-mock"}),
        Step.UPLOAD: ScriptedStep(outputs={"video_id": "yt-mock"},
                                  provider_ids={"youtube_video_id": "yt-mock"}),
        Step.SUBMIT_DISTRIBUTION: ScriptedStep(
            outputs={"release_id": "rel-mock"},
            provider_ids={"distributor_release_id": "rel-mock"},
            cost=MoneyAmount(amount="1.00", currency="USD"),
        ),
        Step.AWAIT_PROCESSING: ScriptedStep(outputs={"published_offset_seconds":
                                                     published_offset.total_seconds()},
                                            provider_ids={"youtube_video_id": "yt-mock"}),
        Step.EVALUATE_OUTCOME: ScriptedStep(
            outputs={"reward_fact_key": "mock-fact", "maturity": "mature"},
            provider_ids={"outcome_id": "outcome-mock"},
        ),
        Step.DISPATCH_REWARD: ScriptedStep(outputs={"delivery_status": "DELIVERED"},
                                           provider_ids={"outbox_message_id": "obx-mock"}),
        Step.EXTEND: ScriptedStep(outputs={"track_spec_ids": ["spec-topup-mock"]},
                                  provider_ids={"extension_id": "ext-mock"}),
        Step.LEARN: ScriptedStep(outputs={"update": {"applied": 1},
                                          "next_cycle": {"cycle_id": "cycle-mock"}},
                                 provider_ids={"next_cycle_id": "cycle-mock"}),
    }


class MockPhaseSteps:
    """Scripted phase steps with an at-most-once effect store keyed by idempotency key."""

    def __init__(self, scripts: Mapping[Step, ScriptedStep] | None = None, *,
                 guard: ExternalWriteGuard | None = None,
                 ledger: BudgetLedger | None = None) -> None:
        if (guard is None) != (ledger is None):
            raise ValueError("a guarded mock needs the guard and the ledger it funds from")
        self.scripts: dict[Step, ScriptedStep] = dict(
            default_scripts() if scripts is None else scripts
        )
        self.guard = guard
        self.ledger = ledger
        self.calls: list[tuple[str, str]] = []
        """(step, idempotency key) per call, in call order (every attempt)."""
        self.effects: dict[str, StepResult] = {}
        self.effect_counts: Counter[str] = Counter()
        self.blocked = asyncio.Event()
        """Set when a scripted block has applied its effect and is waiting."""
        self._attempts: Counter[str] = Counter()
        self._keys: dict[Step, list[str]] = {}
        self._settled: set[str] = set()

    def calls_for(self, step: Step) -> list[str]:
        return [key for name, key in self.calls if name == step.value]

    def effects_for(self, step: Step) -> int:
        """How many distinct effects the step applied (one per idempotency key)."""
        keys = set(self.calls_for(step))
        return sum(self.effect_counts[key] for key in keys)

    def _authorize(self, step: Step, request: StepRequest) -> GuardDecision | None:
        capability = PAID_STEP_CAPABILITIES.get(step)
        if self.guard is None or capability is None:
            return None
        decision = self.guard.authorize_external_write(
            capability, provider_mode=ProviderMode.REAL,
            idempotency_key=request.idempotency_key, cost_estimate=None,
            now=request.requested_at)
        if not decision.decision.may_call_provider:
            raise ExternalWriteRefused(
                _STEP_PHASE[step], f"/mock/{step.value}", 409,
                f"external_write_{decision.decision.value}",
                f"{capability} not performed: {decision.reason}")
        return decision

    def _settle(self, step: Step, decision: GuardDecision | None, script: ScriptedStep,
                result: StepResult, key: str) -> StepResult:
        """Commit the reservation once, then report the ledger's amount and basis."""
        if decision is None or self.ledger is None or self.guard is None:
            return result
        if key not in self._settled:
            actual = (None if script.cost is None
                      else Money(amount=Decimal(script.cost.amount),
                                 currency=script.cost.currency))
            self.guard.settle_external_write(decision, performed=True, actual_cost=actual)
            self._settled.add(key)
        entry = self.ledger.entry(decision.capability, key)
        receipt = {"capability": decision.capability, "idempotency_key": key,
                   "decision": decision.decision.value, "reason": decision.reason}
        outputs = {**result.outputs, "external_writes": [receipt]}
        if entry is None or entry.actual is None:
            return StepResult(outputs=outputs, provider_ids=result.provider_ids,
                              cost=None, cost_basis=None)
        return StepResult(
            outputs=outputs, provider_ids=result.provider_ids,
            cost=MoneyAmount(amount=str(entry.actual.amount), currency=entry.actual.currency),
            cost_basis=entry.actual_basis)

    async def _run(self, step: Step, request: StepRequest) -> StepResult:
        self.calls.append((step.value, request.idempotency_key))
        self._attempts[request.idempotency_key] += 1
        attempt = self._attempts[request.idempotency_key]
        script = self.scripts.get(step)
        if script is None:
            raise KeyError(f"no script for step {step.value!r}")
        keys = self._keys.setdefault(step, [])
        if request.idempotency_key not in keys:
            keys.append(request.idempotency_key)
        if script.raises is not None:
            raise script.raises
        if script.raises_first is not None and (
                keys.index(request.idempotency_key) < script.raises_for_first_keys):
            raise script.raises_first
        if attempt <= script.transient_failures:
            raise TransientStepError(f"scripted transient failure {attempt} of {step.value}")
        decision = self._authorize(step, request)
        stored = self.effects.get(request.idempotency_key)
        if stored is not None:
            # The effect already happened under this key: apply nothing, settle once.
            return self._settle(step, decision, script, stored, request.idempotency_key)
        result = StepResult(
            outputs=self._outputs(step, script, request),
            provider_ids=dict(script.provider_ids),
            cost=None if self.guard is not None and decision is not None else script.cost,
        )
        self.effects[request.idempotency_key] = result
        self.effect_counts[request.idempotency_key] += 1
        if script.block_after_effect_on_first_attempt and attempt == 1:
            self.blocked.set()
            await asyncio.Event().wait()  # until the worker cancels this attempt
        return self._settle(step, decision, script, result, request.idempotency_key)

    @staticmethod
    def _outputs(step: Step, script: ScriptedStep, request: StepRequest) -> dict[str, Any]:
        outputs = dict(script.outputs)
        if step is Step.AWAIT_PROCESSING and "published_offset_seconds" in outputs:
            offset = timedelta(seconds=float(outputs.pop("published_offset_seconds")))
            outputs["published_at"] = (request.requested_at + offset).isoformat()
        return outputs

    async def research(self, request: StepRequest) -> StepResult:
        return await self._run(Step.RESEARCH, request)

    async def policy_check(self, request: StepRequest) -> StepResult:
        return await self._run(Step.POLICY_CHECK, request)

    async def generate(self, request: StepRequest) -> StepResult:
        return await self._run(Step.GENERATE, request)

    async def qa(self, request: StepRequest) -> StepResult:
        return await self._run(Step.QA, request)

    async def assemble(self, request: StepRequest) -> StepResult:
        return await self._run(Step.ASSEMBLE, request)

    async def upload(self, request: StepRequest) -> StepResult:
        return await self._run(Step.UPLOAD, request)

    async def submit_distribution(self, request: StepRequest) -> StepResult:
        return await self._run(Step.SUBMIT_DISTRIBUTION, request)

    async def await_processing(self, request: StepRequest) -> StepResult:
        return await self._run(Step.AWAIT_PROCESSING, request)

    async def evaluate_outcome(self, request: StepRequest) -> StepResult:
        return await self._run(Step.EVALUATE_OUTCOME, request)

    async def dispatch_reward(self, request: StepRequest) -> StepResult:
        return await self._run(Step.DISPATCH_REWARD, request)

    async def extend(self, request: StepRequest) -> StepResult:
        return await self._run(Step.EXTEND, request)

    async def learn(self, request: StepRequest) -> StepResult:
        return await self._run(Step.LEARN, request)
