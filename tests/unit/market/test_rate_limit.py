import pytest

from services.market_intelligence.collection.rate_limit import (
    QuotaBudget,
    QuotaExceededError,
    RateLimiter,
    RequestBudget,
    RequestBudgetExceededError,
)


def test_quota_budget_refuses_the_call_that_would_exceed_it() -> None:
    budget = QuotaBudget(limit_units=150)
    budget.consume(100, operation="search.list")
    assert budget.remaining == 50
    with pytest.raises(QuotaExceededError) as excinfo:
        budget.consume(100, operation="search.list")
    assert "search.list" in str(excinfo.value)
    assert budget.used == 100  # a refused consumption changes nothing


def test_request_budget_counts_calls_not_units() -> None:
    budget = RequestBudget(limit_requests=2)
    budget.consume(operation="videos.list")
    budget.consume(operation="channels.list")
    with pytest.raises(RequestBudgetExceededError):
        budget.consume(operation="videos.list")


def test_a_released_attempt_gives_its_units_back() -> None:
    budget = QuotaBudget(limit_units=150)
    budget.consume(100, operation="search.list")
    budget.release(100, operation="search.list")
    assert budget.used == 0
    assert budget.remaining == 150
    with pytest.raises(ValueError, match="negative"):
        budget.release(-1, operation="search.list")
    with pytest.raises(ValueError, match="only 0 are charged"):
        budget.release(1, operation="search.list")


def test_a_released_request_gives_its_count_back() -> None:
    budget = RequestBudget(limit_requests=2)
    budget.consume(operation="videos.list")
    budget.release(operation="videos.list")
    assert budget.used == 0
    with pytest.raises(ValueError, match="none is charged"):
        budget.release(operation="videos.list")


def test_rate_limiter_computes_its_wait_from_the_injected_clock() -> None:
    now = {"t": 0.0}
    naps: list[float] = []
    limiter = RateLimiter(
        requests_per_second=2.0,
        clock=lambda: now["t"],
        sleep=lambda seconds: (
            naps.append(seconds),
            now.__setitem__("t", now["t"] + seconds),
        ),
    )
    assert limiter.wait_time() == 0.0
    limiter.acquire()
    limiter.acquire()
    assert limiter.wait_time() == pytest.approx(0.5)  # 2 rps → 0.5s spacing
    limiter.acquire()
    assert naps and naps[-1] == pytest.approx(0.5)
