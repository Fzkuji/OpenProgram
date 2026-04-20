"""
Branch summarization for tree navigation.

When navigating between branches in a session tree, this generates a summary
of the abandoned branch so context isn't lost. The tree navigator passes in
its own session object satisfying a duck-typed interface:

    session.get_branch(leaf_id) -> list of entries (each with .id, .parent_id, .type, ...)
    session.get_entry(entry_id) -> entry or None

Used by the Runtime layer if it maintains a tree-structured context.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from .utils import (
    SUMMARIZATION_SYSTEM_PROMPT,
    FileOperations,
    compute_file_lists,
    create_file_ops,
    extract_file_ops_from_message,
    format_file_operations,
    serialize_conversation,
)


# ─── Types ────────────────────────────────────────────────────────────────────

@dataclass
class BranchSummaryResult:
    summary: str | None = None
    read_files: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)
    aborted: bool = False
    error: str | None = None


@dataclass
class BranchSummaryDetails:
    read_files: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)


@dataclass
class BranchPreparation:
    messages: list[Any] = field(default_factory=list)
    file_ops: FileOperations = field(default_factory=create_file_ops)
    total_tokens: int = 0


@dataclass
class CollectEntriesResult:
    entries: list[Any] = field(default_factory=list)
    common_ancestor_id: str | None = None


# ─── Entry collection ─────────────────────────────────────────────────────────

def collect_entries_for_branch_summary(
    session: Any,
    old_leaf_id: str | None,
    target_id: str,
) -> CollectEntriesResult:
    """Collect entries to summarize when navigating between branches.

    Walks back from ``old_leaf_id`` toward the common ancestor of the old and
    target branches, collecting entries that will be abandoned by the move.
    """
    if not old_leaf_id:
        return CollectEntriesResult()

    old_path_set = {e.id for e in session.get_branch(old_leaf_id)}
    target_path = session.get_branch(target_id)

    common_ancestor_id: str | None = None
    for entry in reversed(target_path):
        if entry.id in old_path_set:
            common_ancestor_id = entry.id
            break

    entries: list[Any] = []
    current: str | None = old_leaf_id

    while current and current != common_ancestor_id:
        entry = session.get_entry(current)
        if not entry:
            break
        entries.append(entry)
        current = entry.parent_id

    entries.reverse()
    return CollectEntriesResult(entries=entries, common_ancestor_id=common_ancestor_id)


# ─── Entry to message conversion ──────────────────────────────────────────────

def _get_message_from_entry(entry: Any) -> Any | None:
    """Extract an AgentMessage from a session entry."""
    from openprogram.agent.messages import (
        create_branch_summary_message,
        create_compaction_summary_message,
        create_custom_message,
    )

    entry_type = getattr(entry, "type", None)

    if entry_type == "message":
        msg = getattr(entry, "message", None)
        if msg and getattr(msg, "role", None) == "toolResult":
            return None
        return msg

    if entry_type == "custom_message":
        return create_custom_message(
            entry.custom_type,
            entry.content,
            getattr(entry, "display", None),
            getattr(entry, "details", None),
            getattr(entry, "timestamp", None),
        )

    if entry_type == "branch_summary":
        return create_branch_summary_message(
            entry.summary,
            getattr(entry, "from_id", None),
            getattr(entry, "timestamp", None),
        )

    if entry_type == "compaction":
        return create_compaction_summary_message(
            entry.summary,
            getattr(entry, "tokens_before", None),
            getattr(entry, "timestamp", None),
        )

    return None


def _estimate_tokens_msg(message: Any) -> int:
    """Rough char/4 token estimate for a message."""
    chars = 0
    role = getattr(message, "role", "")
    content = getattr(message, "content", "")

    if role in ("user", "toolResult"):
        if isinstance(content, str):
            chars = len(content)
        elif isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "text":
                    chars += len(b.get("text", ""))
    elif role == "assistant":
        if isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                btype = b.get("type")
                if btype == "text":
                    chars += len(b.get("text", ""))
                elif btype == "thinking":
                    chars += len(b.get("thinking", ""))
                elif btype == "toolCall":
                    import json
                    chars += len(b.get("name", "")) + len(json.dumps(b.get("arguments", {})))
    else:
        summary = getattr(message, "summary", "")
        if summary:
            chars = len(summary)

    return max(1, chars // 4)


def prepare_branch_entries(
    entries: list[Any],
    token_budget: int = 0,
) -> BranchPreparation:
    """Prepare entries for summarization with a token budget.

    Walks newest-to-oldest, keeping the most recent context until the budget
    is exhausted. File-ops are collected across the entire branch.
    """
    messages: list[Any] = []
    file_ops = create_file_ops()
    total_tokens = 0

    for entry in entries:
        if (
            getattr(entry, "type", None) == "branch_summary"
            and not getattr(entry, "from_hook", False)
            and getattr(entry, "details", None) is not None
        ):
            details = entry.details
            if isinstance(details, dict):
                for f in details.get("readFiles", []):
                    file_ops.read.add(f)
                for f in details.get("modifiedFiles", []):
                    file_ops.edited.add(f)

    for entry in reversed(entries):
        msg = _get_message_from_entry(entry)
        if not msg:
            continue

        extract_file_ops_from_message(msg, file_ops)
        tokens = _estimate_tokens_msg(msg)

        if token_budget > 0 and total_tokens + tokens > token_budget:
            entry_type = getattr(entry, "type", "")
            if entry_type in ("compaction", "branch_summary"):
                if total_tokens < token_budget * 0.9:
                    messages.insert(0, msg)
                    total_tokens += tokens
            break

        messages.insert(0, msg)
        total_tokens += tokens

    return BranchPreparation(messages=messages, file_ops=file_ops, total_tokens=total_tokens)


# ─── Summary generation ───────────────────────────────────────────────────────

_BRANCH_SUMMARY_PREAMBLE = (
    "The user explored a different conversation branch before returning here.\n"
    "Summary of that exploration:\n\n"
)

_BRANCH_SUMMARY_PROMPT = """Create a structured summary of this conversation branch for context when returning later.

