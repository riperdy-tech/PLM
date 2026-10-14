"""The exception queue with hold classes (R3, architecture PART XVIII, ADR-0033).

A hold stops a workflow until a human or an external process clears it. Every hold is a
row in ``ops.exception_cases`` with an append-only history in ``ops.exception_events``.
Phases reach these tables only through :class:`ExceptionQueue` (ADR-0033).

**Hold classes** come from versioned policy data (``plm_platform/policy/hold_classes.json``,
:func:`load_hold_class_policy`), never from the caller:

* ``HARD_STOP`` (copyright and platform-policy disputes, KYC, account security, payment
  changes and every P29 category): no approve or override from any role resumes it. It
  closes only through :meth:`ExceptionQueue.record_external_resolution`, carrying the
  external process's evidence (for example a dispute id and its outcome).
* ``REVIEWABLE_QUARANTINE``: resumes only for an actor that the injected
  :class:`ReviewerDirectory` confirms holds the case's ``reviewer_role``, and only with
  every ``required_evidence`` item present. Unknown rights are reviewable, but their only
  exit is the license evidence.
* A reason code the policy does not map is classified ``HARD_STOP`` and flagged
  (``policy_mapped = False``): fail closed.

The classification is copied onto the case when it is raised, so a later policy version
never silently reclassifies an open hold.

**State machine** (``CaseStatus``)::

    OPEN --resolve(RESUME), reviewable, authorized reviewer + evidence--> RESOLVED
    OPEN --record_external_resolution(CLEARED), hard stop + evidence----> RESOLVED
    OPEN --record_external_resolution(ADVERSE), hard stop + evidence----> REJECTED
    OPEN --resolve(REJECT)----------------------------------------------> REJECTED
    OPEN --resolve(WITHDRAW) (superseded, e.g. re-evaluation)-----------> WITHDRAWN

``RESOLVED``, ``REJECTED`` and ``WITHDRAWN`` are terminal. Only ``RESOLVED`` lets a
workflow continue; :meth:`ExceptionQueue.authorize_resume` is the single check a resume
handler calls before signalling a workflow.

Every decision (raise, authorization, refusal, resolution) is an event with its actor and
an injected ``now``; nothing here reads the wall clock. Ids derive from ``hashlib``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any, Final, Literal, NoReturn, Protocol

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .clock import require_aware
from .db import PLATFORM_SCHEMA

__all__ = [
    "EXCEPTIONS_METADATA",
    "MANDATORY_HARD_STOPS",
    "NO_REVIEWERS",
    "ORIGIN_PHASES",
    "CaseResolution",
    "CaseStatus",
    "DecisionCode",
    "EventType",
    "ExceptionCase",
    "ExceptionCaseNotFoundError",
    "ExceptionEvent",
    "ExceptionQueue",
    "ExternalOutcome",
    "ExternalResolutionRefusedError",
    "HoldClass",
    "HoldClassPolicy",
    "HoldClassPolicyError",
    "HoldClassification",
    "HoldRule",
    "HoldSeverity",
    "RaiseResult",
    "ResumeDecision",
    "ResumeRefusedError",
    "ReviewDecision",
    "ReviewerDirectory",
    "StaticReviewerDirectory",
    "case_id_for",
    "exception_cases_table",
    "exception_events_table",
    "load_hold_class_policy",
    "most_severe_refusal",
    "parse_hold_class_policy",
]

logger = logging.getLogger(__name__)

POLICY_PACKAGE: Final[str] = "plm_platform.policy"
POLICY_FILE: Final[str] = "hold_classes.json"

ORIGIN_PHASES: Final[frozenset[str]] = frozenset(
    {"P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8", "P9", "platform"}
)
"""Where a hold can originate (ADR-0033): a phase, or the platform itself."""

MANDATORY_HARD_STOPS: Final[frozenset[str]] = frozenset(
    {
        # P0 §2 human hard-stops (docs/P0_master_integration_implementation_plan.md:66)
        "COPYRIGHT_DISPUTE",
        "PLATFORM_POLICY_DISPUTE",
        "KYC_REQUIRED",
        "ACCOUNT_SECURITY_EVENT",
        "PAYMENT_METHOD_CHANGE",
        # P29 (docs/architecture.md:911-918)
        "PROTECTED_ARTIST_REFERENCE_DETECTED",
        "LYRIC_SIMILARITY_HIGH",
        "SAMPLE_PROVENANCE_UNCERTAIN",
        "COMMERCIAL_LICENSE_UNCERTAIN",
        "UNAUTHORIZED_VOICE_CLONE_DETECTED",
        "AI_POLICY_CONFLICT",
        "DISTRIBUTOR_REJECTION",
        "PLATFORM_CLAIM",
    }
)
"""Codes the policy data must carry as ``HARD_STOP``. A data file that omits one or
classifies it reviewable is refused at load: policy data cannot relax a human hard stop."""

_REASON_CODE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")


# --------------------------------------------------------------------------- vocabulary


class HoldClass(StrEnum):
    REVIEWABLE_QUARANTINE = "REVIEWABLE_QUARANTINE"
    HARD_STOP = "HARD_STOP"


class HoldSeverity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class CaseStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"


class CaseResolution(StrEnum):
    RESUMED_BY_REVIEWER = "RESUMED_BY_REVIEWER"
    CLEARED_BY_EXTERNAL_PROCESS = "CLEARED_BY_EXTERNAL_PROCESS"
    REJECTED_BY_REVIEWER = "REJECTED_BY_REVIEWER"
    REJECTED_BY_EXTERNAL_PROCESS = "REJECTED_BY_EXTERNAL_PROCESS"
    WITHDRAWN = "WITHDRAWN"


class ReviewDecision(StrEnum):
    """What a reviewer asks :meth:`ExceptionQueue.resolve` to do."""

    RESUME = "RESUME"
    REJECT = "REJECT"
    WITHDRAW = "WITHDRAW"


class ExternalOutcome(StrEnum):
    """The external process's result for a hard stop."""

    CLEARED = "CLEARED"
    ADVERSE = "ADVERSE"


