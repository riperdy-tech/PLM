"""P2's policy arithmetic: exploration quotas, evidence classes and seeding (P2.7).

Pure functions of the validated `PolicyConfig` — the three discrete allocation classes
are the contract (ADR-0023 #2); `exploration_fraction` is reporting only and no function
in this package branches on it. Deterministic throughout: the only entropy is the
caller-supplied, persisted-seed `random.Random`.
"""
