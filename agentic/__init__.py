"""
Agentic Programming — a programming paradigm where Python and LLM co-execute functions.

The framework has 3 components:

    @agentic_function    Decorator. Auto-tracks execution in a Context tree.
    runtime.exec()       Calls the LLM. Auto-records input/output to Context.
    Context              Tree of execution records. Query with summarize().

Core principle:
    ONE tree records EVERYTHING. When feeding context to an LLM, query selectively.
    Recording and querying are fully separated.

Minimal example:
    from agentic import agentic_function, runtime

    @agentic_function
    def observe(task):
        '''Look at the screen and describe what you see.'''
        img = take_screenshot()
        return runtime.exec(prompt=observe.__doc__, input={"task": task}, images=[img])

    @agentic_function
    def navigate(target):
        '''Find and click the target.'''
        obs = observe(task=f"find {target}")
        click(obs["location"])
        return {"success": True}

    navigate("login")  # Context tree is built automatically

Design history:
    v1 (harness/): Heavy framework with Session, Scope, Memory, MCP, Type classes.
       Session alone was 905 lines with 6 implementations (Anthropic, OpenAI, Claude Code...).
       Too complex. Users had to understand too many abstractions.
    
    v2 (agentic/): Stripped to essentials. 3 files, ~500 lines total.
       Key insight: users should write NORMAL PYTHON. The framework is invisible.
       @agentic_function + runtime.exec() + Context is all you need.
       
    The old harness/ was deleted entirely. DESIGN.md still references some
    old concepts (Session, Scope, Memory) — those are now aspirational, not implemented.
"""

from agentic.context import (
    Context, ContextPolicy, get_context, get_root_context, init_root,
    ORCHESTRATOR, PLANNER, WORKER, LEAF, FOCUSED,
)
from agentic.function import agentic_function
from agentic import runtime  # use runtime.exec()

__all__ = [
    "agentic_function",
    "runtime",
    "Context",
    "ContextPolicy",
    "get_context",
    "get_root_context",
    "init_root",
    # Preset policies
    "ORCHESTRATOR",
    "PLANNER",
    "WORKER",
    "LEAF",
    "FOCUSED",
]
