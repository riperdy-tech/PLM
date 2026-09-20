"""Phase P1: market intelligence service.

Public entry points: `config.load_market_settings` (P1 knobs) and the `domain`
package (vocabulary, value objects, entities). The HTTP app (`api.app.create_app`),
job registrations (`jobs.REGISTRATIONS`) and CLI implementations (`cli`) are added
by their own P1 tasks.
"""
