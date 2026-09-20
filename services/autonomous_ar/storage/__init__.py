"""The `ar` schema's SQLAlchemy Core layer (P2.3).

`models.py` is the single declaration of the fifteen P2 tables; `repositories.py`
holds the row-level helpers every later P2 task writes through. Ids are derived
(`ids.deterministic_id`), never minted.
"""
