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

Prompt design is adapted from Anthropic's Claude Code EnterPlanMode tool
(``references/claude-code-leaked/src/tools/EnterPlanModeTool/prompt.ts``)
— we keep their WHEN / WHEN-NOT / Examples taxonomy verbatim because it
has been validated against real user behaviour at scale. Local edits:
remove the AskUserQuestion references (we don't have that tool), drop
the in-mode "What Happens" section (the system reminder we inject at
dispatch time covers it), and pass the plan as a tool argument instead
of via a plan file.
"""
from __future__ import annotations

from openprogram.functions._runtime import function
from openprogram.agent import plan_mode as _pm


_DESCRIPTION = """Use this tool proactively when you're about to start a non-trivial implementation task. Getting user sign-off on your approach before writing code prevents wasted effort and ensures alignment. This tool transitions you into plan mode where you can explore the codebase and design an implementation approach for user approval.

## When to Use This Tool

**Prefer using enter_plan_mode** for implementation tasks unless they're simple. Use it when ANY of these conditions apply:

1. **New Feature Implementation**: Adding meaningful new functionality
   - Example: "Add a logout button" - where should it go? What should happen on click?
   - Example: "Add form validation" - what rules? What error messages?

2. **Multiple Valid Approaches**: The task can be solved in several different ways
   - Example: "Add caching to the API" - could use Redis, in-memory, file-based, etc.
   - Example: "Improve performance" - many optimization strategies possible

3. **Code Modifications**: Changes that affect existing behavior or structure
   - Example: "Update the login flow" - what exactly should change?
   - Example: "Refactor this component" - what's the target architecture?

4. **Architectural Decisions**: The task requires choosing between patterns or technologies
   - Example: "Add real-time updates" - WebSockets vs SSE vs polling
   - Example: "Implement state management" - Redux vs Context vs custom solution

5. **Multi-File Changes**: The task will likely touch more than 2-3 files
   - Example: "Refactor the authentication system"
   - Example: "Add a new API endpoint with tests"

6. **Unclear Requirements**: You need to explore before understanding the full scope
   - Example: "Make the app faster" - need to profile and identify bottlenecks
   - Example: "Fix the bug in checkout" - need to investigate root cause

7. **User Preferences Matter**: The implementation could reasonably go multiple ways
   - Plan mode lets you explore first, then present options with context

## When NOT to Use This Tool

Only skip enter_plan_mode for simple tasks:
- Single-line or few-line fixes (typos, obvious bugs, small tweaks)
- Adding a single function with clear requirements
- Tasks where the user has given very specific, detailed instructions
- Pure research/exploration tasks (use read / glob / grep directly)

## What Happens in Plan Mode

In plan mode, you'll:
1. Thoroughly explore the codebase using glob, grep, and read tools
2. Understand existing patterns and architecture
3. Design an implementation approach
4. Present your plan to the user for approval
5. Exit plan mode by calling `exit_plan_mode(plan=...)` when ready to implement

## Examples

### GOOD - Use enter_plan_mode:
User: "Add user authentication to the app"
- Requires architectural decisions (session vs JWT, where to store tokens, middleware structure)

User: "Optimize the database queries"
- Multiple approaches possible, need to profile first, significant impact

User: "Implement dark mode"
- Architectural decision on theme system, affects many components

User: "Add a delete button to the user profile"
- Seems simple but involves: where to place it, confirmation dialog, API call, error handling, state updates

User: "Update the error handling in the API"
- Affects multiple files, user should approve the approach

### BAD - Don't use enter_plan_mode:
User: "Fix the typo in the README"
- Straightforward, no planning needed

User: "Add a console.log to debug this function"
- Simple, obvious implementation

User: "What files handle routing?"
- Research task, not implementation planning

## Important Notes

- After calling this tool, write tools (bash, edit, write, apply_patch, execute_code, process) become invisible to you until you call `exit_plan_mode` with an approved plan
- If unsure whether to use it, err on the side of planning - it's better to get alignment upfront than to redo work
- Users appreciate being consulted before significant changes are made to their codebase
"""


@function(
    name="enter_plan_mode",
    description=_DESCRIPTION,
    toolset=["core"],
)
def enter_plan_mode() -> str:
    """Switch the active session into plan mode."""
    session_id = _pm.current_session_id.get()
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
