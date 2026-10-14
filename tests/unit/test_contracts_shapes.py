"""Response and event shapes: ErrorShape (P3 §81), Envelope/Page (P9 §89, P1 §25), EventEnvelope (P3 §63)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from plm_contracts import (
    Envelope,
    ErrorShape,
    EventEnvelope,
    Page,
    QualityStatus,
    SourceFreshness,
    Tri,
)
from pydantic import ValidationError

AS_OF = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


class TestErrorShape:
    def test_exactly_the_p3_81_fields(self) -> None:
        assert set(ErrorShape.model_fields) == {"code", "field_paths", "message"}

    def test_p3_81_example_round_trips(self) -> None:
        error = ErrorShape(
            code="CONTRADICTORY_CONSTRAINTS",
            field_paths=["genome.vocal_mode", "track_specs[0].vocals_required"],
            message="Instrumental genome conflicts with mandatory vocal track specification.",
        )
        assert ErrorShape.model_validate_json(error.model_dump_json()) == error

    def test_field_paths_defaults_to_empty(self) -> None:
        assert ErrorShape(code="X", message="m").field_paths == []

    def test_blank_code_or_message_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ErrorShape(code="", message="m")
        with pytest.raises(ValidationError):
            ErrorShape(code="X", message="")

    def test_extra_fields_are_refused(self) -> None:
        with pytest.raises(ValidationError):
            ErrorShape(code="X", message="m", detail="free-form strings are not the contract")


class TestEnvelope:
    def test_exactly_the_p9_89_fields(self) -> None:
        assert set(Envelope[float].model_fields) == {
            "as_of",
            "source_freshness",
            "quality_status",
            "metric_version",
            "currency",
            "observation_count",
            "data",
        }

    def test_wraps_a_payload_with_the_required_metric_semantics(self) -> None:
        envelope = Envelope[float](
            as_of=AS_OF,
            source_freshness=SourceFreshness.FRESH,
            quality_status=QualityStatus.VALID,
            metric_version="revenue_v2",
            currency="USD",
            data=1.5,
        )
        assert envelope.data == 1.5
        assert envelope.observation_count is None

    def test_non_monetary_payloads_use_currency_none_not_a_guess(self) -> None:
        envelope = Envelope[float](
            as_of=AS_OF,
            source_freshness=SourceFreshness.FRESH,
            quality_status=QualityStatus.VALID,
            metric_version="p1-v1",
            currency=None,
            data=0.5,
        )
        assert envelope.currency is None

    def test_currency_shape_is_validated(self) -> None:
        for bad in ("usd", "US", "USDD", "U$D"):
            with pytest.raises(ValidationError):
                Envelope[float](
                    as_of=AS_OF,
                    source_freshness=SourceFreshness.FRESH,
                    quality_status=QualityStatus.VALID,
                    metric_version="revenue_v2",
                    currency=bad,
                    data=1.0,
                )

    def test_naive_timestamps_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Envelope[float](
                as_of=datetime(2026, 9, 20, 12, 0),
                source_freshness=SourceFreshness.FRESH,
                quality_status=QualityStatus.VALID,
                metric_version="revenue_v2",
                currency="USD",
                data=1.0,
            )

    def test_observation_count_of_zero_is_a_real_count(self) -> None:
        # P9 §58: ZERO is a first-class value. A fresh aggregate with no source rows must
        # still be able to carry the count — rejecting 0 would force omission ("not
        # applicable") or a fabricated count, the exact collapses this contract prevents.
        envelope = Envelope[int](
            as_of=AS_OF,
            source_freshness=SourceFreshness.FRESH,
            quality_status=QualityStatus.VALID,
            metric_version="revenue_v2",
            currency="KRW",
            observation_count=0,
            data=0,
        )
        assert envelope.observation_count == 0

    def test_negative_observation_counts_are_still_refused(self) -> None:
        with pytest.raises(ValidationError):
            Envelope[int](
                as_of=AS_OF,
                source_freshness=SourceFreshness.FRESH,
                quality_status=QualityStatus.VALID,
                metric_version="revenue_v2",
                currency="KRW",
                observation_count=-1,
                data=1,
            )

    def test_an_absent_observation_count_is_distinguishable_from_a_zero_count(self) -> None:
        absent = Envelope[int](
            as_of=AS_OF,
            source_freshness=SourceFreshness.FRESH,
            quality_status=QualityStatus.VALID,
            metric_version="revenue_v2",
            currency="KRW",
            data=1,
        )
        verified_zero = Envelope[int](
            as_of=AS_OF,
            source_freshness=SourceFreshness.FRESH,
            quality_status=QualityStatus.VALID,
            metric_version="revenue_v2",
            currency="KRW",
            observation_count=0,
            data=1,
        )
        assert absent.observation_count is None
        assert verified_zero.observation_count == 0
        assert absent != verified_zero
        assert '"observation_count":null' in absent.model_dump_json()
        assert '"observation_count":0' in verified_zero.model_dump_json()

    def test_round_trips_json(self) -> None:
        envelope = Envelope[float](
            as_of=AS_OF,
            source_freshness=SourceFreshness.ACCEPTABLE,
            quality_status=QualityStatus.ESTIMATED,
            metric_version="revenue_v2",
            currency="USD",
            observation_count=3,
            data=41.2,
        )
        assert Envelope[float].model_validate_json(envelope.model_dump_json()) == envelope

    def test_a_tri_state_payload_keeps_its_state_inside_the_envelope(self) -> None:
        envelope = Envelope[Tri[float]](
            as_of=AS_OF,
            source_freshness=SourceFreshness.STALE,
            quality_status=QualityStatus.PARTIAL,
            metric_version="revenue_v2",
            currency="USD",
            data=Tri.unknown(),
        )
        revived = Envelope[Tri[float]].model_validate_json(envelope.model_dump_json())
        assert revived.data.is_unknown
        assert revived.data != Tri.known(0)


class TestPage:
    def test_exactly_the_pagination_fields(self) -> None:
        assert set(Page[float].model_fields) == {"items", "next_cursor", "limit", "total"}

    def test_cursor_is_required_even_when_null(self) -> None:
        with pytest.raises(ValidationError):
            Page[float](items=[])
        page = Page[float](items=[1.0, 2.0], next_cursor=None)
        assert page.next_cursor is None
        assert page.items == [1.0, 2.0]

    def test_limit_and_total_bounds(self) -> None:
        assert Page[float](items=[], next_cursor="c", limit=20).limit == 20
        assert Page[float](items=[], next_cursor=None, total=0).total == 0
        with pytest.raises(ValidationError):
            Page[float](items=[], next_cursor=None, limit=0)
        with pytest.raises(ValidationError):
            Page[float](items=[], next_cursor=None, total=-1)

    def test_round_trips_json(self) -> None:
        page = Page[str](items=["a"], next_cursor="cursor-2", limit=50, total=12)
        assert Page[str].model_validate_json(page.model_dump_json()) == page

    def test_page_sits_inside_an_envelope(self) -> None:
        envelope = Envelope[Page[str]](
            as_of=AS_OF,
            source_freshness=SourceFreshness.FRESH,
            quality_status=QualityStatus.VALID,
            metric_version="p1-v1",
            currency=None,
            data=Page[str](items=["arm_1"], next_cursor=None),
        )
        revived = Envelope[Page[str]].model_validate_json(envelope.model_dump_json())
        assert revived.data.items == ["arm_1"]


class TestEventEnvelope:
    def test_exactly_the_p3_63_fields_plus_versioning_and_idempotency(self) -> None:
        assert set(EventEnvelope.model_fields) == {
            "event_id",
            "experiment_id",
            "event_type",
            "occurred_at",
            "actor",
            "payload",
            "software_version",
            "event_version",
            "idempotency_key",
        }

    def test_minimal_event_uses_version_one_and_an_empty_payload(self) -> None:
        event = EventEnvelope(
            event_id="evt_1",
            event_type="EXPERIMENT_CREATED",
            occurred_at=AS_OF,
            actor="p3.worker",
            software_version="p3-v1",
        )
        assert event.event_version == "1"
        assert event.payload == {}
        assert event.experiment_id is None
        assert event.idempotency_key is None

    def test_experiment_scoped_event_round_trips(self) -> None:
        event = EventEnvelope(
            event_id="evt_2",
            experiment_id="exp_001",
            event_type="PRODUCTION_STARTED",
            occurred_at=AS_OF,
            actor="p3.worker",
            payload={"package_id": "pkg_9"},
            software_version="p3-v1",
            idempotency_key="idem-2",
        )
        revived = EventEnvelope.model_validate_json(event.model_dump_json())
        assert revived == event
        assert revived.payload["package_id"] == "pkg_9"

    def test_flat_extra_fields_are_refused(self) -> None:
        with pytest.raises(ValidationError):
            EventEnvelope(
                event_id="evt_3",
                event_type="music_candidate_ready_for_p5",
                occurred_at=AS_OF,
                actor="p4.worker",
                software_version="p4-v1",
                candidate_id="candidate-123",  # type: ignore[call-arg]
            )

    def test_events_are_append_only_so_instances_are_frozen(self) -> None:
        event = EventEnvelope(
            event_id="evt_4",
            event_type="EXPERIMENT_CLOSED",
            occurred_at=AS_OF,
            actor="p3.worker",
            software_version="p3-v1",
        )
        with pytest.raises(ValidationError):
            event.actor = "someone-else"

    def test_naive_occurred_at_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EventEnvelope(
                event_id="evt_5",
                event_type="EXPERIMENT_CLOSED",
                occurred_at=datetime(2026, 9, 20),
                actor="p3.worker",
                software_version="p3-v1",
            )
