"""Prompt text for the bash tool (description shown to the LLM).

Condensed from Claude Code's src/tools/BashTool/prompt.ts (leaked source,
reference-only) — we keep only the instructions that matter without a tool
catalogue, permission system, or sandbox. Callers are free to override.
"""

from __future__ import annotations

DEFAULT_MAX_TIMEOUT_MS = 10 * 60 * 1000   # 10 min
DEFAULT_TIMEOUT_MS = 2 * 60 * 1000        # 2 min

DESCRIPTION = (
    "Execute a host shell command and return its stdout, stderr, and exit code. "
    "macOS/Linux use their shell; Windows uses Git Bash when available and "
    "otherwise Windows PowerShell.\n"
    "\n"
    "The working directory persists between commands; shell state (exported "
    "variables, aliases) does not. The environment comes from the user's "
    "profile.\n"
    "\n"
    "- Prefer absolute paths over cd'ing around; double-quote paths with spaces.\n"
    "- Prefer dedicated file/search tools or Python for portable file and text "
    "operations. Avoid assuming Unix coreutils exist on Windows.\n"
    "- Emit independent commands as parallel tool calls in one turn. When "
    "chaining is necessary, use syntax supported by the active host shell.\n"
    "- To wait on a process, poll with a check command rather than sleep.\n"
    "- For git, prefer new commits over amending, and never skip hooks "
    "(--no-verify) unless explicitly asked.\n"
)
