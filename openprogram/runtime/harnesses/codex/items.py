"""Codex thread items drawn as OpenProgram's tool cards.

A command is a ``bash`` card, a file change an ``apply_patch`` card whose
patch is built from Codex's unified diffs, a web search a provider-run
``web_search``, an MCP call ``mcp__<server>__<tool>`` and the plan the
``todo_write`` checklist. An item with no card returns None.
"""
from __future__ import annotations

import json
import shlex
from typing import Any, Optional

#: The login-shell wrappers Codex runs a command in.
_SHELL_WRAPPERS = ("/bin/zsh -lc ", "/bin/bash -lc ", "/bin/sh -lc ", "zsh -lc ", "bash -lc ", "sh -lc ")

#: Item types that are text, reasoning or bookkeeping, never a card.
TEXT_ITEMS = frozenset({"userMessage", "agentMessage", "reasoning", "contextCompaction", "hookPrompt"})


def shown_command(command: Any) -> str:
    """The command as typed, without Codex's ``/bin/zsh -lc '…'`` wrapper."""
    if isinstance(command, list):
        command = " ".join(shlex.quote(str(part)) for part in command)
    text = str(command or "")
    for wrapper in _SHELL_WRAPPERS:
        if text.startswith(wrapper):
            inner = text[len(wrapper):]
            try:
                parts = shlex.split(inner)
            except ValueError:
                return inner
            return parts[0] if len(parts) == 1 else inner
    return text


def _diff_lines(diff: str) -> list[str]:
    """The ``+``/``-``/context lines of a unified diff, headers dropped."""
    lines = []
    for line in (diff or "").splitlines():
        if line.startswith(("--- ", "+++ ", "diff --git", "index ", "new file mode", "deleted file mode")):
            continue
        lines.append(line)
    return lines


def patch_envelope(changes: list[dict]) -> str:
    """An ``*** Begin Patch`` envelope of Codex's changes, as ``apply_patch`` takes."""
    out = ["*** Begin Patch"]
    for change in changes or []:
        if not isinstance(change, dict):
            continue
        path = str(change.get("path") or "")
        kind = change.get("kind") if isinstance(change.get("kind"), dict) else {"type": change.get("kind")}
        operation = str(kind.get("type") or "update")
        diff = str(change.get("diff") or "")
        if operation == "add":
            out.append(f"*** Add File: {path}")
            body = _diff_lines(diff)
            if any(line.startswith("@@") for line in body):
                out.extend(line for line in body if line.startswith("+"))
            else:
                out.extend("+" + line for line in diff.splitlines())
        elif operation == "delete":
            out.append(f"*** Delete File: {path}")
        else:
            out.append(f"*** Update File: {path}")
            if kind.get("move_path"):
                out.append(f"*** Move to: {kind['move_path']}")
            out.extend(_diff_lines(diff))
    out.append("*** End Patch")
    return "\n".join(out)


def _status_outcome(status: Any) -> tuple[bool, str]:
    """``(is_error, outcome)`` of an item's final status."""
    status = str(status or "completed")
    if status in ("failed", "error"):
        return True, "failed"
    if status == "declined":
        return True, "failed"
    if status in ("cancelled", "interrupted"):
        return True, "cancelled"
    return False, "completed"


def plan_text(plan: list[dict]) -> str:
    marks = {"completed": "[x]", "inProgress": "[~]", "in_progress": "[~]"}
    return "\n".join(f"{marks.get(str(step.get('status')), '[ ]')} {step.get('step', '')}"
                     for step in plan if isinstance(step, dict))


def plan_input(plan: list[dict], explanation: Optional[str] = None) -> dict:
    todos = []
    for step in plan or []:
        if not isinstance(step, dict):
            continue
        status = str(step.get("status") or "pending")
        todos.append({
            "content": str(step.get("step") or ""),
            "status": "in_progress" if status in ("inProgress", "in_progress") else status,
        })
    card: dict = {"todos": todos}
    if explanation:
        card["title"] = explanation
    return card


