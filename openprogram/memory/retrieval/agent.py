"""Agent-controlled Scriptorium retrieval through Claude Code."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .config import QueryConfig
from .context import initialize_context
from .tool_server import RetrievalToolState, retrieval_tools
from .views import memory_files


_ANSWER_RE = re.compile(r"<answer>(.*?)</answer>", re.DOTALL | re.IGNORECASE)


def collect_answer(
    runtime: Any,
    item: dict[str, Any],
    memory_dir: Path,
    turn_index: dict[str, Any],
    condition: str = "native",
    *,
    include_recent: bool = True,
    config: QueryConfig | None = None,
) -> tuple[list[dict[str, str]], int, str, list[dict[str, Any]]]:
    del turn_index
    config = config or getattr(runtime, "query_config", None) or QueryConfig()
    memory_dir = Path(memory_dir).resolve()
    files = memory_files(
        memory_dir, condition, include_recent=include_recent
    )
    prompt, trace, evidence, initial_tokens = initialize_context(
        memory_dir=memory_dir,
        files=files,
        condition=condition,
        item=item,
        verify_sources=config.verify_sources,
        model=runtime.model,
    )
    state = RetrievalToolState(
        trace=trace,
        evidence=evidence,
        model=runtime.model,
        visible_tokens=initial_tokens,
    )
    result = runtime.agent.run(
        prompt=prompt,
        system_prompt=(
            "Retrieve evidence from the supplied memory workspace and answer "
            "the question. Use only the available tools and evidence."
        ),
        cwd=memory_dir,
        tools=retrieval_tools(
            runtime,
            memory_dir=memory_dir,
            files=files,
            condition=condition,
            include_recent=include_recent,
            state=state,
            search_tools=config.search_tools,
        ),
        max_turns=config.max_turns,
        max_budget_usd=config.max_budget_usd,
    )
    runtime.log_agent_result(result, phase="memory_qa")
    match = _ANSWER_RE.search(result.text)
    answer = (match.group(1) if match else result.text).strip()
    trace.append({
        "type": "termination",
        "termination_reason": result.stop_reason or "complete",
        "retrieval_rounds": result.num_turns,
        "tool_calls": state.tool_calls,
        "memory_visible_tokens": state.visible_tokens,
        "source_verification": config.verify_sources,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "anthropic_equivalent_cost_usd": result.anthropic_equivalent_cost_usd,
    })
    return evidence, result.num_turns, answer or "Insufficient information.", trace
