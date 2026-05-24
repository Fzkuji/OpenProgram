"""task — spawn a sub-agent that runs an independent turn on its own
git branch and returns its final reply.

Mirrors Claude Code's Task tool. The calling agent issues
``task(prompt=...)`` from within a turn; this function blocks until
the sub-agent completes, then returns the sub-agent's final text as
the tool's result. Multiple ``task`` calls in one assistant message
run sequentially today (agent_loop is serial) — true parallel
execution would require changing ``_execute_tool_calls`` to gather.

The sub-agent gets a fresh worktree, an isolated SessionStore rooted
at that worktree, and writes its history/context onto a sub-branch
that survives release. A future merge turn (``merge_branches`` WS
action) can reconcile multiple sub-branches back into the parent.

Parent context is found via two ContextVars set by the dispatcher /
webui:

* ``openprogram.webui._pause_stop._current_session_id``
  — bound at ``execute_in_context`` entry.
* ``openprogram.store._current_turn_id``
  — set by ``dispatcher.process_user_turn`` to the assistant
  message id of the turn currently running.

If either is missing the tool returns a structured error string so
the calling LLM sees a clear message it can act on.
"""
from __future__ import annotations

from openprogram.functions._runtime import function


_DESCRIPTION = (
    "Spawn a sub-agent that runs an independent turn on a separate git "
    "branch off the current conversation. The sub-agent gets its own "
    "worktree (isolated file edits), runs to completion with the given "
    "prompt, and returns its final reply as this tool's result. The "
    "sub-branch and its commits survive after this call so a follow-up "
    "merge turn can reconcile multiple parallel sub-agents.\n"
    "\n"
    "Use this when a sub-task is well-scoped (one focused goal, "
    "self-contained context) and the main agent doesn't need to see "
    "the sub-agent's intermediate steps — only its final answer. "
    "Common patterns: parallel research probes, independent code "
    "explorations, scoped refactors.\n"
    "\n"
    "Args:\n"
    "  prompt: full instruction for the sub-agent (it sees ONLY this; "
    "include any context it needs).\n"
    "  description: short label (1-3 words) shown in the UI / git "
    "branch name; helps you distinguish multiple sub-agents.\n"
    "  agent_id: which agent profile the sub-agent should run as. "
    "Defaults to the parent's agent."
)


def _resolve_parent() -> tuple[str | None, str | None, str | None]:
    """Pull (session_id, assistant_msg_id, default_agent_id) from the
    ambient ContextVars + the parent's session row. Returns
    (None, ...) if either ContextVar is unset — the tool can't run
    without a parent turn to hang off."""
    try:
        from openprogram.webui._pause_stop import _current_session_id
        sid = _current_session_id.get(None)
    except Exception:
        sid = None
    try:
        from openprogram.store import _current_turn_id
        aid = _current_turn_id.get()
    except Exception:
        aid = None
    agent_id = None
    if sid:
        try:
            from openprogram.agent.session_db import default_db
            sess = default_db().get_session(sid) or {}
            agent_id = sess.get("agent_id")
        except Exception:
            agent_id = None
    return sid, aid, agent_id


def _task_impl(
    prompt: str,
    description: str = "",
    agent_id: str = "",
) -> str:
    """Implementation body. Pulled out of the @function-wrapped binding
    so unit tests can drive it directly with their own ContextVars
    instead of going through the AgentTool execute path."""
    sid, aid, parent_agent = _resolve_parent()
    if not sid or not aid:
        return (
            "[task error] no active parent turn — task() must be called "
            "from inside an assistant turn (the dispatcher sets the "
            "session + turn ContextVars on entry)."
        )
    chosen_agent = (agent_id or "").strip() or parent_agent or "main"

    label = (description or "").strip()
    # Sanitize label for branch name: git ref chars only.
    if label:
        label = "".join(
            c if c.isalnum() or c in "-_" else "_"
            for c in label
        )[:24]

    try:
        from openprogram.agent.sub_agent_run import run_sub_agent_turn
        result = run_sub_agent_turn(
            parent_session_id=sid,
            parent_assistant_id=aid,
            prompt=prompt,
            agent_id=chosen_agent,
            label=label or None,
        )
    except Exception as e:  # noqa: BLE001
        return f"[task error] {type(e).__name__}: {e}"

    if result.error and not result.final_text:
        return f"[task error: branch={result.branch}] {result.error}"
    out = result.final_text or "(sub-agent returned no text)"
    if result.error:
        out = f"{out}\n\n[task warning] {result.error}"
    # Tag the result with branch + commit_sha so a follow-up
    # `merge_branches` call can be issued without re-spawning.
    out = (
        f"{out}\n\n"
        f"[sub-agent branch={result.branch}"
        + (f" commit={result.sub_commit_sha}" if result.sub_commit_sha else "")
        + "]"
    )
    return out


@function(
    name="task",
    description=_DESCRIPTION,
    toolset=["core"],
)
def task(
    prompt: str,
    description: str = "",
    agent_id: str = "",
) -> str:
    """Spawn a sub-agent. Blocks until the sub-agent's turn finishes,
    then returns its final reply text.

    Args:
        prompt: instruction for the sub-agent. It starts with an empty
            conversation; everything it needs to know goes here.
        description: short label (1-3 words) used as the sub-branch
            name and shown in the UI. Optional but recommended when
            spawning several sub-agents in the same turn so they
            stay distinguishable.
        agent_id: agent profile to run the sub-agent under. Defaults
            to the parent session's agent.
    """
    return _task_impl(prompt=prompt, description=description, agent_id=agent_id)
