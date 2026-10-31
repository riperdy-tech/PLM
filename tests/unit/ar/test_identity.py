# tests/unit/ar/test_identity.py
"""ADR-0044: a brand's identity decides which P1 arms P2 may ever pick."""

from dataclasses import replace
from datetime import UTC, datetime

from services.autonomous_ar.config import load_ar_settings, production_settings
from services.autonomous_ar.decisioning.cycle import (
    CycleRequest,
    MarketSnapshot,
    run_allocation_cycle,
)
from services.autonomous_ar.domain.arm import ArmDimensions, ContentArm
from services.autonomous_ar.domain.enums import ArmLifecycleStatus, ReasonCode
from services.autonomous_ar.domain.policy import DEFAULT_POLICY
from services.autonomous_ar.policy.identity import (
    OUT_OF_IDENTITY_PREFIX,
    BrandIdentity,
    load_brand_identities,
)
from services.autonomous_ar.storage import repositories
from tests.support.ar_db import ar_session, create_ar_engine

NOW = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)
FULL = {name: 60.0 for name in
        ("demand", "growth", "competitor_velocity", "competition", "audience_quality",
         "cross_platform", "production_fit")}
FOCUS = BrandIdentity(
    version="brand-identity-test", use_cases=frozenset({"study_focus", "focus"}),
    genres=frozenset({"focus_electronic", "ambient"}), allow_unset_genre=True,
    excluded_vocal_modes=frozenset({"vocal"}),
)


def _arm(arm_id: str, *, genre: str | None, use_case: str,
         vocal_mode: str | None = None) -> ContentArm:
    return ContentArm(
        arm_id=arm_id,
        dimensions=ArmDimensions(genre=genre, use_cases=(use_case,), vocal_mode=vocal_mode),
        p1_score=70.0, p1_confidence=0.7, component_scores=dict(FULL), observation_count=5,
        source_count=2, as_of=NOW, score_version="p1-v1", freshness_seconds=0.0,
    )


def test_identity_admits_only_focus_use_case_focus_genre_and_no_vocals() -> None:
    def admits(**kw: object) -> bool:
        return FOCUS.admits(_arm("a", **kw).dimensions)  # type: ignore[arg-type]

    assert admits(genre="ambient", use_case="study_focus")
    assert admits(genre=None, use_case="focus")
    assert admits(genre="none", use_case="study_focus"), "P1 writes unset as 'none'"
    assert not admits(genre="lofi", use_case="study_focus"), "genre outside the identity"
    assert not admits(genre="ambient", use_case="sleep"), "use case outside the identity"
    assert not admits(genre="ambient", use_case="study_focus", vocal_mode="vocal")
    assert admits(genre="focus_electronic", use_case="none"), "the genre names the use"
    assert not admits(genre="none", use_case="none"), "no genre and no use case"
    assert not admits(genre="focus_electronic", use_case="sleep")
    assert not replace(FOCUS, allow_unset_genre=False).admits(
        _arm("a", genre=None, use_case="focus").dimensions)


def test_identity_file_declares_the_production_brand() -> None:
    identities = load_brand_identities()
    assert "neon_seoul" in identities
    assert production_settings(platform=None).brand_identities.keys() == identities.keys()
    assert load_ar_settings().brand_identities == {}, "unit settings stay unfiltered"


def _cycle(session: object, settings: object, *arms: ContentArm, as_of: datetime = NOW):
    request = CycleRequest(brand_id="neon_seoul", as_of=as_of, slot_count=2, budget=2.0,
                           policy_version="p2-v1")
    market = MarketSnapshot(arms=arms, as_of=as_of, source="live", score_version="p1-v1")
    return run_allocation_cycle(session, request, settings=settings,  # type: ignore[arg-type]
                                market=market, now=as_of)


def test_cycle_blocks_out_of_identity_arms_and_never_selects_them() -> None:
    settings = load_ar_settings(brand_identities={"neon_seoul": FOCUS})
    engine = create_ar_engine()
    with ar_session(engine) as session:
        repositories.insert_policy(session, DEFAULT_POLICY, brand_id="neon_seoul",
                                   created_at=NOW)
        result = _cycle(session, settings,
                        _arm("focus-1", genre="ambient", use_case="study_focus"),
                        _arm("rnb-1", genre="rnb", use_case="late_night_drive",
                             vocal_mode="vocal"))
        assert {d.arm_id for d in result.directives} == {"focus-1"}
        row = repositories.load_arm_state(session, "rnb-1")
        assert row is not None
        assert row["status"] == ArmLifecycleStatus.BLOCKED.value
        assert row["retirement_reason"] == f"{OUT_OF_IDENTITY_PREFIX}brand-identity-test"
        assert ReasonCode.IDENTITY_UNDECLARED.value not in result.warnings


def test_a_wider_identity_unblocks_only_identity_blocked_arms() -> None:
    engine = create_ar_engine()
    with ar_session(engine) as session:
        repositories.insert_policy(session, DEFAULT_POLICY, brand_id="neon_seoul",
                                   created_at=NOW)
        lofi = _arm("lofi-1", genre="lofi", use_case="study_focus")
        paused = _arm("paused-1", genre="ambient", use_case="study_focus")
        _cycle(session, load_ar_settings(brand_identities={"neon_seoul": FOCUS}), lofi, paused)
        repositories.set_arm_status(session, arm_id="paused-1",
                                    status=ArmLifecycleStatus.BLOCKED, now=NOW,
                                    reason="owner_block")
        wider = replace(FOCUS, genres=FOCUS.genres | {"lofi"})
        later = datetime(2026, 11, 2, 6, 0, tzinfo=UTC)
        _cycle(session, load_ar_settings(brand_identities={"neon_seoul": wider}), lofi, paused,
               as_of=later)
        assert repositories.load_arm_state(session, "lofi-1")["status"] != "blocked"  # type: ignore[index]
        assert repositories.load_arm_state(session, "paused-1")["status"] == "blocked"  # type: ignore[index]


def test_undeclared_brand_is_not_filtered_but_warned() -> None:
    engine = create_ar_engine()
    with ar_session(engine) as session:
        repositories.insert_policy(session, DEFAULT_POLICY, brand_id="neon_seoul",
                                   created_at=NOW)
        result = _cycle(session, load_ar_settings(),
                        _arm("rnb-1", genre="rnb", use_case="late_night_drive"))
        assert ReasonCode.IDENTITY_UNDECLARED.value in result.warnings
        assert {d.arm_id for d in result.directives} == {"rnb-1"}
