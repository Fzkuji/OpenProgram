"""exit_plan_mode — submit the drafted plan for user approval.

This tool is always wrapped by the dispatcher's approval gate
(``requires_approval=True``). The approval payload carries the
``plan`` argument so the UI shows the proposed plan to the user. If
the user approves, the tool body runs (clears the plan-mode flag and
returns a success message). If the user rejects, the gate returns a
"[denied]" result and the flag stays on — the LLM is expected to read
the rejection, revise, and either re-submit or change direction.

Prompt design follows Anthropic's ExitPlanMode tool
(``references/claude-code-leaked/src/tools/ExitPlanModeTool/prompt.ts``)
— same purpose, same "when to use" rules. Local edit: claude-code-leaked
v2 reads the plan from a file written via FileEdit; we pass the plan
as the ``plan`` argument directly, because OpenProgram doesn't have the
plan-file workflow.
"""
from __future__ import annotations

from openprogram.functions._runtime import function
from openprogram.agent import plan_mode as _pm


_DESCRIPTION = """Use this tool when you are in plan mode and have finished drafting your plan and are ready for user approval.

## How This Tool Works
- Pass the full plan content as the `plan` argument (markdown is recommended)
- The user reviews the plan and chooses Approve or Reject
- Approve: this tool returns a confirmation, plan mode is cleared, and you can implement
- Reject: this tool returns a "[denied]" result, plan mode stays on, and you should revise the plan based on any feedback before re-submitting

## When to Use This Tool
IMPORTANT: Only use this tool when the task requires planning the implementation steps of a task that requires writing code. For research tasks where you're gathering information, searching files, reading files or in general trying to understand the codebase — do NOT use this tool.

## Before Using This Tool
Ensure your plan is complete and unambiguous:
- If you have unresolved questions about requirements or approach, ask the user a clarifying question first (in earlier turns)
- Once your plan is finalized, use THIS tool to request approval

**Important:** Do NOT ask the user "Is this plan okay?" or "Should I proceed?" in free-form text — that's exactly what THIS tool does. exit_plan_mode inherently requests user approval of your plan.

## Plan Quality
Write the plan for a human reviewer, not for yourself. Concrete sections beat vague summaries:
- Files to change and what changes
- New code structure (function/class names, signatures, where they live)
- Ordering of work
- Edge cases and how you'll handle them
- Anything the user needs to evaluate before agreeing

Avoid vague summaries like "add tests and refactor".

## Examples

1. Initial task: "Search for and understand the implementation of vim mode in the codebase" — Do not use exit_plan_mode because you are not planning the implementation steps of a task.
2. Initial task: "Help me implement yank mode for vim" — Use exit_plan_mode after you have finished planning the implementation steps of the task.
3. Initial task: "Add a new feature to handle user authentication" — If unsure about auth method (OAuth, JWT, etc.), ask a clarifying question first, then use exit_plan_mode after clarifying the approach.
"""


@function(
    name="exit_plan_mode",
    description=_DESCRIPTION,
    toolset=["core"],
    requires_approval=True,
)
def exit_plan_mode(plan: str) -> str:
    """Submit a plan for user approval.

    Args:
        plan: The concrete implementation plan in markdown. Reaches
            the user verbatim — write it for a human reviewer, not
            for the LLM.
    """
    session_id = _pm.current_session_id.get()
    if not session_id:
        return "[plan-mode] no active session — flag not cleared"
    _pm.exit(session_id)
    return (
        "Plan approved. You have exited plan mode. You can now make "
        "edits, run tools, and take actions. Implement the plan as "
        "approved; do not deviate from it without checking back with "
        "the user first."
    )
