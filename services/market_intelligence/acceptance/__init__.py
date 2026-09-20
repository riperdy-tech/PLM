# services/market_intelligence/acceptance/__init__.py
"""§40's acceptance surface: the live smoke test and its first-class blocked outcome.

The blocked path (`blocked: pending credentials`) is the *acceptance criterion* of
PHASE-P1 until a human completes P0 §10's YouTube API project; it is never a skip,
never an exit-0, and never reported as a pass.
"""
