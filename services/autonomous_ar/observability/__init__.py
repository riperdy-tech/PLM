"""§87/§88/§94/§109: counters, alerts, the structured cycle log and explanations (P2.14).

Everything here is a thin, deterministic layer over structured data: counters live on the
platform registry, the cycle log is one line per finished cycle, alerts are codes plus a
warning record, and the explanation is a template — no statistics, no LLM, no clock.
"""
