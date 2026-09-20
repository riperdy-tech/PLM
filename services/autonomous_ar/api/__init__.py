"""The `/ar` service surface (§71): FastAPI app, fail-closed auth, C2 directives (P2.13).

Every write path here goes through the same engine functions the scheduled job uses;
the API adds transport, validation and the ADR-0024 serving rule — never a second
allocation path.
"""
