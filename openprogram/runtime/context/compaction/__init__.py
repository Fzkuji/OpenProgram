"""Compaction: fitting a conversation into the model's context window.

- ``summarize``: whole-turn summaries with token budgeting, using the
  prompts in ``prompts`` and ``bounded_summary`` for over-long text.
- ``request``: budgeting one provider request without rewriting history.
- ``view``: the frozen DAG input a compaction works from.
- ``reactive``: recovering when the provider reports a context overflow.
- ``hold``: pausing automatic compaction that keeps failing.

The engine (``runtime.context.engine``) decides when each one runs;
``docs/reference/design/context/compaction.html`` describes the design.
"""
