"""
Custom message types and LLM converters for the agent layer.

Extends the core AgentMessage types (user/assistant/toolResult) with
specialized message kinds used for context compaction, tree navigation,
bash execution summaries, and extension-injected messages.

Originally ported from pi_coding_agent.core.messages.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

COMPACTION_SUMMARY_PREFIX = (
    "The conversation history before this point was compacted into the following summary:\n\n<summary>\n"
)
COMPACTION_SUMMARY_SUFFIX = "\n</summary>"

BRANCH_SUMMARY_PREFIX = (
    "The following is a summary of a branch that this conversation came back from:\n\n<summary>\n"
)
BRANCH_SUMMARY_SUFFIX = "</summary>"


@dataclass
class BashExecutionMessage:
    """Message type for bash executions via the ! command."""

    role: str = "bashExecution"
    command: str = ""
    output: str = ""
    exit_code: int | None = None
    cancelled: bool = False
    truncated: bool = False
    full_output_path: str | None = None
    timestamp: int = 0
    exclude_from_context: bool = False


@dataclass
class CustomMessage:
    """Message type for extension-injected messages via sendMessage()."""

    role: str = "custom"
    custom_type: str = ""
    content: str | list[dict[str, Any]] = ""
    display: bool = True
    details: Any = None
    timestamp: int = 0


@dataclass
class BranchSummaryMessage:
    """Message summarizing a branch that this conversation forked from."""

    role: str = "branchSummary"
    summary: str = ""
    from_id: str = ""
    timestamp: int = 0


@dataclass
class CompactionSummaryMessage:
    """Message containing a compacted conversation summary."""

    role: str = "compactionSummary"
    summary: str = ""
    tokens_before: int = 0
    timestamp: int = 0


def bash_execution_to_text(msg: BashExecutionMessage) -> str:
    """Convert a BashExecutionMessage to user message text for LLM context."""
    text = f"Ran `{msg.command}`\n"
    if msg.output:
        text += f"```\n{msg.output}\n```"
    else:
        text += "(no output)"
    if msg.cancelled:
        text += "\n\n(command cancelled)"
    elif msg.exit_code is not None and msg.exit_code != 0:
        text += f"\n\nCommand exited with code {msg.exit_code}"
    if msg.truncated and msg.full_output_path:
        text += f"\n\n[Output truncated. Full output: {msg.full_output_path}]"
    return text


def create_branch_summary_message(summary: str, from_id: str, timestamp: str) -> BranchSummaryMessage:
    from datetime import datetime
    ts = int(datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp() * 1000)
    return BranchSummaryMessage(summary=summary, from_id=from_id, timestamp=ts)


def create_compaction_summary_message(
    summary: str,
    tokens_before: int,
    timestamp: str,
) -> CompactionSummaryMessage:
    from datetime import datetime
    ts = int(datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp() * 1000)
    return CompactionSummaryMessage(summary=summary, tokens_before=tokens_before, timestamp=ts)


def create_custom_message(
    custom_type: str,
    content: str | list[dict[str, Any]],
    display: bool,
    details: Any,
    timestamp: str,
) -> CustomMessage:
    from datetime import datetime
    ts = int(datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp() * 1000)
    return CustomMessage(
        custom_type=custom_type,
        content=content,
        display=display,
        details=details,
        timestamp=ts,
    )


def convert_to_llm(messages: list[Any]) -> list[dict[str, Any]]:
    """
    Transform AgentMessages (including custom types) to LLM-compatible messages.

    Used by:
    - Agent's convert_to_llm option (for prompt calls and queued messages)
    - Compaction's generate_summary (for summarization)
    - Custom extensions and tools
    """
    result: list[dict[str, Any]] = []
    for m in messages:
        role = getattr(m, "role", None)

        if role == "bashExecution":
            if getattr(m, "exclude_from_context", False):
                continue
            result.append({
                "role": "user",
                "content": [{"type": "text", "text": bash_execution_to_text(m)}],
                "timestamp": getattr(m, "timestamp", 0),
            })

        elif role == "custom":
            content = m.content
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            result.append({
                "role": "user",
                "content": content,
                "timestamp": getattr(m, "timestamp", 0),
            })

        elif role == "branchSummary":
            result.append({
                "role": "user",
                "content": [{"type": "text", "text": BRANCH_SUMMARY_PREFIX + m.summary + BRANCH_SUMMARY_SUFFIX}],
                "timestamp": getattr(m, "timestamp", 0),
            })

        elif role == "compactionSummary":
            result.append({
                "role": "user",
                "content": [{"type": "text", "text": COMPACTION_SUMMARY_PREFIX + m.summary + COMPACTION_SUMMARY_SUFFIX}],
                "timestamp": getattr(m, "timestamp", 0),
            })

        elif role in ("user", "assistant", "toolResult"):
            if hasattr(m, "model_dump"):
                result.append(m.model_dump())
            elif hasattr(m, "__dict__"):
                result.append(dict(m.__dict__))
            else:
                result.append(m)

    return result


def wrap_convert_to_llm(block_images: bool):
    """
    Wrap convert_to_llm to optionally replace images with a placeholder.

    Deduplicates consecutive placeholder text blocks within each message's
    content array.
    """
    placeholder = "Image reading is disabled."

    def wrapped_convert(messages: list[Any]) -> list[dict[str, Any]]:
        converted = convert_to_llm(messages)

        if not block_images:
            return converted

        for msg in converted:
            role = msg.get("role")
            if role in ("user", "toolResult"):
                content = msg.get("content", [])
                if isinstance(content, list):
                    new_content = []
                    prev_was_placeholder = False

                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "image":
                            if not prev_was_placeholder:
                                new_content.append({"type": "text", "text": placeholder})
                                prev_was_placeholder = True
                        else:
                            new_content.append(c)
                            prev_was_placeholder = (
                                isinstance(c, dict)
                                and c.get("type") == "text"
                                and c.get("text") == placeholder
                            )

                    msg["content"] = new_content

        return converted

    return wrapped_convert
