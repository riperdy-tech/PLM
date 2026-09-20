"""Shared data contracts for the PLM platform (imported by every service).

Vocabulary and shapes only — no phase business logic, and no dependency on FastAPI,
SQLAlchemy, Temporal or ``services/``.
"""

from .enums import AllocationType as AllocationType
from .enums import ChannelActivationState as ChannelActivationState
from .enums import CommercialUseEligibility as CommercialUseEligibility
from .enums import DetectorResult as DetectorResult
from .enums import GateDisposition as GateDisposition
from .enums import GateOutcome as GateOutcome
from .enums import GateOverrideStatus as GateOverrideStatus
from .enums import ObservationWindow as ObservationWindow
from .enums import PolicyAction as PolicyAction
from .enums import ProvenanceStatus as ProvenanceStatus
from .enums import QualityStatus as QualityStatus
from .enums import ReviewResult as ReviewResult
from .enums import SourceFreshness as SourceFreshness
from .enums import from_exploration_level as from_exploration_level
from .enums import to_exploration_level as to_exploration_level
from .envelopes import CurrencyCode as CurrencyCode
from .envelopes import Envelope as Envelope
from .envelopes import Page as Page
from .errors import ErrorShape as ErrorShape
from .events import EventEnvelope as EventEnvelope
from .ids import VERSIONED_ID_PATTERN as VERSIONED_ID_PATTERN
from .ids import BrandId as BrandId
from .ids import BrandScoped as BrandScoped
from .ids import format_versioned_id as format_versioned_id
from .ids import is_versioned_id as is_versioned_id
from .ids import parse_versioned_id as parse_versioned_id
from .tri import Tri as Tri
from .tri import TriCoercionError as TriCoercionError
from .tri import TriState as TriState
from .tri import TriStateError as TriStateError

__all__ = [
    "VERSIONED_ID_PATTERN",
    "AllocationType",
    "BrandId",
    "BrandScoped",
    "ChannelActivationState",
    "CommercialUseEligibility",
    "CurrencyCode",
    "DetectorResult",
    "Envelope",
    "ErrorShape",
    "EventEnvelope",
    "GateDisposition",
    "GateOutcome",
    "GateOverrideStatus",
    "ObservationWindow",
    "Page",
    "PolicyAction",
    "ProvenanceStatus",
    "QualityStatus",
    "ReviewResult",
    "SourceFreshness",
    "Tri",
    "TriCoercionError",
    "TriState",
    "TriStateError",
    "format_versioned_id",
    "from_exploration_level",
    "is_versioned_id",
    "parse_versioned_id",
    "to_exploration_level",
]
