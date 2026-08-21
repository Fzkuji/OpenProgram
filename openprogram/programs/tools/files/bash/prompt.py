"""Prompt text for the bash tool (description shown to the LLM).

Condensed from Claude Code's src/tools/BashTool/prompt.ts (leaked source,
reference-only) — we keep only the instructions that matter without a tool
catalogue, permission system, or sandbox. Callers are free to override.
"""

from __future__ import annotations

DEFAULT_MAX_TIMEOUT_MS = 10 * 60 * 1000   # 10 min
DEFAULT_TIMEOUT_MS = 2 * 60 * 1000        # 2 min

DESCRIPTION = (
    "Execute a bash command and return its stdout, stderr, and exit code.\n"
    "\n"
    "The working directory persists between commands; shell state (exported "
    "variables, aliases) does not. The environment comes from the user's "
    "profile.\n"
    "\n"
    "- Prefer absolute paths over cd'ing around; double-quote paths with spaces.\n"
    "- Chain ordered commands with && in one call; emit independent commands "
    "as parallel tool calls in one turn. Use ';' only when earlier failures "
    "do not matter.\n"
    "- Separate commands with && or ';', never newlines (newlines are fine "
    "inside quoted strings).\n"
    "- To wait on a process, poll with a check command rather than sleep.\n"
    "- For git, prefer new commits over amending, and never skip hooks "
    "(--no-verify) unless explicitly asked.\n"
)
