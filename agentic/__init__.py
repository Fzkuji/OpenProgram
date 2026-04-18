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
from agentic.function import agentic_function, traced, auto_trace_module, auto_trace_package
from agentic.runtime import Runtime
from agentic.meta_functions import create, create_app, fix, improve, create_skill
from agentic.providers import detect_provider, create_runtime, check_providers
from agentic.functions.general_action import general_action
from agentic.functions.agent_loop import agent_loop
from agentic.functions.wait import wait
from agentic.functions.deep_work import deep_work
from agentic.functions.init_research import init_research

__all__ = [
    "agentic_function",
    "traced",
    "auto_trace_module",
    "Runtime",
    "Context",
    "create",
    "create_app",
    "fix",
    "improve",
    "create_skill",
    "detect_provider",
    "create_runtime",
    "check_providers",
    "general_action",
    "agent_loop",
    "wait",
    "deep_work",
    "init_research",
]
