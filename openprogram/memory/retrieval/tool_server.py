"""Claude Code MCP tools for read-only memory retrieval."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

from ..runtime.tokenization import TokenCounter
from .tools import execute_tool_call
from .views import tools_for


@dataclass
class RetrievalToolState:
    trace: list[dict[str, Any]]
    evidence: list[dict[str, str]]
    model: str
    indexes: dict[str, Any] = field(default_factory=dict)
    tool_calls: int = 0
    visible_tokens: int = 0


def retrieval_tools(
    runtime: Any,
    *,
    memory_dir: Path,
    files: list[Path],
    condition: str,
    include_recent: bool,
    state: RetrievalToolState,
    search_tools: str = "fused",
) -> list[Any]:
    definitions = tools_for(condition, search_tools)

    def make_tool(definition: dict[str, Any]):
        function = definition["function"]
        name = function["name"]

        @tool(
            name,
            function.get("description", ""),
            function["parameters"],
        )
        async def invoke(arguments: dict[str, Any]) -> dict[str, Any]:
            state.tool_calls += 1
            try:
                output, executed, accepted = execute_tool_call(
                    runtime,
                    name,
                    arguments,
                    memory_dir=memory_dir,
                    files=files,
                    condition=condition,
                    include_recent=include_recent,
                    indexes=state.indexes,
                )
                is_error = output.startswith(("Command rejected:", "Tool error:"))
            except Exception as exc:  # MCP reports the failure to Claude Code
                output = f"{type(exc).__name__}: {exc}"
                executed = False
                accepted = None
                is_error = True
            nonempty = (
                bool(output.strip())
                and not is_error
                and output not in {
                    "No BM25 matches.",
                    "No embedding matches.",
                }
            )
            tokens = TokenCounter.resolve(
                requested_model=state.model
            ).count(output) if nonempty else 0
            state.visible_tokens += tokens
            row = {
                "type": name,
                "args": arguments,
                "executed": executed,
                "nonempty": nonempty,
                "visible_tokens": tokens,
                "cumulative_visible_tokens": state.visible_tokens,
            }
            if accepted is not None:
                row["accepted"] = accepted
            state.trace.append(row)
            if nonempty:
                state.evidence.append({"text": output, "date": ""})
            return {
                "content": [{"type": "text", "text": output or "(no output)"}],
                "is_error": is_error,
            }

        return invoke

    return [make_tool(definition) for definition in definitions]
