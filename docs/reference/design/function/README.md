# Function Design

Internal design notes for the function/tool-calling framework.

**Writing functions?** The authoring-facing docs (usage patterns, metadata
rules, the three "choose the next step" mechanisms, pure-python helpers)
moved to the user guide:
[`docs/agentic-programming/`](../../../capabilities/agentic-programming/README.md).

## Current sources

| Topic | Source |
|---|---|
| Function/tool calling framework (`@function` / `@agentic_function`, shared registry, gating, deferred loading) | [`calling-unification.md`](calling-unification.md) |
| Decorator usage, metadata spec, the tool-call loop, pure-python helpers | product pages under [`capabilities/agentic-programming/`](../../../capabilities/agentic-programming/README.md) — these are the single home; the former design duplicates here were deleted |

## Implementation files

- `openprogram/agentic_programming/function.py`
- `openprogram/agentic_programming/runtime.py`
- `openprogram/agentic_programming/decision.py`
- `openprogram/functions/tools/<name>/`
- `openprogram/functions/agentics/llm_call_example/__init__.py`
