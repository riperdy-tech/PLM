"""Unit tests for P10 feature projection (Seam C8, P9 §155)."""

from __future__ import annotations

from analytics.domain.enums import ObservationWindow
from analytics.evaluation.feedback import evaluate_experiment_outcome
from analytics.projection.p10_features import build_p10_feature_view

from tests.support.analytics_db import analytics_db_session


def test_build_p10_feature_view_conforms_to_c8() -> None:
    with analytics_db_session() as session:
        outcome = evaluate_experiment_outcome(
            session,
            experiment_id="exp_synth_01",
            experiment_family_id="fam_synth_01",
            content_arm_id="arm_synth_01",
            content_genome_id="genome_synth_01",
            unit_id="video_synth_01",
            net_contribution_usd=143.21,
            views=85000,
            watch_time_minutes=612.4 * 60,
            window=ObservationWindow.W_7D,
        )

        view = build_p10_feature_view(
            outcome,
            views=85000,
            watch_time_hours=612.4,
            subscribers_gained=179,
            spotify_streams=1300,
            data_completeness=0.98,
            fidelity=0.96,
            contamination=False,
        )

        assert view.unit_id == "video_synth_01"
        assert view.experiment_id == "exp_synth_01"
        assert view.arm_id == "arm_synth_01"
        assert view.content_genome_id == "genome_synth_01"
        assert view.observation_window == "7d"
        assert view.maturity == "MATURE"

        assert view.primary_outcome["metric"] == "net_contribution"
        assert view.primary_outcome["value"] == 143.21
        assert view.primary_outcome["status"] == "REPORTED"

        assert view.secondary_outcomes["views"] == 85000
        assert view.secondary_outcomes["watch_time_hours"] == 612.4
        assert view.secondary_outcomes["subscribers_gained"] == 179
        assert view.secondary_outcomes["spotify_streams"] == 1300

        assert view.quality["data_completeness"] == 0.98
        assert view.quality["fidelity"] == 0.96
        assert view.quality["contamination"] is False
        assert view.quality["attribution_confidence"] == 1.0