class EventType(StrEnum):
    RAISED = "RAISED"
    RESUME_AUTHORIZED = "RESUME_AUTHORIZED"
    RESUME_REFUSED = "RESUME_REFUSED"
    RESOLVED = "RESOLVED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"
    EXTERNAL_RESOLUTION_RECORDED = "EXTERNAL_RESOLUTION_RECORDED"
    EXTERNAL_RESOLUTION_REFUSED = "EXTERNAL_RESOLUTION_REFUSED"


class DecisionCode(StrEnum):
    """Why a resume or resolution was allowed or refused (returned to API callers)."""

    REVIEWER_AUTHORIZED = "reviewer_authorized"
    EXTERNAL_RESOLUTION_RECORDED = "external_resolution_recorded"
    ALREADY_RESOLVED = "already_resolved"
    HARD_STOP_NOT_OVERRIDABLE = "hard_stop_not_overridable"
    OVERRIDE_NOT_PERMITTED = "override_not_permitted"
    ACTOR_MISSING = "actor_missing"
    REVIEWER_ROLE_MISMATCH = "reviewer_role_mismatch"
    REVIEWER_NOT_AUTHORIZED = "reviewer_not_authorized"
    EVIDENCE_MISSING = "evidence_missing"
    CASE_CLOSED = "case_closed"
    EXTERNAL_RESOLUTION_NOT_APPLICABLE = "external_resolution_not_applicable"


_REFUSAL_PRECEDENCE: Final[tuple[DecisionCode, ...]] = (
    DecisionCode.HARD_STOP_NOT_OVERRIDABLE,
    DecisionCode.CASE_CLOSED,
    DecisionCode.OVERRIDE_NOT_PERMITTED,
    DecisionCode.EXTERNAL_RESOLUTION_NOT_APPLICABLE,
    DecisionCode.ACTOR_MISSING,
    DecisionCode.REVIEWER_ROLE_MISMATCH,
    DecisionCode.REVIEWER_NOT_AUTHORIZED,
    DecisionCode.EVIDENCE_MISSING,
)


# ------------------------------------------------------------------------------ policy


class HoldClassPolicyError(ValueError):
    """The hold-class policy data is malformed or relaxes a mandatory hard stop."""


