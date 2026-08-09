"""Initial context rendered for one retrieval trajectory."""

from pathlib import Path
from typing import Any

from ..runtime.tokenization import TokenCounter
from ..prompts import RETRIEVAL_PROMPT


def initialize_context(
    *,
    memory_dir: Path,
    files: list[Path],
    condition: str,
    item: dict[str, Any],
    verify_sources: bool,
    model: str,
) -> tuple[str, list[dict[str, Any]], list[dict[str, str]], int]:
    core_path = memory_dir / "core.md"
    recent_path = memory_dir / "recent_events.jsonl"
    core = core_path.read_text(encoding="utf-8") if core_path in files else ""
    recent = (
        recent_path.read_text(encoding="utf-8")
        if recent_path in files
        else ""
    )
    inventory = "\n".join(
        path.relative_to(memory_dir).as_posix() for path in files
    )
    prompt = RETRIEVAL_PROMPT.format(
        condition=condition,
        workspace_root=memory_dir,
        core_memory=core or "(empty)",
        recent_memory=recent or "(empty)",
        inventory=inventory or "(no visible files)",
        source_verification_guidance=(
            "Source files are directly accessible through the standard "
            "read and search tools. Verify relevant Topic evidence against "
            "its Source file before answering."
            if verify_sources
            else "Source files remain directly accessible through the "
            "standard read and search tools; explicit source verification "
            "is optional for this run."
        ),
        question_date=item.get("question_date", ""),
        question=item["question"],
    )
    evidence = [
        {"text": text, "date": ""}
        for text in (core, recent)
        if text.strip()
    ]
    visible_tokens = TokenCounter.resolve(requested_model=model).count(
        core + recent + inventory
    )
    trace = []
    if core.strip() or recent.strip():
        trace.append({
            "type": "initial_context",
            "core_present": bool(core.strip()),
            "recent_present": bool(recent.strip()),
            "memory_visible_tokens": visible_tokens,
        })
    return prompt, trace, evidence, visible_tokens
