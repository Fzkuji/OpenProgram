"""
Agentic Programming — Python and LLM co-execute functions.

    @agentic_function    Decorator. Records execution into Context tree.
    runtime.exec()       Calls the LLM. Auto-reads context, auto-records I/O.
    Context              Tree of execution records.
"""

from agentic.context import Context, get_context, get_root_context, init_root
from agentic.function import agentic_function
from agentic import runtime

__all__ = [
    "agentic_function",
    "runtime",
    "Context",
    "get_context",
    "get_root_context",
    "init_root",
]
