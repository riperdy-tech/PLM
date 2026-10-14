"""The single choke point for external side effects, and boot-time capability checks.

Every adapter call that changes something outside PLM (an upload, a release
submission, a takedown) must first obtain a :class:`GuardDecision` from
:meth:`ExternalWriteGuard.authorize_external_write`. The guard combines four
independent conditions; all must hold before a real write proceeds:

1. **Execution policy** for the capability is ``LIVE``. The default for every capability
   is ``DRY_RUN``, so an unconfigured capability can never write.
2. **An explicit authorization record** for that capability exists and has not expired.
   Authorizations are granted programmatically by an operator-facing path, never
   inferred from configuration or from ``ProviderMode.REAL``. (R3 persists them.)
3. **A budget reservation** succeeds. Without a ledger the default
   :class:`NoBudgetLedger` refuses every live write: fail closed. With one
   (:class:`plm_platform.ledger.PersistentBudgetGate`) the cost is reserved against the
   persisted ceiling; after the provider call the caller settles it with
   :meth:`ExternalWriteGuard.settle_external_write` (committed or released).
4. **An idempotency key** is supplied, so a retried call cannot cause a second effect.

A call served by a ``ProviderMode.MOCK`` implementation has no external effect; the
guard answers ``SIMULATE`` so mock pipelines still run end to end. Every decision is
logged and kept in :attr:`ExternalWriteGuard.decisions` for audit.

Boot validation (:func:`validate_integrations`) fails a service at startup only when an
integration it *requires* has no implementation for its configured provider mode;
missing optional integrations degrade with a logged warning.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Final, Protocol, runtime_checkable

from plm_contracts import ExecutionPolicy, ProviderMode
from plm_contracts.money import Money

from plm_platform.clock import require_aware

from .config import SettingsError
from .dotenv import load_env_file

__all__ = [
    "BUDGET_LEDGER_CURRENCY_VAR",
    "EXECUTION_POLICY_PREFIX",
    "PROVIDER_MODE_PREFIX",
    "BudgetGate",
    "BudgetSettlement",
    "BudgetVerdict",
    "CapabilityUnavailableError",
    "CostEstimate",
    "ExecutionSettings",
    "ExternalWriteGuard",
    "ExternalWriteRefusedError",
    "GuardDecision",
    "IntegrationRequirement",
    "NoBudgetLedger",
    "WriteAuthorization",
    "WriteDecision",
    "load_execution_settings",
    "provider_mode_of",
    "validate_integrations",
]

logger = logging.getLogger(__name__)

PROVIDER_MODE_PREFIX: Final[str] = "PLM_PROVIDER_MODE__"
"""``PLM_PROVIDER_MODE__YOUTUBE=real`` selects the real YouTube implementation."""

EXECUTION_POLICY_PREFIX: Final[str] = "PLM_EXECUTION_POLICY__"
"""``PLM_EXECUTION_POLICY__YOUTUBE_UPLOAD=live`` sets the policy for ``youtube.upload``."""

BUDGET_LEDGER_CURRENCY_VAR: Final[str] = "PLM_BUDGET_LEDGER_CURRENCY"
"""``PLM_BUDGET_LEDGER_CURRENCY=USD`` turns on the persisted budget ledger in that currency.
Unset, live writes meet :class:`NoBudgetLedger` and are refused."""

DEFAULT_PROVIDER_MODE: Final[ProviderMode] = ProviderMode.MOCK
DEFAULT_EXECUTION_POLICY: Final[ExecutionPolicy] = ExecutionPolicy.DRY_RUN


class WriteDecision(StrEnum):
    """What the caller may do with one external write."""

    SIMULATE = "simulate"
    """Mock provider: call it; nothing leaves the process."""

    EXECUTE = "execute"
    """Real provider, live policy, authorized, budgeted: perform the write."""

    RECORD_INTENT = "record_intent"
    """Real provider under dry-run or shadow: do not call; the intent is recorded."""

    DENY = "deny"
    """Refused: read-only policy, no authorization, no budget or no idempotency key."""

    @property
    def may_call_provider(self) -> bool:
        return self in {WriteDecision.SIMULATE, WriteDecision.EXECUTE}


@dataclass(frozen=True, slots=True)
class WriteAuthorization:
    """An operator's explicit grant for one capability to write externally."""

    capability: str
    granted_by: str
    granted_at: datetime
    expires_at: datetime | None = None
    reference: str | None = None

    def is_valid_at(self, now: datetime) -> bool:
        return self.granted_at <= now and (self.expires_at is None or now < self.expires_at)


