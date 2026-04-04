"""
Agentic Programming — Python functions that call LLMs with automatic context.

Three things:

    @agentic_function    Decorator. Records every call into a Context tree.
    Runtime              LLM runtime class. Handles context injection and recording.
    Context              The tree of execution records. Query it with summarize().

Quick start:

    from agentic import agentic_function, Runtime

    runtime = Runtime(call=my_llm_func, model="gpt-4o")

    @agentic_function
    def observe(task):
        '''Look at the screen and describe what you see.'''
        return runtime.exec(content=[
            {"type": "text", "text": "Find the login button."},
            {"type": "image", "path": "screenshot.png"},
        ])

    @agentic_function(compress=True)
    def navigate(target):
        '''Navigate to a target element.'''
        obs = observe(f"find {target}")
        action = plan(obs)
        act(action)
        return verify(target)
"""

from agentic.context import Context
from agentic.function import agentic_function
from agentic.runtime import Runtime
from agentic.meta_function import create, fix

__all__ = [
    "agentic_function",
    "Runtime",
    "Context",
    "create",
    "fix",
]
