"""
Agentic Programming — a programming paradigm where Python and LLM co-execute functions.

Core exports:
    agentic_function    Decorator to mark an Agentic Function (auto context tracking)
    runtime             Agentic Runtime module — use runtime.exec() to call LLM
    Context             Execution record for one function
    get_context         Get current Context inside a function
    get_root_context    Get the root of the Context tree
"""

from agentic.context import Context, get_context, get_root_context, init_root
from agentic.function import agentic_function
from agentic import runtime  # use runtime.exec()

__all__ = [
    "agentic_function",
    "runtime",
    "Context",
    "get_context",
    "get_root_context",
    "init_root",
]