@dataclass(frozen=True, slots=True)
class BudgetVerdict:
    allowed: bool
    reason: str


type CostEstimate = Money | Decimal | None
"""What a caller knows about a write's cost.

``Money`` is a priced write. ``None`` means the caller cannot price it (the gate may
price it from its rate card, else it is unfunded); it is never read as zero. A bare
``Decimal`` is accepted for gates that predate currencies; the persisted ledger refuses
it, because an amount without a currency cannot be charged against a ceiling."""


class BudgetGate(Protocol):
    """Reserves spend for one write before it happens."""

    def reserve(
        self, capability: str, idempotency_key: str, cost_estimate: CostEstimate
    ) -> BudgetVerdict: ...


@runtime_checkable
class BudgetSettlement(Protocol):
    """A budget gate that records what happened to a funded write after the provider call."""

    def commit(self, capability: str, idempotency_key: str, *, actual: Money | None) -> None: ...

    def release(self, capability: str, idempotency_key: str, *, reason: str) -> None: ...


class NoBudgetLedger:
    """Default budget gate: no ledger is configured, so no live write can be funded."""

    def reserve(
        self, capability: str, idempotency_key: str, cost_estimate: CostEstimate
    ) -> BudgetVerdict:
        return BudgetVerdict(allowed=False, reason="budget_ledger_unavailable")


@dataclass(frozen=True, slots=True)
class GuardDecision:
    capability: str
    decision: WriteDecision
    reason: str
    provider_mode: ProviderMode
    policy: ExecutionPolicy
    idempotency_key: str
    cost_estimate: CostEstimate
    decided_at: datetime


class ExternalWriteRefusedError(RuntimeError):
    """Raised by callers that cannot represent a non-executed write in their own state."""

    def __init__(self, decision: GuardDecision) -> None:
        self.decision = decision
        super().__init__(
            f"external write {decision.capability!r} not performed: "
            f"{decision.decision.value} ({decision.reason})"
        )


@dataclass(frozen=True, slots=True)
class ExecutionSettings:
    """Provider modes per integration and execution policies per capability."""

    provider_modes: Mapping[str, ProviderMode] = field(default_factory=dict)
    policies: Mapping[str, ExecutionPolicy] = field(default_factory=dict)
    budget_currency: str | None = None
    """The persisted budget ledger's currency; ``None`` means no ledger is configured."""

    def provider_mode(self, integration: str) -> ProviderMode:
        return self.provider_modes.get(integration, DEFAULT_PROVIDER_MODE)

    def policy(self, capability: str) -> ExecutionPolicy:
        return self.policies.get(capability, DEFAULT_EXECUTION_POLICY)


def _capability_from_env_suffix(suffix: str) -> str:
    """``YOUTUBE_UPLOAD`` -> ``youtube.upload`` (first underscore separates the integration)."""
    integration, _, action = suffix.lower().partition("_")
    return f"{integration}.{action}" if action else integration


