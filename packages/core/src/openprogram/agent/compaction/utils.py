"""
Shared utilities for compaction and branch summarization.

Tracks file operations across agent messages, formats file lists, serializes
conversations for summarization. Self-contained — no provider dependencies.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FileOperations:
    """Tracks file read/write/edit operations across agent messages."""

    read: set[str] = field(default_factory=set)
    written: set[str] = field(default_factory=set)
    edited: set[str] = field(default_factory=set)


def create_file_ops() -> FileOperations:
    return FileOperations()


def extract_file_ops_from_message(message: Any, file_ops: FileOperations) -> None:
    """Extract file operations from tool calls in an assistant message."""
    if not hasattr(message, "role") or message.role != "assistant":
        return

    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return

    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "toolCall":
            continue

        name = block.get("name", "")
        args = block.get("arguments", {})
        if not isinstance(args, dict):
            continue

        path = args.get("path")
        if not isinstance(path, str) or not path:
            continue

        if name == "read":
            file_ops.read.add(path)
        elif name == "write":
            file_ops.written.add(path)
        elif name == "edit":
            file_ops.edited.add(path)


def compute_file_lists(
    file_ops: FileOperations,
) -> tuple[list[str], list[str]]:
    """Compute final file lists from file operations.

    Returns (read_files, modified_files) — read_files only read (not modified),
    modified_files are written or edited.
    """
    modified = file_ops.edited | file_ops.written
    read_only = sorted(f for f in file_ops.read if f not in modified)
    modified_files = sorted(modified)
    return read_only, modified_files


def format_file_operations(read_files: list[str], modified_files: list[str]) -> str:
    """Format file operations as XML tags for inclusion in summary."""
    sections: list[str] = []
    if read_files:
        sections.append(f"<read-files>\n{chr(10).join(read_files)}\n</read-files>")
    if modified_files:
        sections.append(f"<modified-files>\n{chr(10).join(modified_files)}\n</modified-files>")
    if not sections:
        return ""
    return "\n\n" + "\n\n".join(sections)


_TOOL_RESULT_MAX_CHARS = 2000


def _truncate_for_summary(text: str, max_chars: int) -> str:
    """Truncate text to max_chars for summarization to prevent context overflow."""
    if len(text) <= max_chars:
        return text
    truncated = len(text) - max_chars
    return f"{text[:max_chars]}\n\n[... {truncated} more characters truncated]"


def serialize_conversation(messages: list[Any]) -> str:
    """Serialize LLM messages to text for summarization.

    Formats as [Role]: text blocks so the model treats it as data to summarize,
    not a conversation to continue.
    """
    parts: list[str] = []

    for msg in messages:
        role = getattr(msg, "role", None)

        if role == "user":
            content = getattr(msg, "content", "")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = "".join(
                    b.get("text", "") if isinstance(b, dict) else ""
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            else:
                text = ""
            if text:
                parts.append(f"[User]: {text}")

        elif role == "assistant":
            content = getattr(msg, "content", [])
            text_parts: list[str] = []
            thinking_parts: list[str] = []
            tool_calls: list[str] = []

            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    text_parts.append(block.get("text", ""))
                elif btype == "thinking":
                    thinking_parts.append(block.get("thinking", ""))
                elif btype == "toolCall":
                    args = block.get("arguments", {})
                    args_str = ", ".join(
                        f"{k}={json.dumps(v)}" for k, v in (args.items() if isinstance(args, dict) else [])
                    )
                    tool_calls.append(f"{block.get('name', '')}({args_str})")

            if thinking_parts:
                parts.append(f"[Assistant thinking]: {chr(10).join(thinking_parts)}")
            if text_parts:
                parts.append(f"[Assistant]: {chr(10).join(text_parts)}")
            if tool_calls:
                parts.append(f"[Assistant tool calls]: {'; '.join(tool_calls)}")

        elif role == "toolResult":
            content = getattr(msg, "content", [])
            text = "".join(
                b.get("text", "") if isinstance(b, dict) else ""
                for b in (content if isinstance(content, list) else [])
                if isinstance(b, dict) and b.get("type") == "text"
            )
            if text:
                parts.append(f"[Tool result]: {_truncate_for_summary(text, _TOOL_RESULT_MAX_CHARS)}")

    return "\n\n".join(parts)


SUMMARIZATION_SYSTEM_PROMPT = (
    "You are a context summarization assistant. Your task is to read a conversation between "
    "a user and an AI coding assistant, then produce a structured summary following the exact "
    "format specified.\n\n"
    "Do NOT continue the conversation. Do NOT respond to any questions in the conversation. "
    "ONLY output the structured summary."
)
