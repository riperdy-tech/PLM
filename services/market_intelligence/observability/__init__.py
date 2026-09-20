# services/market_intelligence/observability/__init__.py
"""P1's observability surface: run summaries, Prometheus counters, run log lines.

This package never installs a second logging stack or a second metrics registry:
counters bind to `plm_platform.observability.METRICS_REGISTRY` and the run line goes
through `logging.getLogger("plm.market.run")` into the platform JSON handler.
"""