def load_execution_settings(
    env: Mapping[str, str] | None = None,
    dotenv_path: str | Path | None = None,
) -> ExecutionSettings:
    """Read ``PLM_PROVIDER_MODE__*``, ``PLM_EXECUTION_POLICY__*`` and
    ``PLM_BUDGET_LEDGER_CURRENCY`` (env, then ``.env``).

    Unset means the safe default (mock provider, dry-run policy). An unrecognised value
    raises :class:`SettingsError` naming the variable.
    """
    process_env: Mapping[str, str] = os.environ if env is None else env
    dotenv_values = load_env_file(Path(".env") if dotenv_path is None else dotenv_path)
    merged = {**dotenv_values, **{k: v for k, v in process_env.items() if v.strip()}}

    modes: dict[str, ProviderMode] = {}
    policies: dict[str, ExecutionPolicy] = {}
    budget_currency: str | None = None
    for name, raw in sorted(merged.items()):
        if not raw.strip():
            continue
        if name == BUDGET_LEDGER_CURRENCY_VAR:
            budget_currency = raw.strip().upper()
            if len(budget_currency) != 3 or not budget_currency.isalpha():
                raise SettingsError(f"invalid value for {name!r}: expected an ISO 4217 code")
        elif name.startswith(PROVIDER_MODE_PREFIX):
            modes[name[len(PROVIDER_MODE_PREFIX):].lower()] = _parse(name, raw, ProviderMode)
        elif name.startswith(EXECUTION_POLICY_PREFIX):
            capability = _capability_from_env_suffix(name[len(EXECUTION_POLICY_PREFIX):])
            policies[capability] = _parse(name, raw, ExecutionPolicy)
    return ExecutionSettings(
        provider_modes=modes, policies=policies, budget_currency=budget_currency
    )


def _parse[E: StrEnum](name: str, raw: str, enum: type[E]) -> E:
    try:
        return enum(raw.strip().lower())
    except ValueError:
        allowed = ", ".join(member.value for member in enum)
        raise SettingsError(f"invalid value for {name!r}: expected one of {allowed}") from None


def provider_mode_of(client: object) -> ProviderMode:
    """A client's declared ``provider_mode``; undeclared clients count as REAL (fail closed)."""
    declared = getattr(client, "provider_mode", None)
    return declared if isinstance(declared, ProviderMode) else ProviderMode.REAL


