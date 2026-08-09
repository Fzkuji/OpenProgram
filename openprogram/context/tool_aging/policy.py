"""Tunable knobs for cross-turn tool memory.

Defaults integrate the techniques surveyed across Claude Code,
OpenCode, Hermes, and OpenClaw. See
``docs/design/context/cross-turn-tool-context.md`` for the rationale of
each number.

Every knob here is env-overridable so an ablation run can move one
variable without editing code. Defaults reproduce the shipped
behaviour exactly, so an unset environment changes nothing.

``AGING_ENABLED`` is read by BOTH consumers — ``prepare_history`` (the
history-mutating path) and ``render._aged_code_ids`` (the DAG render
pre-scan). Turning off only one leaves the other still aging, which is
the trap this single flag exists to avoid.
"""
from __future__ import annotations

import os


def _env_flag(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "off", "false", "no", ""}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip())
    except (TypeError, ValueError):
        return default


# Master switch for tool aging. Off ⇒ every tool result renders at full
# fidelity regardless of age (both the engine path and the DAG path).
AGING_ENABLED = _env_flag("OPENPROGRAM_TOOL_AGING", True)

# How many MOST RECENT assistant turns keep tool_use / tool_result
# at full fidelity. Older turns get aged down to one-line stubs.
# OpenCode defaults to 2; we bump to 3 to give our typical multi-tool
# turns one extra round of full memory.
TAIL_TURNS = _env_int("OPENPROGRAM_TOOL_AGING_TAIL_TURNS", 3)

# Single tool_result is hard-capped at this many characters before
# the head + tail middle-truncation kicks in. Applies even to the
# tail window — a 50MB JSON dump from one tool call still blows
# context within a single turn.
MAX_TOOL_RESULT_CHARS = _env_int(
    "OPENPROGRAM_TOOL_AGING_MAX_RESULT_CHARS", 4000,
)

# When aging an older turn's tool_use, the args dict gets JSON-
# stringified and truncated to this length. Keeps "what did I call"
# visible while shedding most of the payload.
MAX_TOOL_ARGS_CHARS = 200

# Tools whose results are short + semantically load-bearing —
# never aged, even on old turns. The todo board holds the agent's
# plan; web_search seeds the URLs the agent wants to revisit.
PRUNE_PROTECTED_TOOLS: frozenset[str] = frozenset({
    "todo_create",
    "todo_update",
    "todo_list",
    "web_search",
})

# When a tool result gets summarized to a stub, this prefix marks
# the stub so the model can tell aged content from live content.
STUB_PREFIX = "[aged]"