Use this EXACT format:

## Goal
[What was the user trying to accomplish in this branch?]

## Constraints & Preferences
- [Any constraints, preferences, or requirements mentioned]
- [Or "(none)" if none were mentioned]

## Progress
### Done
- [x] [Completed tasks/changes]

### In Progress
- [ ] [Work that was started but not finished]

### Blocked
- [Issues preventing progress, if any]

## Key Decisions
- **[Decision]**: [Brief rationale]

## Next Steps
1. [What should happen next to continue this work]

Keep each section concise. Preserve exact file paths, function names, and error messages."""


async def generate_branch_summary(
    entries: list[Any],
    model: Any,
    api_key: str,
    cancel_event: asyncio.Event | None = None,
    custom_instructions: str | None = None,
    replace_instructions: bool = False,
    reserve_tokens: int = 16384,
) -> BranchSummaryResult:
    """Generate a summary of abandoned branch entries."""
    from openprogram.agent.messages import convert_to_llm
    from openprogram.providers import complete_simple
    from openprogram.providers.types import Context, SimpleStreamOptions, TextContent, UserMessage

    context_window = getattr(model, "context_window", None) or 128000
    token_budget = context_window - reserve_tokens

    preparation = prepare_branch_entries(entries, token_budget)

    if not preparation.messages:
        return BranchSummaryResult(summary="No content to summarize")

    llm_messages = convert_to_llm(preparation.messages)
    conversation_text = serialize_conversation(llm_messages)

    if replace_instructions and custom_instructions:
        instructions = custom_instructions
    elif custom_instructions:
        instructions = f"{_BRANCH_SUMMARY_PROMPT}\n\nAdditional focus: {custom_instructions}"
    else:
        instructions = _BRANCH_SUMMARY_PROMPT

    prompt_text = f"<conversation>\n{conversation_text}\n</conversation>\n\n{instructions}"

    ctx = Context(
        system_prompt=SUMMARIZATION_SYSTEM_PROMPT,
        messages=[UserMessage(
            role="user",
            content=[TextContent(type="text", text=prompt_text)],
            timestamp=int(time.time() * 1000),
        )],
    )
    opts_kwargs: dict[str, Any] = {"max_tokens": 2048}
    if api_key:
        opts_kwargs["api_key"] = api_key
    if cancel_event is not None:
        opts_kwargs["signal"] = cancel_event
    opts = SimpleStreamOptions(**opts_kwargs)

    response = await complete_simple(model, ctx, opts)

    if getattr(response, "stop_reason", None) == "aborted":
        return BranchSummaryResult(aborted=True)
    if getattr(response, "stop_reason", None) == "error":
        return BranchSummaryResult(
            error=getattr(response, "error_message", None) or "Summarization failed"
        )

    content = getattr(response, "content", [])
    text_parts: list[str] = []
    for b in content if isinstance(content, list) else []:
        if isinstance(b, dict) and b.get("type") == "text":
            text_parts.append(b.get("text", ""))
        elif isinstance(b, TextContent):
            text_parts.append(b.text)
        elif hasattr(b, "text") and hasattr(b, "type") and getattr(b, "type", None) == "text":
            text_parts.append(getattr(b, "text", ""))
    summary_text = "\n".join(text_parts)

    summary = _BRANCH_SUMMARY_PREAMBLE + summary_text

    read_files, modified_files = compute_file_lists(preparation.file_ops)
    summary += format_file_operations(read_files, modified_files)

    return BranchSummaryResult(
        summary=summary or "No summary generated",
        read_files=read_files,
        modified_files=modified_files,
    )
