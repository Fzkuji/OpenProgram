"""Agent configuration and shared execution primitives.

Agent methods create call scopes automatically. ``Context``
(``openprogram.runtime.context``) supplies named content and history
visibility. Runtime uses the existing providers and Session DAG.
"""

from openprogram.runtime.agent.call_state import (
    traced, auto_trace_module, auto_trace_package,
)
from openprogram.runtime.agent.runtime import Runtime
from openprogram.runtime.agent.llm import llm
from openprogram.runtime.agent.agent_function import agent, agent_async
from openprogram.runtime.agent.agent_class import Agent
from openprogram.runtime.agent import decision
from openprogram.runtime.agent.follow_up_session import Session
from openprogram.runtime.agent.control_flow import (
    validate_and_retry, route, conditional
)

__all__ = [
    "traced",
    "auto_trace_module",
    "auto_trace_package",
    "Runtime",
    "llm",
    "agent",
    "agent_async",
    "Agent",
    "decision",
    "Session",
    "validate_and_retry",
    "route",
    "conditional",
]

