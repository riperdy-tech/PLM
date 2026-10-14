"""Cross-phase seam payloads: one definition per seam, used by producer and consumer.

See ADR-0025 and ``base.py`` for the rules every seam model follows.
"""

from .base import SCHEMA_VERSION_PATTERN as SCHEMA_VERSION_PATTERN
from .base import SeamModel as SeamModel
from .base import SeamRejection as SeamRejection
from .base import parse_seam as parse_seam
from .lineage import PLACEHOLDER_IDS as PLACEHOLDER_IDS
from .lineage import ExperimentLineage as ExperimentLineage
from .lineage import LineageId as LineageId

__all__ = [
    "PLACEHOLDER_IDS",
    "SCHEMA_VERSION_PATTERN",
    "ExperimentLineage",
    "LineageId",
    "SeamModel",
    "SeamRejection",
    "parse_seam",
]
