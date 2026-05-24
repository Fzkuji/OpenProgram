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
    "Spawn a sub-agent, run a single turn against it with the given "
    "prompt, and return its final reply. Two modes:\n"
    "\n"
    "  inline (default): the sub-agent inherits THIS conversation as "
    "context. Its reply lands as a sibling branch in the current "
    "session — a normal DAG fork. Use this when the sub-task needs "
    "the conversation so far. The reply is visible in the chat as "
    "a new branch the user can switch to.\n"
    "\n"
    "  detached: the sub-agent gets a brand-new peer session and "
    "sees ONLY the prompt (no parent context). Use this when the "
    "sub-task is fully self-contained and you don't want the parent "
    "history bleeding in. The reply shows up as an attach card in "
    "the parent chat; the peer session has its own sidebar entry.\n"
    "\n"
    "Args:\n"
    "  prompt: full instruction for the sub-agent. In detached mode "
    "this is ALL it sees — include any context it needs.\n"
    "  description: short label (1-3 words) used as the branch name "
    "/ sub-session title.\n"
    "  agent_id: which agent profile to run as. Defaults to this "
    "session's agent.\n"
    "  mode: 'inline' (default) or 'detached'. Pick inline unless "
    "you specifically want a fresh-context fan-out."
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
    mode: str = "inline",
) -> str:
    """Implementation body. Pulled out of the @function-wrapped binding
    so unit tests can drive it directly with their own ContextVars
    instead of going through the AgentTool execute path.

    ``mode="inline"`` (default): the spawned agent sees the parent
    conversation and writes its reply as a sibling branch in the same
    session — same DAG fork the user would get from clicking
    "fork from here". This is the normal Claude-Code Task feel.

    ``mode="detached"``: the spawned agent gets a brand-new peer
    session with no parent context (only the prompt). The reply
    materializes as an attach pointer card in the parent's DAG and
    the sub-session appears in the sidebar.
    """
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

    mode_clean = (mode or "").strip().lower() or "inline"
    if mode_clean not in ("inline", "detached"):
        return (
            f"[task error] unknown mode {mode!r} — use 'inline' (default, "
            "agent inherits this conversation) or 'detached' (agent gets "
            "a fresh peer session)."
        )

    try:
        if mode_clean == "inline":
            from openprogram.agent.sub_agent_run import run_inline_agent_turn
            result = run_inline_agent_turn(
                parent_session_id=sid,
                parent_assistant_id=aid,
                prompt=prompt,
                agent_id=chosen_agent,
                label=label or None,
            )
        else:
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
        if mode_clean == "inline":
            return f"[task error: head={result.head_id}] {result.error}"
        return f"[task error: session={result.sub_session_id}] {result.error}"

    out = result.final_text or "(sub-agent returned no text)"
    if result.error:
        out = f"{out}\n\n[task warning] {result.error}"

    if mode_clean == "inline":
        # Inline result lives at parent_session_id:head_id; merge_branches
        # accepts that pair literally.
        tail = f"branch={sid}:{result.head_id or '?'}"
    else:
        tail = f"session={result.sub_session_id}"
        if result.sub_commit_id:
            tail += f" commit={result.sub_commit_id}"
    return f"{out}\n\n[sub-agent {tail}]"


@function(
    name="task",
    description=_DESCRIPTION,
    toolset=["core"],
)
def task(
    prompt: str,
    description: str = "",
    agent_id: str = "",
    mode: str = "inline",
) -> str:
    """Spawn a sub-agent. Blocks until the sub-agent finishes; returns
    its final reply.

    Args:
        prompt: instruction for the sub-agent. In ``mode="detached"``
            this is ALL it sees, so include any context it needs.
        description: short label (1-3 words) used as the branch name
            / sub-session title.
        agent_id: agent profile to run the sub-agent under. Defaults
            to this session's agent.
        mode: ``"inline"`` (default) ⇒ sub-agent inherits THIS
            conversation, reply lands as a sibling branch in the
            current session. ``"detached"`` ⇒ fresh peer session
            with empty context; reply shows as an attach card.
    """
    return _task_impl(
        prompt=prompt, description=description,
        agent_id=agent_id, mode=mode,
    )
