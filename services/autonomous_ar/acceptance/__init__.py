# services/autonomous_ar/acceptance/__init__.py
"""§114 P2.16's acceptance surface: the dry-run production smoke and its blocked reasons.

The successful outcome is an operator command (a live P1 plus the service token); every
missing precondition returns a named `blocked: …` string, never a silent pass. What this
phase cannot yet do (P9/P10 have no producer, §116 has no manual strategy to compare
against) is stated in `production_smoke`'s docstring, not hidden.
"""
