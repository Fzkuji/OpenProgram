"""Tunable knobs for cross-turn tool memory.

Defaults integrate the techniques surveyed across Claude Code,
OpenCode, Hermes, and OpenClaw. See
``docs/design/context/cross-turn-tool-context.md`` for the rationale of
each number.
"""
from __future__ import annotations


# How many MOST RECENT assistant turns keep tool_use / tool_result
# at full fidelity. Older turns get aged down to one-line stubs.
# OpenCode defaults to 2; we bump to 3 to give our typical multi-tool
# turns one extra round of full memory.
TAIL_TURNS = 3

# Single tool_result is hard-capped at this many characters before
# the head + tail middle-truncation kicks in. Applies even to the
# tail window — a 50MB JSON dump from one tool call still blows
# context within a single turn.
MAX_TOOL_RESULT_CHARS = 4000

# When aging an older turn's tool_use, the args dict gets JSON-
# stringified and truncated to this length. Keeps "what did I call"
# visible while shedding most of the payload.
MAX_TOOL_ARGS_CHARS = 200

# Tools whose results are short + semantically load-bearing —
# never aged, even on old turns. todo_read/todo_write maintain the
# agent's plan; web_search seeds the URLs the agent wants to revisit.
PRUNE_PROTECTED_TOOLS: frozenset[str] = frozenset({
    "todo_read",
    "todo_write",
    "web_search",
})

# When a tool result gets summarized to a stub, this prefix marks
# the stub so the model can tell aged content from live content.
STUB_PREFIX = "[aged]"
