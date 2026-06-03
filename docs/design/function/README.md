# Function Design

This directory documents how functions are authored and exposed in OpenProgram.

## Current sources

| Topic | Source |
|---|---|
| Metadata source of truth | [`function_metadata.md`](function_metadata.md) |
| `@agentic_function` usage patterns | [`agentic_function.md`](agentic_function.md) |
| Plain Python helper functions | [`pure_python.md`](pure_python.md) |
| Python-decided subfunction order | [`function_calling/code_call.md`](function_calling/code_call.md) |
| LLM-decided function selection | [`../function-calling-unification.md`](function-calling-unification.md) — current source; loop details are in [`../tool-calling.md`](tool-calling.md) |

## Current rules

1. `@agentic_function` may call `runtime.exec()` multiple times.
2. `@agentic_function` may call other `@agentic_function` objects directly.
3. LLM-driven selection uses `runtime.exec(tools=[...])` and provider-native tool use.
4. The docstring describes the function for humans, catalogs, and tool specs.
5. Each `runtime.exec(content=[...])` must include the concrete instruction and data for that LLM call.

For parameter descriptions, placeholders, hidden arguments, `render_range`, and
WebUI behavior, use [`function_metadata.md`](function_metadata.md) as the
authority.

## Implementation files

- `openprogram/agentic_programming/function.py`
- `openprogram/agentic_programming/runtime.py`
- `openprogram/functions/tools/<name>/`
- `openprogram/functions/agentics/llm_call_example/__init__.py`
