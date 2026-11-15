"""Run code on a chosen Runtime the way a configured Agent does."""
from __future__ import annotations


def run_on(runtime, function, /, *args, **kwargs):
    """Call ``function`` with ``runtime`` as the Runtime of the current call.

    Equivalent to calling it from an Agent configured with ``runtime``,
    without adding a graph node of its own.
    """
    from openprogram.runtime.agent.runtime_scope import execution_scope

    with execution_scope(runtime=runtime):
        return function(*args, **kwargs)
