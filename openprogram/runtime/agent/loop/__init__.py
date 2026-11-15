"""
Agent loop — mirrors packages/agent/src/agent-loop.ts

Core loop logic: agentLoop(), agentLoopContinue(), runLoop().
"""
from __future__ import annotations

from openprogram.runtime.agent.loop.guards import (
    MAX_INNER_ITERATIONS,
    RepetitiveOutputError,
    iteration_cap_for,
)
from openprogram.runtime.agent.loop.message_context import (
    _inject_memory_prefetch,
)
from openprogram.runtime.agent.loop.lifecycle import (
    _create_agent_stream,
    _finish_interrupted_stream,
)
from openprogram.runtime.agent.loop.streaming import (
    _cancel_response_stream,
    _stream_assistant_response,
)
from openprogram.runtime.agent.loop.tool_dispatch import (
    RUNNING_TOOL_CALLS,
    RUNNING_TOOL_CALLS_LOCK,
    _attach_nested_agents_md,
    _rewrite_tool_result,
    _tool_call_label,
    _execute_tool_calls,
)
from openprogram.runtime.agent.loop.entrypoints import (
    agent_loop,
    agent_loop_continue,
    agent_loop_resume,
)

__all__ = [
    "MAX_INNER_ITERATIONS",
    "RUNNING_TOOL_CALLS",
    "RUNNING_TOOL_CALLS_LOCK",
    "RepetitiveOutputError",
    "_attach_nested_agents_md",
    "_cancel_response_stream",
    "_create_agent_stream",
    "_execute_tool_calls",
    "_finish_interrupted_stream",
    "_inject_memory_prefetch",
    "_rewrite_tool_result",
    "_stream_assistant_response",
    "_tool_call_label",
    "agent_loop",
    "agent_loop_continue",
    "agent_loop_resume",
    "iteration_cap_for",
]
