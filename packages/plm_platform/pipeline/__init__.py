"""The PART XIV pipeline workflow (R3): durable states, persistence and activities.

Modules (import them by full path; this package keeps its import light because the
Temporal workflow sandbox re-imports it):

* :mod:`.states`: ``PipelineState``, ``Step`` and the P27 transition rules.
* :mod:`.types`: the dataclasses exchanged between the workflow and its activities.
* :mod:`.store`: ``ops.pipeline_runs`` and the append-only ``ops.pipeline_transitions``.
* :mod:`.activities`: the ``PipelineActivities`` protocol, the platform operations over
  ``ops`` and the Temporal adapters.
* :mod:`.workflow`: ``PipelineWorkflow``.
* :mod:`.mock`: controllable phase steps for tests; :mod:`.http`: phase steps over HTTP.
* :mod:`.worker`: the deployed worker's registrations (``pipeline`` task queue).
"""
