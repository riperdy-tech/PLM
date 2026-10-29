"""B01: arm IDs must fit every consumer's persisted database column."""
from __future__ import annotations

import sqlalchemy as sa

from services.analytics.storage.models import ANALYTICS_METADATA
from services.distribution.storage.models import DISTRIBUTION_METADATA
from services.experiments.storage.models import EXPERIMENTS_METADATA
from services.market_intelligence.domain.enums import TaxonomyDimension as D
from services.market_intelligence.taxonomy.arms import generate_candidate_arms
from services.market_intelligence.taxonomy.validators import ClassificationDraft
from services.media_factory.storage.models import MEDIA_METADATA
from services.quality_gate.storage.models import QA_METADATA
from services.youtube_publishing.storage.models import YOUTUBE_METADATA


def test_v3_ids_fit_narrowest_downstream_arm_column() -> None:
    metadatas = (
        ANALYTICS_METADATA, DISTRIBUTION_METADATA, EXPERIMENTS_METADATA,
        MEDIA_METADATA, QA_METADATA, YOUTUBE_METADATA,
    )
    narrow = [
        (f"{table.fullname}.{column.name}", column.type.length)
        for metadata in metadatas
        for table in metadata.tables.values()
        for column in table.columns
        if column.name in {"arm_id", "content_arm_id"}
        and isinstance(column.type, sa.String)
        and column.type.length is not None
    ]
    assert narrow and min(size for _, size in narrow) == 64
    for profile in [
        {D.GENRE: ("lofi",), D.MOOD: ("calm",), D.USE_CASE: ("study_focus",)},
        {D.GENRE: ("jazz",), D.MOOD: ("dreamy",), D.USE_CASE: ("late_night_drive",)},
        {D.GENRE: ("rnb",), D.DURATION_BUCKET: ("45_75m",)},
    ]:
        draft = generate_candidate_arms(
            [ClassificationDraft(dimensions=profile, confidence=0.9)],
            min_support=1, taxonomy_version="p1-taxonomy-v3",
        )[0]
        assert len(draft.arm_id) <= min(size for _, size in narrow), draft.arm_id
