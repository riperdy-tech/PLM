"""P4 refuses a prompt that names a protected artist before any provider call (R4)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from plm_contracts.seams.c3 import TrackSpecPayload
from plm_platform.clock import FixedClock

from services.music_factory.generation.engine import GenerationEngine
from services.music_factory.generation.planner import plan_generation
from services.music_factory.generation.prompts import (
    ProtectedArtistPromptRefused,
    build_generation_prompt,
)
from services.music_factory.ingestion.p3_adapter import adapt_track_spec
from services.music_factory.providers.adapters import get_provider_adapter
from services.music_factory.storage.artifacts import InMemoryArtifactStorage
from services.music_factory.storage.repositories import list_generation_attempts
from tests.support.music_db import music_db_session
from tests.support.seams import track_spec_fields

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _spec(**preferences: Any) -> Any:
    bounded = {"bpm": 84, "key_profile": None, "mood": ["melancholic"],
               "use_case": ["late_night_drive"]}
    bounded.update(preferences)
    return adapt_track_spec(
        TrackSpecPayload.build(**track_spec_fields(bounded_preferences=bounded)).to_wire()
    )


class _SpyProvider:
    """Wraps the mock provider and counts every generate() call."""

    def __init__(self) -> None:
        self._inner = get_provider_adapter("mock")
        self.calls = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def generate(self, request: Any) -> Any:
        self.calls += 1
        return self._inner.generate(request)


def test_clean_prompt_builds() -> None:
    """Proves: screening does not refuse an ordinary spec."""
    assert "RNB track" in build_generation_prompt(_spec()).prompt


@pytest.mark.parametrize(
    ("delta", "code"),
    [
        ({"extra_instruction": "vocals like Taylor Swift"}, "PROTECTED_ARTIST_REFERENCE_DETECTED"),
        ({"additional_tags": ["the-weeknd"]}, "PROTECTED_ARTIST_REFERENCE_DETECTED"),
        ({"extra_instruction": "make it an AI cover"}, "AI_POLICY_CONFLICT"),
    ],
)
def test_prompt_naming_a_protected_artist_is_refused_with_its_reason_code(
    delta: dict[str, Any], code: str
) -> None:
    """Proves: a name or impersonation request in the prompt or a style tag refuses the
    build with the shared policy's reason code; no prompt object is returned."""
    with pytest.raises(ProtectedArtistPromptRefused) as refused:
        build_generation_prompt(_spec(), variant_delta=delta)
    assert code in refused.value.reason_codes
    assert refused.value.policy_version == "protected-artists-v1"


def test_protected_artist_in_the_spec_never_reaches_the_provider() -> None:
    """Proves: the refusal happens before anything is submitted (the provider's generate()
    is never called, no attempt is recorded), the plan ends FAILED rather than stuck
    RUNNING, and the P29 reason opens a hard-stop hold on the plan."""
    from plm_platform.exceptions import ExceptionQueue

    from services.music_factory.domain.enums import PlanStatus
    from services.music_factory.storage.repositories import get_generation_plan

    spec = _spec(mood=["melancholic", "Billie Eilish whisper"])
    plan, variants = plan_generation(spec, created_at=NOW)
    spy = _SpyProvider()
    engine = GenerationEngine(clock=FixedClock(NOW), storage=InMemoryArtifactStorage())
    with music_db_session() as session:
        result = engine.execute_plan(
            session, plan=plan, variants=variants, spec=spec,
            provider_adapter=spy,  # type: ignore[arg-type]
        )
        assert result.status is PlanStatus.FAILED
        assert "PROTECTED_ARTIST_REFERENCE_DETECTED" in result.errors[0]
        assert list_generation_attempts(session, plan.plan_id) == []
        stored = get_generation_plan(session, plan.plan_id)
        assert stored is not None and stored.status is PlanStatus.FAILED
        (hold,) = ExceptionQueue().list_open(session)
        assert hold.reason_code == "PROTECTED_ARTIST_REFERENCE_DETECTED"
        assert hold.hold_class.value == "HARD_STOP"
        assert hold.subject_ref == f"music.generation_plan/{plan.plan_id}"
    assert spy.calls == 0


def test_variant_delta_cannot_smuggle_an_artist_past_the_builder() -> None:
    """Proves: the screen runs on the final prompt and tags, after every variant delta
    (here a genre override that lands in both) is applied."""
    with pytest.raises(ProtectedArtistPromptRefused):
        build_generation_prompt(_spec(), variant_delta={"override_genre": "eminem"})