class ExternalWriteGuard:
    """Decides, and records, whether one external write may happen."""

    def __init__(
        self,
        settings: ExecutionSettings | None = None,
        *,
        authorizations: Iterable[WriteAuthorization] = (),
        budget: BudgetGate | None = None,
    ) -> None:
        self._settings = settings or ExecutionSettings()
        self._authorizations = {a.capability: a for a in authorizations}
        self._budget: BudgetGate = budget or NoBudgetLedger()
        self.decisions: list[GuardDecision] = []

    def authorize_external_write(
        self,
        capability: str,
        *,
        provider_mode: ProviderMode,
        idempotency_key: str,
        cost_estimate: CostEstimate = None,
        policy_ceiling: ExecutionPolicy | None = None,
        now: datetime,
    ) -> GuardDecision:
        """Decide one write at ``now``. ``policy_ceiling`` lets a caller tighten, never loosen, policy.

        ``cost_estimate`` defaults to ``None`` (not priced by the caller), never to zero.
        """
        now = require_aware(now)
        policy = self._settings.policy(capability)
        if policy_ceiling is not None and _rank(policy_ceiling) < _rank(policy):
            policy = policy_ceiling
        decision, reason = self._decide(
            capability, provider_mode, policy, idempotency_key, cost_estimate, now
        )
        result = GuardDecision(
            capability=capability,
            decision=decision,
            reason=reason,
            provider_mode=provider_mode,
            policy=policy,
            idempotency_key=idempotency_key,
            cost_estimate=cost_estimate,
            decided_at=now,
        )
        self.decisions.append(result)
        logger.info(
            "external write decision",
            extra={
                "capability": capability,
                "decision": decision.value,
                "reason": reason,
                "provider_mode": provider_mode.value,
                "policy": policy.value,
                "idempotency_key": idempotency_key,
            },
        )
        return result

    def _decide(
        self,
        capability: str,
        provider_mode: ProviderMode,
        policy: ExecutionPolicy,
        idempotency_key: str,
        cost_estimate: CostEstimate,
        now: datetime,
    ) -> tuple[WriteDecision, str]:
        if not idempotency_key.strip():
            return WriteDecision.DENY, "missing_idempotency_key"
        if provider_mode is ProviderMode.MOCK:
            return WriteDecision.SIMULATE, "mock_provider"
        if policy is ExecutionPolicy.READ_ONLY:
            return WriteDecision.DENY, "policy_read_only"
        if policy is ExecutionPolicy.DRY_RUN:
            return WriteDecision.RECORD_INTENT, "policy_dry_run"
        if policy is ExecutionPolicy.SHADOW:
            return WriteDecision.RECORD_INTENT, "policy_shadow"
        grant = self._authorizations.get(capability)
        if grant is None:
            return WriteDecision.DENY, "no_authorization"
        if not grant.is_valid_at(now):
            return WriteDecision.DENY, "authorization_expired"
        verdict = self._budget.reserve(capability, idempotency_key, cost_estimate)
        if not verdict.allowed:
            return WriteDecision.DENY, verdict.reason
        return WriteDecision.EXECUTE, "authorized"

    def settle_external_write(
        self,
        decision: GuardDecision,
        *,
        performed: bool,
        actual_cost: Money | None = None,
        reason: str = "write_not_performed",
    ) -> bool:
        """Record the outcome of a write this guard funded; call it after the provider call.

        ``performed=True`` commits the reservation (``actual_cost`` when the provider
        reported one, else the reserved estimate, labelled as such). ``performed=False``
        releases it, and is only for a write known not to have happened. When the outcome
        is uncertain (the provider call raised), do not settle: the reservation keeps
        holding headroom until an operator reconciles it (``plm budget``).

        Only an ``EXECUTE`` decision reserved budget; any other decision is a no-op, as is
        a gate without settlement. A settlement failure is logged, not raised: the
        external effect has already happened or not, and the reservation stays held,
        which is the conservative state. Returns whether the ledger recorded it.
        """
        if decision.decision is not WriteDecision.EXECUTE:
            return False
        if not isinstance(self._budget, BudgetSettlement):
            return False
        try:
            if performed:
                self._budget.commit(
                    decision.capability, decision.idempotency_key, actual=actual_cost
                )
            else:
                self._budget.release(decision.capability, decision.idempotency_key, reason=reason)
        except Exception:  # deliberate: the reservation stays held (see docstring)
            logger.exception(
                "budget settlement failed; reservation left held",
                extra={"capability": decision.capability, "idempotency_key": decision.idempotency_key},
            )
            return False
        return True


_POLICY_ORDER: Final[tuple[ExecutionPolicy, ...]] = (
    ExecutionPolicy.READ_ONLY,
    ExecutionPolicy.DRY_RUN,
    ExecutionPolicy.SHADOW,
    ExecutionPolicy.LIVE,
)


def _rank(policy: ExecutionPolicy) -> int:
    return _POLICY_ORDER.index(policy)


class CapabilityUnavailableError(SettingsError):
    """A required integration has no implementation for its configured provider mode."""


@dataclass(frozen=True, slots=True)
class IntegrationRequirement:
    """One integration a service uses, which provider modes it implements, and if required."""

    integration: str
    implemented: frozenset[ProviderMode]
    required: bool


def validate_integrations(
    requirements: Iterable[IntegrationRequirement], settings: ExecutionSettings
) -> list[str]:
    """Fail on a required integration with no implementation; warn on optional ones.

    Returns the warnings (also logged) so callers can surface degraded capabilities.
    """
    warnings: list[str] = []
    for req in requirements:
        mode = settings.provider_mode(req.integration)
        if mode in req.implemented:
            continue
        implemented = ", ".join(sorted(m.value for m in req.implemented)) or "none"
        message = (
            f"integration {req.integration!r}: provider mode {mode.value!r} is configured "
            f"but only [{implemented}] is implemented"
        )
        if req.required:
            raise CapabilityUnavailableError(message)
        logger.warning("%s; continuing without it", message)
        warnings.append(message)
    return warnings
