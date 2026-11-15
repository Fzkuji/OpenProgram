"""enter_plan_mode — switch the current session into plan mode.

While plan mode is on:

  * The LLM cannot see / call tools that modify state (bash, edit,
    write, apply_patch, execute_code, process). Only read-only tools
    (read, glob, grep, list, web_search, web_fetch, todo, ...) are
    available.
  * The dispatcher injects a plan-mode reminder into the system prompt
    so the LLM knows the rules of the mode.

The LLM stays in plan mode across turns until it calls
``exit_plan_mode`` with an approved plan. There is no time limit.

The prompt says when to enter, when to skip it and what happens next.
The plan is a tool argument rather than a file.
"""
from __future__ import annotations

from openprogram.programs._host.runtime import function
from openprogram.runtime.conversation import plan_mode as _pm


_DESCRIPTION = """Switch this conversation into plan mode, where you investigate and agree an approach with the user before changing anything.

Enter it when the right change is not yet clear: the request leaves real choices open, several designs would work, the change spans many files or alters existing behaviour, or you need to investigate before you can scope the work.

Skip it when the change is small and fully specified, or when the user only wants information about the code.

After this call the write tools (bash, edit, write, apply_patch, execute_code, process) are hidden. Read what you need with read, glob and grep, then send your proposal with `exit_plan_mode(plan=...)`. The write tools come back once the user approves it.
"""


@function(
    name="enter_plan_mode",
    description=_DESCRIPTION,
    toolset=["core"],
    # Plan comes only from the selector: a model must not be able to put an
    # Bypass conversation into a read-only state.
    expose=False,
)
def enter_plan_mode() -> str:
    """Switch the active session into plan mode."""
    session_id = _pm.turn_session_id()
    if not session_id:
        # No session context — this happens in unit tests that call
        # the tool outside dispatcher. Be liberal: report instead of
        # crashing.
        return "[plan-mode] no active session — flag not set"
    _pm.enter(session_id)
    return (
        "Entered plan mode. Write tools are now hidden. Research the "
        "codebase with read / glob / grep, draft a concrete plan, and "
        "submit it via `exit_plan_mode(plan=...)` when you are ready "
        "for user review."
    )
