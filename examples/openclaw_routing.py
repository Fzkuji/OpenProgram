"""
OpenClaw Routing — Route text through Claude Code CLI.

Input text → Claude Code CLI → Output text. That's it.

Usage:
    from examples.openclaw_routing import route
    response = route(text="What is prompt caching?")
"""

from agentic import agentic_function
from agentic.providers import ClaudeCodeRuntime

runtime = ClaudeCodeRuntime(model="sonnet", timeout=120)


@agentic_function
def route(text: str) -> str:
    """Route text through Claude Code and return the response."""
    return runtime.exec(content=[{"type": "text", "text": text}])