class HoldRule(BaseModel):
    """How one reason code holds a workflow."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    hold_class: HoldClass
    severity: HoldSeverity
    override_permitted: bool
    required_evidence: tuple[str, ...] = Field(min_length=1)
    reviewer_role: str = Field(min_length=1)
    category: str = Field(min_length=1)
    source: str = Field(min_length=1)
    description: str = Field(min_length=1)

    @field_validator("required_evidence")
    @classmethod
    def _evidence_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        bad = [item for item in value if not _IDENTIFIER.match(item)]
        if bad:
            raise ValueError(f"evidence names must be snake_case identifiers: {bad}")
        if len(set(value)) != len(value):
            raise ValueError("required_evidence lists an item twice")
        return value

    @field_validator("reviewer_role")
    @classmethod
    def _role_name(cls, value: str) -> str:
        if not _IDENTIFIER.match(value):
            raise ValueError(f"reviewer_role must be a snake_case identifier: {value!r}")
        return value

    @model_validator(mode="after")
    def _hard_stop_is_never_overridable(self) -> HoldRule:
        if self.hold_class is HoldClass.HARD_STOP and self.override_permitted:
            raise ValueError("a HARD_STOP rule cannot permit an override")
        return self


class HoldClassPolicy(BaseModel):
    """The versioned reason-code → hold-rule map."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"]
    policy_version: str = Field(min_length=1)
    notes: tuple[str, ...] = ()
    unmapped: HoldRule
    reason_codes: dict[str, HoldRule]

    @model_validator(mode="after")
    def _fail_closed_invariants(self) -> HoldClassPolicy:
        if self.unmapped.hold_class is not HoldClass.HARD_STOP:
            raise ValueError("the rule for unmapped reason codes must be HARD_STOP")
        bad = sorted(code for code in self.reason_codes if not _REASON_CODE.match(code))
        if bad:
            raise ValueError(f"reason codes must be UPPER_SNAKE_CASE: {bad}")
        missing = sorted(MANDATORY_HARD_STOPS - set(self.reason_codes))
        if missing:
            raise ValueError(f"mandatory hard stops missing from the policy: {missing}")
        relaxed = sorted(
            code
            for code in MANDATORY_HARD_STOPS
            if self.reason_codes[code].hold_class is not HoldClass.HARD_STOP
        )
        if relaxed:
            raise ValueError(f"mandatory hard stops classified as reviewable: {relaxed}")
        return self

    def classify(self, reason_code: str) -> HoldClassification:
        rule = self.reason_codes.get(reason_code)
        if rule is None:
            return HoldClassification(
                reason_code=reason_code,
                rule=self.unmapped,
                mapped=False,
                policy_version=self.policy_version,
            )
        return HoldClassification(
            reason_code=reason_code, rule=rule, mapped=True, policy_version=self.policy_version
        )


@dataclass(frozen=True, slots=True)
class HoldClassification:
    reason_code: str
    rule: HoldRule
    mapped: bool
    policy_version: str


def parse_hold_class_policy(data: Mapping[str, Any]) -> HoldClassPolicy:
    """Validate policy data; raises :class:`HoldClassPolicyError` naming the defect."""
    try:
        return HoldClassPolicy.model_validate(dict(data))
    except ValidationError as exc:
        raise HoldClassPolicyError(f"invalid hold-class policy: {exc}") from exc


@cache
def load_hold_class_policy() -> HoldClassPolicy:
    """The policy shipped in ``plm_platform/policy/hold_classes.json`` (validated, cached)."""
    text = resources.files(POLICY_PACKAGE).joinpath(POLICY_FILE).read_text(encoding="utf-8")
    return parse_hold_class_policy(json.loads(text))


# ------------------------------------------------------------------ reviewer identity


class ReviewerDirectory(Protocol):
    """The injected source of which roles an actor holds. A request never asserts its own
    role into existence: the role it claims must be granted here. Real identity (an
    identity provider) replaces the static mapping later."""

    def roles_of(self, actor: str) -> frozenset[str]: ...


class StaticReviewerDirectory:
    """A fixed actor → roles mapping. Unknown actors hold no role."""

    def __init__(self, grants: Mapping[str, Iterable[str]]) -> None:
        self._grants: dict[str, frozenset[str]] = {
            actor: frozenset(roles) for actor, roles in grants.items()
        }

    def roles_of(self, actor: str) -> frozenset[str]:
        return self._grants.get(actor, frozenset())

    @classmethod
    def from_json_file(cls, path: str | Path) -> StaticReviewerDirectory:
        """Load ``{"actor": ["role", ...]}`` from a JSON file."""
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not all(
            isinstance(k, str) and isinstance(v, list) and all(isinstance(r, str) for r in v)
            for k, v in raw.items()
        ):
            raise ValueError(f"{path}: expected an object mapping actor to a list of roles")
        return cls(raw)


NO_REVIEWERS: Final[StaticReviewerDirectory] = StaticReviewerDirectory({})
"""The default directory: nobody holds a role, so no reviewable case can resume."""


# ------------------------------------------------------------------------------ tables

EXCEPTIONS_METADATA = sa.MetaData(schema=PLATFORM_SCHEMA)

