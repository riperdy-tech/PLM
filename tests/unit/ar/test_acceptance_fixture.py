# tests/unit/ar/test_acceptance_fixture.py
"""§97's fixture shape, its live five-cluster property, and seed idempotence.

The idempotence half is the P1 `UniqueViolation` lesson applied to the fixture itself: a
replayed seed must derive the same keys and leave the row counts unchanged.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session

from services.autonomous_ar.config import load_ar_settings
from services.autonomous_ar.decisioning.planner import cluster_snapshot
from services.autonomous_ar.storage import repositories
from services.autonomous_ar.storage.models import (
    ar_content_arm_state,
    ar_experiment_result,
    ar_posterior_update_log,
    ar_reward_observation,
)
from tests.fixtures.ar.scenario import NOW, build_acceptance_environment, seed_environment
from tests.support.ar_db import ar_session, create_ar_engine


def test_section_97_shape_is_exact() -> None:
    environment = build_acceptance_environment()
    assert len(environment.arms) == 20
    assert len({arm.dimensions.genre for arm in environment.arms}) == 3
    assert len({arm.dimensions.use_cases[0] for arm in environment.arms}) == 4
    assert len({arm.dimensions.language for arm in environment.arms}) == 3
    assert len(environment.blocked) == len(environment.retired) == len(environment.stale) == 1
    assert len(environment.mature) == 2 and len(environment.provisional) == 3
    assert len({arm.p1_confidence for arm in environment.arms}) >= 3, (
        "§97 asks for multiple confidence levels"
    )


def test_the_live_clustering_yields_exactly_five_clusters() -> None:
    """The five clusters are §97's, verified against the real `cluster_snapshot` — the
    plan's draft arithmetic made genre and language identical, collapsing five to three."""
    environment = build_acceptance_environment()
    clusters = cluster_snapshot(environment.arms,
                                threshold=load_ar_settings().similarity_threshold)
    assert len(clusters) == 20
    assert len(set(clusters.values())) == 5


def test_seed_environment_is_idempotent_and_marks_the_excluded_arms() -> None:
    engine = create_ar_engine()
    environment = build_acceptance_environment()
    with ar_session(engine) as session:
        seed_environment(session, environment, now=NOW)
        session.commit()
        first = _row_counts(session)
        seed_environment(session, environment, now=NOW)  # the replay
        session.commit()
        assert _row_counts(session) == first, "a replayed seed writes no second row"
        states = {row["arm_id"]: row["status"] for row in repositories.load_arm_states(session)}
        assert states[environment.blocked[0]] == "blocked"
        assert states[environment.retired[0]] == "retired"
        posterior = repositories.load_posterior(session, environment.mature[0])
        assert posterior is not None and posterior.observation_count == 2
        assert first["updates"] == 10, "§97's ten experiments applied exactly once each"
        assert first["experiments"] == 10 and first["observations"] == 10


def _row_counts(session: Session) -> dict[str, int]:
    tables = {"states": ar_content_arm_state, "observations": ar_reward_observation,
              "experiments": ar_experiment_result, "updates": ar_posterior_update_log}
    return {name: session.execute(sa.select(sa.func.count()).select_from(table)).scalar_one()
            for name, table in tables.items()}