class ItemCard:
    """One item's card: its tool name, input, and (once ended) result."""

    def __init__(self, tool: str, arguments: dict, *, provider_executed: bool = False) -> None:
        self.tool = tool
        self.arguments = arguments
        self.provider_executed = provider_executed

    @property
    def input(self) -> str:
        return json.dumps(self.arguments, ensure_ascii=False, default=str)


def card_for(item: dict) -> Optional[ItemCard]:
    """The card an item starts, or None for text and items with no card."""
    kind = item.get("type")
    if kind in TEXT_ITEMS or not kind:
        return None
    if kind == "commandExecution":
        return ItemCard("bash", {"command": shown_command(item.get("command")),
                                 **({"workdir": item["cwd"]} if item.get("cwd") else {})})
    if kind == "fileChange":
        changes = item.get("changes") or []
        first = next((c.get("path") for c in changes if isinstance(c, dict) and c.get("path")), None)
        arguments = {"input": patch_envelope(changes)}
        if first:
            arguments["path"] = first
        return ItemCard("apply_patch", arguments)
    if kind == "webSearch":
        return ItemCard("web_search", {"query": str(item.get("query") or "")}, provider_executed=True)
    if kind == "mcpToolCall":
        arguments = item.get("arguments")
        return ItemCard(f"mcp__{item.get('server') or 'server'}__{item.get('tool') or 'tool'}",
                        arguments if isinstance(arguments, dict) else {"arguments": arguments})
    if kind == "dynamicToolCall":
        arguments = item.get("arguments")
        return ItemCard(str(item.get("tool") or "tool"),
                        arguments if isinstance(arguments, dict) else {"arguments": arguments})
    if kind == "imageView":
        return ItemCard("read", {"path": str(item.get("path") or "")})
    if kind == "plan":
        return None  # turn/plan/updated carries the steps
    return None


def result_of(item: dict) -> tuple[str, bool, str]:
    """``(result text, is_error, outcome)`` of an ended item."""
    kind = item.get("type")
    is_error, outcome = _status_outcome(item.get("status"))
    if kind == "commandExecution":
        output = str(item.get("aggregatedOutput") or "")
        exit_code = item.get("exitCode")
        if exit_code not in (None, 0):
            is_error, outcome = True, "failed"
            output = (output + ("\n" if output and not output.endswith("\n") else "")
                      + f"Exit code {exit_code}")
        return output, is_error, outcome
    if kind == "fileChange":
        paths = [str(c.get("path")) for c in item.get("changes") or [] if isinstance(c, dict)]
        if item.get("status") == "declined":
            return "Declined: " + ", ".join(paths), True, "failed"
        return ("Updated " + ", ".join(paths)) if paths else "", is_error, outcome
    if kind == "webSearch":
        action = item.get("action")
        if isinstance(action, dict):
            urls = [str(u) for u in (action.get("urls") or []) if u]
            if action.get("url"):
                urls.insert(0, str(action["url"]))
            if urls:
                return "\n".join(urls), is_error, outcome
        return str(item.get("query") or ""), is_error, outcome
    if kind in ("mcpToolCall", "dynamicToolCall"):
        error = item.get("error")
        if error:
            message = error.get("message") if isinstance(error, dict) else error
            return str(message), True, "failed"
        result = item.get("result") if kind == "mcpToolCall" else item.get("contentItems")
        return _content_text(result), is_error, outcome
    if kind == "imageView":
        return str(item.get("path") or ""), is_error, outcome
    return "", is_error, outcome


def _content_text(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, dict) and isinstance(result.get("content"), list):
        result = result["content"]
    if isinstance(result, list):
        parts = []
        for block in result:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
            else:
                parts.append(json.dumps(block, ensure_ascii=False, default=str))
        return "\n".join(parts)
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False, default=str)


def reasoning_text(item: dict) -> str:
    summary = item.get("summary") or []
    content = item.get("content") or []
    parts = [str(part) for part in (summary or content) if part]
    return "\n\n".join(parts)
