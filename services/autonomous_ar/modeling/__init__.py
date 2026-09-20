"""P2's deterministic models: structured similarity (P2.4) and priors (P2.5).

Every function here is a pure transform of its inputs plus a version string — no clock,
no `uuid4`, no iteration-order dependence — so replaying a cycle reproduces its numbers.
"""
