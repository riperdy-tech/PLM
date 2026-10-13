"""Test doubles that production code may also use in explicitly mocked modes.

They live in the package, not under ``tests/``, so no runtime module ever imports test
code. Nothing here talks to a network.
"""
