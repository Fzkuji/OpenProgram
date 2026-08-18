"""
openprogram.agentic_programming — core engine.

Primitives:

    1. @agentic_function  — turn a Python function into one that can call an LLM
    2. llm                 — make one model request through the ambient Runtime
    3. agent               — make a tool loop through the ambient Runtime
    4. goal                — make a judgment loop through the ambient Runtime
    5. decision.make       — let the LLM make the next-step decision

Infrastructure:

    Runtime                — base class for provider calls and accounting

Execution traces are persisted as a flat DAG in
``openprogram.context.storage`` (SQLite). Older revisions kept a
parallel in-memory ``Context`` tree + a JSONL trace + an event pubsub
layer; those have all been retired in favour of the DAG.

Zero downstream dependencies: providers / programs / webui depend on
agentic_programming, never the other way around.
"""

from openprogram.agentic_programming.function import (
    agentic_function, traced, auto_trace_module, auto_trace_package,
)
from openprogram.agentic_programming.runtime import Runtime
from openprogram.agentic_programming.llm import llm
from openprogram.agentic_programming.agent import agent
from openprogram.agentic_programming.goal import goal
from openprogram.agentic_programming import decision
from openprogram.agentic_programming.session import Session
from openprogram.agentic_programming.control_flow import (
    validate_and_retry, route, conditional
)

__all__ = [
    "agentic_function",
    "traced",
    "auto_trace_module",
    "auto_trace_package",
    "Runtime",
    "llm",
    "agent",
    "goal",
    "decision",
    "Session",
    "validate_and_retry",
    "route",
    "conditional",
]
