# services/market_intelligence/reports/__init__.py
"""§31's daily report: the ten sections, in machine- and human-readable form.

`market_snapshot.build_snapshot` reads stored scores and observations; nothing in
this package computes a new metric. `exports.render_machine`/`render_markdown`
render the same object; `exports.store_report`/`store_markdown` write the two
artifacts under the `market/` prefix (R13/C9).
"""
