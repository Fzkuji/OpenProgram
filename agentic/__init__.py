"""
Agentic Programming — a programming paradigm where Python and LLM co-execute functions.

Three components:
    @agentic_function    Decorator. Tracks execution + controls context injection.
    runtime.exec()       Calls the LLM. Auto-records I/O to Context.
    Context              Tree of execution records.

Minimal example:
    from agentic import agentic_function, runtime

    @agentic_function
    def observe(task):
        '''Look at the screen and describe what you see.'''
        img = take_screenshot()
        return runtime.exec(prompt=observe.__doc__, input={"task": task}, images=[img])

    @agentic_function(depth=1, siblings=3, decay=True)
    def observe_in_loop(task):
        '''Repeated observation with auto-decaying context.'''
        ...
"""

from agentic.context import (
    Context, ContextPolicy, get_context, get_root_context, init_root,
    ORCHESTRATOR, PLANNER, WORKER, LEAF, FOCUSED,
)
from agentic.function import agentic_function
from agentic import runtime

__all__ = [
    "agentic_function",
    "runtime",
    "Context",
    "ContextPolicy",
    "get_context",
    "get_root_context",
    "init_root",
    # Preset policies (can pass to context_policy= or use as reference)
    "ORCHESTRATOR",
    "PLANNER",
    "WORKER",
    "LEAF",
    "FOCUSED",
]
