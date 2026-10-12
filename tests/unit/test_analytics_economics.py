"""Unit tests for financial ledger arithmetic and unit economics (P9 §19, §20, §21, §23)."""

from __future__ import annotations

from analytics.domain.enums import PaybackStatus
from analytics.economics.calculator import compute_unit_economics


def test_standard_profitable_unit_economics() -> None:
    econ = compute_unit_economics(
        "vid_001",
        "video",
        gross_revenue_usd=100.0,
        allocated_cost_usd=25.0,
        views=20000,
        daily_revenue_rate_usd=5.0,
    )
    assert econ.net_contribution_usd == 75.0
    assert econ.profit_margin == 0.75
    assert econ.roi == 3.0  # 75 / 25
    assert econ.rpm_usd == 5.0  # (100 / 20000) * 1000
    assert econ.payback_status == PaybackStatus.REPAID
    assert econ.payback_days == 5  # 25 / 5


def test_unprofitable_unit_economics() -> None:
    econ = compute_unit_economics(
        "vid_002",
        "video",
        gross_revenue_usd=10.0,
        allocated_cost_usd=50.0,
        views=5000,
    )
    assert econ.net_contribution_usd == -40.0
    assert econ.profit_margin == -4.0
    assert econ.roi == -0.8
    assert econ.payback_status == PaybackStatus.NOT_REPAID
    assert econ.payback_days is None


def test_zero_revenue_and_zero_cost_edge_cases() -> None:
    # Zero cost
    econ_free = compute_unit_economics(
        "vid_003",
        "video",
        gross_revenue_usd=15.0,
        allocated_cost_usd=0.0,
    )
    assert econ_free.net_contribution_usd == 15.0
    assert econ_free.profit_margin == 1.0
    assert econ_free.roi == 1.0
    assert econ_free.payback_status == PaybackStatus.NOT_APPLICABLE

    # Zero revenue, zero cost
    econ_zero = compute_unit_economics(
        "vid_004",
        "video",
        gross_revenue_usd=0.0,
        allocated_cost_usd=0.0,
    )
    assert econ_zero.net_contribution_usd == 0.0
    assert econ_zero.profit_margin == 0.0
    assert econ_zero.roi == 0.0


def test_dsp_revenue_per_stream() -> None:
    econ_dsp = compute_unit_economics(
        "trk_001",
        "track",
        gross_revenue_usd=400.0,
        allocated_cost_usd=50.0,
        streams=100000,
    )
    assert econ_dsp.revenue_per_stream_usd == 0.004