exception_cases_table = sa.Table(
    "exception_cases",
    EXCEPTIONS_METADATA,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("origin_phase", sa.String(16), nullable=False),
    sa.Column("subject_ref", sa.String(256), nullable=False),
    sa.Column("reason_code", sa.String(128), nullable=False),
    sa.Column("severity", sa.String(16), nullable=False),
    sa.Column("hold_class", sa.String(32), nullable=False),
    sa.Column("override_permitted", sa.Boolean(), nullable=False),
    sa.Column("required_evidence_json", sa.JSON(), nullable=False),
    sa.Column("reviewer_role", sa.String(64), nullable=False),
    sa.Column("policy_version", sa.String(64), nullable=False),
    sa.Column("policy_mapped", sa.Boolean(), nullable=False),
    sa.Column("status", sa.String(16), nullable=False),
    sa.Column("resolution", sa.String(48), nullable=True),
    sa.Column("details_json", sa.JSON(), nullable=False),
    sa.Column("raised_by", sa.String(128), nullable=False),
    sa.Column("raised_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("closed_by", sa.String(128), nullable=True),
    sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    sa.UniqueConstraint(
        "origin_phase", "subject_ref", "reason_code", name="uq_exception_cases_subject_reason"
    ),
    sa.Index("ix_exception_cases_status", "status"),
)

exception_events_table = sa.Table(
    "exception_events",
    EXCEPTIONS_METADATA,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column(
        "case_id",
        sa.String(64),
        sa.ForeignKey(f"{PLATFORM_SCHEMA}.exception_cases.id"),
        nullable=False,
    ),
    sa.Column("seq", sa.Integer(), nullable=False),
    sa.Column("event_type", sa.String(48), nullable=False),
    sa.Column("decision_code", sa.String(64), nullable=True),
    sa.Column("actor", sa.String(128), nullable=False),
    sa.Column("role", sa.String(64), nullable=True),
    sa.Column("evidence_json", sa.JSON(), nullable=False),
    sa.Column("notes", sa.Text(), nullable=False),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("case_id", "seq", name="uq_exception_events_case_seq"),
)


# ------------------------------------------------------------------------------ domain


@dataclass(frozen=True, slots=True)
class ExceptionCase:
    id: str
    origin_phase: str
    subject_ref: str
    reason_code: str
    severity: HoldSeverity
    hold_class: HoldClass
    override_permitted: bool
    required_evidence: tuple[str, ...]
    reviewer_role: str
    policy_version: str
    policy_mapped: bool
    status: CaseStatus
    resolution: CaseResolution | None
    details: Mapping[str, Any]
    raised_by: str
    raised_at: datetime
    closed_by: str | None = None
    closed_at: datetime | None = None

    @property
    def is_open(self) -> bool:
        return self.status is CaseStatus.OPEN


@dataclass(frozen=True, slots=True)
class ExceptionEvent:
    id: str
    case_id: str
    seq: int
    event_type: EventType
    decision_code: str | None
    actor: str
    role: str | None
    evidence: Mapping[str, str]
    notes: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class RaiseResult:
    case: ExceptionCase
    created: bool


@dataclass(frozen=True, slots=True)
class ResumeDecision:
    """The answer to "may this workflow continue past this hold?"."""

    case_id: str
    reason_code: str
    hold_class: HoldClass
    allowed: bool
    code: DecisionCode
    message: str
    missing_evidence: tuple[str, ...] = field(default=())


class ExceptionCaseNotFoundError(LookupError):
    """No exception case has this id."""


class ResumeRefusedError(PermissionError):
    """A resume (or a closing decision) was refused; ``decision`` says why."""

    def __init__(self, decision: ResumeDecision) -> None:
        super().__init__(f"{decision.code}: {decision.message}")
        self.decision = decision


class ExternalResolutionRefusedError(PermissionError):
    """An external resolution could not be recorded; ``decision`` says why."""

    def __init__(self, decision: ResumeDecision) -> None:
        super().__init__(f"{decision.code}: {decision.message}")
        self.decision = decision


def most_severe_refusal(decisions: Iterable[ResumeDecision]) -> ResumeDecision | None:
    """The refusal a caller should report first (a hard stop outranks missing evidence)."""
    refused = [d for d in decisions if not d.allowed]
    if not refused:
        return None
    rank = {code: index for index, code in enumerate(_REFUSAL_PRECEDENCE)}
    return min(refused, key=lambda d: (rank.get(d.code, len(rank)), d.case_id))


# ------------------------------------------------------------------------------- queue


def case_id_for(origin_phase: str, subject_ref: str, reason_code: str) -> str:
    """Deterministic case id: one case per (origin, subject, reason code)."""
    material = f"{origin_phase}|{subject_ref}|{reason_code}".encode()
    return "exc_" + hashlib.sha256(material).hexdigest()[:32]


def _event_id(case_id: str, seq: int) -> str:
    return "exe_" + hashlib.sha256(f"{case_id}|{seq}".encode()).hexdigest()[:32]


def _aware(moment: datetime | None) -> datetime | None:
    """SQLite drops tzinfo on read; every stored instant is UTC."""
    if moment is None:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _clean_evidence(evidence: Mapping[str, str] | None) -> dict[str, str]:
    if not evidence:
        return {}
    return {
        str(key): str(value).strip()
        for key, value in sorted(evidence.items())
        if isinstance(value, str) and value.strip()
    }


class ExceptionQueue:
    """Raise, inspect and resolve holds in ``ops``. The caller owns the transaction.

    ``reviewers`` is the injected authorization source; the default grants nobody any
    role. ``policy`` defaults to the shipped hold-class policy.
    """

    def __init__(
        self,
        *,
        reviewers: ReviewerDirectory = NO_REVIEWERS,
        policy: HoldClassPolicy | None = None,
    ) -> None:
        self._reviewers = reviewers
        self._policy = policy if policy is not None else load_hold_class_policy()

    @property
    def policy(self) -> HoldClassPolicy:
        return self._policy

    def classify(self, reason_code: str) -> HoldClassification:
        return self._policy.classify(reason_code)

    # -- raise ---------------------------------------------------------------------------

    def raise_exception(
        self,
        session: Session,
        *,
        origin_phase: str,
        subject_ref: str,
        reason_code: str,
        raised_by: str,
        now: datetime,
        details: Mapping[str, Any] | None = None,
    ) -> RaiseResult:
        """Open a hold, or return the existing one for this (origin, subject, reason).

        Idempotent: a second raise returns the first case unchanged and writes nothing.
        A new occurrence of the same reason needs a new ``subject_ref``.
        """
        if origin_phase not in ORIGIN_PHASES:
            raise ValueError(f"unknown origin phase {origin_phase!r}")
        for name, value in (
            ("subject_ref", subject_ref),
            ("reason_code", reason_code),
            ("raised_by", raised_by),
        ):
            if not value or not value.strip():
                raise ValueError(f"{name} is required to raise an exception")
        now = require_aware(now)
        case_id = case_id_for(origin_phase, subject_ref, reason_code)
        existing = self.get(session, case_id)
        if existing is not None:
            return RaiseResult(case=existing, created=False)

        classification = self.classify(reason_code)
        rule = classification.rule
        if not classification.mapped:
            logger.warning(
                "exception reason code %r (origin %s) is not in hold-class policy %s; "
                "treated as HARD_STOP",
                reason_code,
                origin_phase,
                classification.policy_version,
            )
        stored_details = dict(details or {})
        if not classification.mapped:
            stored_details["policy_unmapped_reason_code"] = True
        try:
            with session.begin_nested():
                session.execute(
                    exception_cases_table.insert().values(
                        id=case_id,
                        origin_phase=origin_phase,
                        subject_ref=subject_ref,
                        reason_code=reason_code,
                        severity=rule.severity.value,
                        hold_class=rule.hold_class.value,
                        override_permitted=rule.override_permitted,
                        required_evidence_json=list(rule.required_evidence),
                        reviewer_role=rule.reviewer_role,
                        policy_version=classification.policy_version,
                        policy_mapped=classification.mapped,
                        status=CaseStatus.OPEN.value,
                        resolution=None,
                        details_json=stored_details,
                        raised_by=raised_by,
                        raised_at=now,
                        closed_by=None,
                        closed_at=None,
                    )
                )
                self._append_event(
                    session,
                    case_id,
                    EventType.RAISED,
                    actor=raised_by,
                    role=None,
                    decision_code=None,
                    evidence={},
                    notes=(
                        f"{rule.hold_class.value} under {classification.policy_version}"
                        + ("" if classification.mapped else " (unmapped reason code)")
                    ),
                    now=now,
                )
        except IntegrityError:
            # A concurrent raise won the insert; the case it wrote is the answer.
            existing = self.get(session, case_id)
            if existing is None:
                raise
            return RaiseResult(case=existing, created=False)
        created = self.get(session, case_id)
        assert created is not None
        return RaiseResult(case=created, created=True)

    # -- read ----------------------------------------------------------------------------

    def get(self, session: Session, case_id: str) -> ExceptionCase | None:
        row = (
            session.execute(
                sa.select(exception_cases_table).where(exception_cases_table.c.id == case_id)
            )
            .mappings()
            .first()
        )
        return None if row is None else _case_from_row(row)

    def require(self, session: Session, case_id: str) -> ExceptionCase:
        case = self.get(session, case_id)
        if case is None:
            raise ExceptionCaseNotFoundError(case_id)
        return case

    def cases_for_subject(
        self, session: Session, *, origin_phase: str, subject_ref: str
    ) -> list[ExceptionCase]:
        rows = (
            session.execute(
                sa.select(exception_cases_table)
                .where(
                    exception_cases_table.c.origin_phase == origin_phase,
                    exception_cases_table.c.subject_ref == subject_ref,
                )
                .order_by(exception_cases_table.c.reason_code)
            )
            .mappings()
            .all()
        )
        return [_case_from_row(row) for row in rows]

    def list_open(self, session: Session) -> list[ExceptionCase]:
        rows = (
            session.execute(
                sa.select(exception_cases_table)
                .where(exception_cases_table.c.status == CaseStatus.OPEN.value)
                .order_by(exception_cases_table.c.raised_at, exception_cases_table.c.id)
            )
            .mappings()
            .all()
        )
        return [_case_from_row(row) for row in rows]

    def history(self, session: Session, case_id: str) -> list[ExceptionEvent]:
        rows = (
            session.execute(
                sa.select(exception_events_table)
                .where(exception_events_table.c.case_id == case_id)
                .order_by(exception_events_table.c.seq)
            )
            .mappings()
            .all()
        )
        return [_event_from_row(row) for row in rows]

    # -- resume authorization -------------------------------------------------------------

    def authorize_resume(
        self,
        session: Session,
        case_id: str,
        *,
        actor: str,
        role: str | None,
        evidence: Mapping[str, str] | None,
        now: datetime,
    ) -> ResumeDecision:
        """The single check before anything signals a held workflow to continue.

        Records the decision (authorized or refused) with its actor and ``now``.
        """
        now = require_aware(now)
        case = self.require(session, case_id)
        cleaned = _clean_evidence(evidence)
        decision = self._evaluate_resume(case, actor=actor, role=role, evidence=cleaned)
        self._append_event(
            session,
            case.id,
            EventType.RESUME_AUTHORIZED if decision.allowed else EventType.RESUME_REFUSED,
            actor=actor or "<missing>",
            role=role,
            decision_code=decision.code.value,
            evidence=cleaned,
            notes=decision.message,
            now=now,
        )
        return decision

    def _evaluate_resume(
        self,
        case: ExceptionCase,
        *,
        actor: str,
        role: str | None,
        evidence: Mapping[str, str],
    ) -> ResumeDecision:
        def decide(
            allowed: bool, code: DecisionCode, message: str, missing: tuple[str, ...] = ()
        ) -> ResumeDecision:
            return ResumeDecision(
                case_id=case.id,
                reason_code=case.reason_code,
                hold_class=case.hold_class,
                allowed=allowed,
                code=code,
                message=f"{case.reason_code} ({case.hold_class.value}): {message}",
                missing_evidence=missing,
            )

        if case.status is CaseStatus.RESOLVED:
            if case.resolution is CaseResolution.CLEARED_BY_EXTERNAL_PROCESS:
                return decide(
                    True,
                    DecisionCode.EXTERNAL_RESOLUTION_RECORDED,
                    "cleared by the external process's recorded evidence",
                )
            return decide(True, DecisionCode.ALREADY_RESOLVED, "already resumed by a reviewer")
        if case.status is not CaseStatus.OPEN:
            return decide(False, DecisionCode.CASE_CLOSED, f"case is {case.status.value}")
        if case.hold_class is HoldClass.HARD_STOP:
            return decide(
                False,
                DecisionCode.HARD_STOP_NOT_OVERRIDABLE,
                "a hard stop is not resumable by any approve or override; it resolves only "
                "when the external process's evidence is recorded",
            )
        if not case.override_permitted:
            return decide(
                False, DecisionCode.OVERRIDE_NOT_PERMITTED, "policy permits no override"
            )
        if not actor or not actor.strip():
            return decide(False, DecisionCode.ACTOR_MISSING, "no reviewer identity given")
        if role != case.reviewer_role:
            return decide(
                False,
                DecisionCode.REVIEWER_ROLE_MISMATCH,
                f"requires role {case.reviewer_role!r}, request acts as {role!r}",
            )
        if case.reviewer_role not in self._reviewers.roles_of(actor):
            return decide(
                False,
                DecisionCode.REVIEWER_NOT_AUTHORIZED,
                f"{actor!r} does not hold role {case.reviewer_role!r}",
            )
        missing = tuple(item for item in case.required_evidence if item not in evidence)
        if missing:
            return decide(
                False,
                DecisionCode.EVIDENCE_MISSING,
                f"required evidence missing: {list(missing)}",
                missing,
            )
        return decide(
            True, DecisionCode.REVIEWER_AUTHORIZED, f"{actor!r} as {role!r} with evidence"
        )

    # -- closing decisions ----------------------------------------------------------------

    def resolve(
        self,
        session: Session,
        case_id: str,
        *,
        decision: ReviewDecision,
        actor: str,
        role: str | None,
        evidence: Mapping[str, str] | None,
        notes: str,
        now: datetime,
    ) -> ExceptionCase:
        """Close a case by a reviewer's decision.

        ``RESUME`` runs the same check as :meth:`authorize_resume` and raises
        :class:`ResumeRefusedError` (after recording the refusal) when it fails; a hard
        stop is always refused. ``REJECT`` and ``WITHDRAW`` never let a workflow continue,
        so they need only an identified actor, and only an open case can take them.
        """
        now = require_aware(now)
        case = self.require(session, case_id)
        cleaned = _clean_evidence(evidence)

        if decision is ReviewDecision.RESUME:
            verdict = self._evaluate_resume(case, actor=actor, role=role, evidence=cleaned)
            if not verdict.allowed:
                self._append_event(
                    session,
                    case.id,
                    EventType.RESUME_REFUSED,
                    actor=actor or "<missing>",
                    role=role,
                    decision_code=verdict.code.value,
                    evidence=cleaned,
                    notes=verdict.message,
                    now=now,
                )
                raise ResumeRefusedError(verdict)
            if case.status is CaseStatus.RESOLVED:
                return case
            return self._close(
                session,
                case,
                status=CaseStatus.RESOLVED,
                resolution=CaseResolution.RESUMED_BY_REVIEWER,
                event=EventType.RESOLVED,
                decision_code=verdict.code,
                actor=actor,
                role=role,
                evidence=cleaned,
                notes=notes,
                now=now,
            )

        if not actor or not actor.strip():
            self._refuse_close(session, case, DecisionCode.ACTOR_MISSING, "", role, now)
        if not case.is_open:
            self._refuse_close(
                session, case, DecisionCode.CASE_CLOSED, actor, role, now,
                f"case is {case.status.value}",
            )
        if decision is ReviewDecision.REJECT:
            return self._close(
                session,
                case,
                status=CaseStatus.REJECTED,
                resolution=CaseResolution.REJECTED_BY_REVIEWER,
                event=EventType.REJECTED,
                decision_code=None,
                actor=actor,
                role=role,
                evidence=cleaned,
                notes=notes,
                now=now,
            )
        return self._close(
            session,
            case,
            status=CaseStatus.WITHDRAWN,
            resolution=CaseResolution.WITHDRAWN,
            event=EventType.WITHDRAWN,
            decision_code=None,
            actor=actor,
            role=role,
            evidence=cleaned,
            notes=notes,
            now=now,
        )

    def record_external_resolution(
        self,
        session: Session,
        case_id: str,
        *,
        outcome: ExternalOutcome,
        evidence: Mapping[str, str],
        actor: str,
        role: str | None,
        notes: str,
        now: datetime,
    ) -> ExceptionCase:
        """Close a hard stop with the external process's evidence.

        The actor records what the external process decided; it must hold the case's
        ``reviewer_role`` and supply every ``required_evidence`` item. ``CLEARED`` resolves
        the case (the workflow may continue); ``ADVERSE`` rejects it.
        """
        now = require_aware(now)
        case = self.require(session, case_id)
        cleaned = _clean_evidence(evidence)

        def refuse(code: DecisionCode, message: str, missing: tuple[str, ...] = ()) -> NoReturn:
            verdict = ResumeDecision(
                case_id=case.id,
                reason_code=case.reason_code,
                hold_class=case.hold_class,
                allowed=False,
                code=code,
                message=f"{case.reason_code} ({case.hold_class.value}): {message}",
                missing_evidence=missing,
            )
            self._append_event(
                session,
                case.id,
                EventType.EXTERNAL_RESOLUTION_REFUSED,
                actor=actor or "<missing>",
                role=role,
                decision_code=code.value,
                evidence=cleaned,
                notes=verdict.message,
                now=now,
            )
            raise ExternalResolutionRefusedError(verdict)

        if case.hold_class is not HoldClass.HARD_STOP:
            refuse(
                DecisionCode.EXTERNAL_RESOLUTION_NOT_APPLICABLE,
                "a reviewable quarantine resolves through an authorized reviewer",
            )
        if not case.is_open:
            refuse(DecisionCode.CASE_CLOSED, f"case is {case.status.value}")
        if not actor or not actor.strip():
            refuse(DecisionCode.ACTOR_MISSING, "no identity recorded the external outcome")
        if role != case.reviewer_role:
            refuse(
                DecisionCode.REVIEWER_ROLE_MISMATCH,
                f"requires role {case.reviewer_role!r}, request acts as {role!r}",
            )
        if case.reviewer_role not in self._reviewers.roles_of(actor):
            refuse(
                DecisionCode.REVIEWER_NOT_AUTHORIZED,
                f"{actor!r} does not hold role {case.reviewer_role!r}",
            )
        missing = tuple(item for item in case.required_evidence if item not in cleaned)
        if missing:
            refuse(
                DecisionCode.EVIDENCE_MISSING,
                f"external-process evidence missing: {list(missing)}",
                missing,
            )
        cleared = outcome is ExternalOutcome.CLEARED
        return self._close(
            session,
            case,
            status=CaseStatus.RESOLVED if cleared else CaseStatus.REJECTED,
            resolution=(
                CaseResolution.CLEARED_BY_EXTERNAL_PROCESS
                if cleared
                else CaseResolution.REJECTED_BY_EXTERNAL_PROCESS
            ),
            event=EventType.EXTERNAL_RESOLUTION_RECORDED,
            decision_code=DecisionCode.EXTERNAL_RESOLUTION_RECORDED,
            actor=actor,
            role=role,
            evidence=cleaned,
            notes=f"outcome={outcome.value}; {notes}".rstrip("; "),
            now=now,
        )

    # -- internals ------------------------------------------------------------------------

    def _refuse_close(
        self,
        session: Session,
        case: ExceptionCase,
        code: DecisionCode,
        actor: str,
        role: str | None,
        now: datetime,
        message: str = "no reviewer identity given",
    ) -> NoReturn:
        verdict = ResumeDecision(
            case_id=case.id,
            reason_code=case.reason_code,
            hold_class=case.hold_class,
            allowed=False,
            code=code,
            message=f"{case.reason_code} ({case.hold_class.value}): {message}",
        )
        self._append_event(
            session,
            case.id,
            EventType.RESUME_REFUSED,
            actor=actor or "<missing>",
            role=role,
            decision_code=code.value,
            evidence={},
            notes=verdict.message,
            now=now,
        )
        raise ResumeRefusedError(verdict)

    def _close(
        self,
        session: Session,
        case: ExceptionCase,
        *,
        status: CaseStatus,
        resolution: CaseResolution,
        event: EventType,
        decision_code: DecisionCode | None,
        actor: str,
        role: str | None,
        evidence: Mapping[str, str],
        notes: str,
        now: datetime,
    ) -> ExceptionCase:
        result = session.execute(
            exception_cases_table.update()
            .where(
                exception_cases_table.c.id == case.id,
                exception_cases_table.c.status == CaseStatus.OPEN.value,
            )
            .values(
                status=status.value,
                resolution=resolution.value,
                closed_by=actor,
                closed_at=now,
            )
        )
        if getattr(result, "rowcount", 1) != 1:
            current = self.require(session, case.id)
            raise ResumeRefusedError(
                ResumeDecision(
                    case_id=case.id,
                    reason_code=case.reason_code,
                    hold_class=case.hold_class,
                    allowed=False,
                    code=DecisionCode.CASE_CLOSED,
                    message=(
                        f"{case.reason_code} ({case.hold_class.value}): closed concurrently "
                        f"as {current.status.value}"
                    ),
                )
            )
        self._append_event(
            session,
            case.id,
            event,
            actor=actor,
            role=role,
            decision_code=None if decision_code is None else decision_code.value,
            evidence=evidence,
            notes=notes,
            now=now,
        )
        return self.require(session, case.id)

    def _append_event(
        self,
        session: Session,
        case_id: str,
        event_type: EventType,
        *,
        actor: str,
        role: str | None,
        decision_code: str | None,
        evidence: Mapping[str, str],
        notes: str,
        now: datetime,
    ) -> None:
        last = session.execute(
            sa.select(sa.func.max(exception_events_table.c.seq)).where(
                exception_events_table.c.case_id == case_id
            )
        ).scalar()
        seq = 1 if last is None else int(last) + 1
        session.execute(
            exception_events_table.insert().values(
                id=_event_id(case_id, seq),
                case_id=case_id,
                seq=seq,
                event_type=event_type.value,
                decision_code=decision_code,
                actor=actor,
                role=role,
                evidence_json=dict(evidence),
                notes=notes,
                occurred_at=now,
            )
        )


def _case_from_row(row: sa.RowMapping) -> ExceptionCase:
    raised_at = _aware(row["raised_at"])
    assert raised_at is not None
    return ExceptionCase(
        id=row["id"],
        origin_phase=row["origin_phase"],
        subject_ref=row["subject_ref"],
        reason_code=row["reason_code"],
        severity=HoldSeverity(row["severity"]),
        hold_class=HoldClass(row["hold_class"]),
        override_permitted=bool(row["override_permitted"]),
        required_evidence=tuple(row["required_evidence_json"]),
        reviewer_role=row["reviewer_role"],
        policy_version=row["policy_version"],
        policy_mapped=bool(row["policy_mapped"]),
        status=CaseStatus(row["status"]),
        resolution=None if row["resolution"] is None else CaseResolution(row["resolution"]),
        details=dict(row["details_json"] or {}),
        raised_by=row["raised_by"],
        raised_at=raised_at,
        closed_by=row["closed_by"],
        closed_at=_aware(row["closed_at"]),
    )


def _event_from_row(row: sa.RowMapping) -> ExceptionEvent:
    occurred_at = _aware(row["occurred_at"])
    assert occurred_at is not None
    return ExceptionEvent(
        id=row["id"],
        case_id=row["case_id"],
        seq=int(row["seq"]),
        event_type=EventType(row["event_type"]),
        decision_code=row["decision_code"],
        actor=row["actor"],
        role=row["role"],
        evidence=dict(row["evidence_json"] or {}),
        notes=row["notes"],
        occurred_at=occurred_at,
    )
