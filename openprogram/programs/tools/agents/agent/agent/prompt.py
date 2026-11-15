"""Prompt text for the agent tool (description shown to the LLM).

The description ends with the roster of saved Agents other agents may use
(``roster_text``), as Claude Code lists its ``subagent_type``s in the Agent
tool prompt. The roster is sorted by Agent id, so an edit to one Agent
moves nothing else, and an edit that changes it announces the prefix break
(``agent_registry.manager``: ``expect_break_everywhere("agent_roster")``).
"""
from __future__ import annotations

DESCRIPTION = (
    "Start a new agent for a bounded task and get its result. Returns the "
    "agent's result when it finishes (run_in_background=false, default), or "
    "an execution_id at once (run_in_background=true); a background agent's "
    "result comes back to you automatically as a notice, so end your turn "
    "or keep working instead of waiting. Every result has the same shape: "
    "<agent name id status> with the final reply, the files it changed when "
    "it had its own worktree, and usage (time, tool calls, tokens, cost). "
    "This tool only creates agents; to give more work to an agent that "
    "already exists, use send_message.\n"
    "\n"
    "start_from — what the new agent sees:\n"
    "  fresh (DEFAULT): only your prompt. Write a self-contained brief: the "
    "goal, what you already know, file paths, what to return. Never write "
    "'based on your findings' or refer to things it cannot see.\n"
    "  fork: a copy of this conversation up to now, sharing its cached "
    "prompt, so it is cheap to start; only with your own Agent. A forked "
    "agent cannot start agents of its own.\n"
    "  'SID:MSG_ID': forks off that exact node of any conversation.\n"
    "\n"
    "isolation: 'worktree' gives the agent its own git worktree, so its file "
    "changes stay out of the checkout until merged; 'none' shares this "
    "folder. Left empty, a background agent that can edit files gets its own "
    "worktree in a git repository and every other agent shares the folder. "
    "Before merging a worktree, review its diff and run the tests, then call "
    "worktree_merge.\n"
    "\n"
    "If an agent fails, retry narrower with send_message to the same agent, "
    "or tell the user; do not silently redo its work.\n"
    "\n"
    "Args:\n"
    "  prompt: the task. With start_from='fresh' this is ALL the agent sees.\n"
    "  description: short name (a few words), shown as written; "
    "send_message(to=...) reaches the agent by it.\n"
    "  agent: the saved Agent to run, by name or id (see the list below). "
    "Defaults to your own Agent. Its model and tools come from that Agent.\n"
    "  start_from: 'fresh' (default), 'fork', or 'SID:MSG_ID'.\n"
    "  run_in_background: true returns at once; the result is pushed to you.\n"
    "  isolation: 'worktree', 'none', or empty for the default rule.\n"
    "  archive_when_done: true archives the agent once its result is back; "
    "otherwise it stays for follow-up messages."
)


def roster_text(agents: list[tuple[str, str, str]]) -> str:
    """The list of Agents other agents may use: ``(id, name, description)``."""
    if not agents:
        return ""
    lines = ["", "", "Agents you can start with agent=…:"]
    for agent_id, name, description in agents:
        about = " ".join((description or "").split())
        if len(about) > 160:
            about = about[:159] + "…"
        label = name or agent_id
        ident = f" ({agent_id})" if name and name != agent_id else ""
        lines.append(f"- {label}{ident}" + (f" — {about}" if about else ""))
    return "\n".join(lines)
