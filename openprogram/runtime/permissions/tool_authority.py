"""Which capability a tool call needs, decided against the authority table.

:mod:`openprogram.runtime.permissions.authority` owns the tiers and what
each one may do; this module knows tool names. Every tool call maps onto
exactly one capability, and an unclassified tool needs ``process.exec``.
The permission decision (``policy.permission_decision``) and the MCP server
check a call with :func:`decide_tool_authority`.
"""
from __future__ import annotations

from typing import Any, Mapping

from openprogram.state.authority import AuthorityDecision, decide_capability, normalize_authority

_READ_TOOLS = {
    "read", "read_file", "grep", "glob", "list", "list_files",
    "read_conversation", "list_agents", "list_jobs",
    # Attaching a file to the turn's own conversation is replying with it,
    # not a send to a third party. It shows the file's bytes to whoever
    # the turn answers, the same reach as reading it and quoting it, so it
    # needs ``fs.read``: the owner has it, and a paired chat-app speaker,
    # who may not read the owner's files, may not be sent them either.
    "send_file",
}
# Reads that stay inside the memory workspace. They reach no other part
# of the filesystem, so they are gated on ``memory.read`` rather than on
# ``fs.read``.
_MEMORY_READ_TOOLS = {"memory_search", "memory_get"}
_WRITE_TOOLS = {
    "write", "write_file", "edit", "edit_file", "apply_patch",
    "worktree_create", "worktree_merge", "worktree_discard", "worktree_keep",
}
_PROCESS_TOOLS = {
    "bash", "exec", "shell", "execute_code", "write_stdin", "agent",
}
_NETWORK_TOOLS = {
    "send_message", "web_search", "list_mcp_resources",
    "read_mcp_resource", "list_mcp_prompts", "get_mcp_prompt",
}
# Posting into the chat the turn came from is the same act as the reply
# itself, so it needs only ``reply``.
_CHAT_REPLY_TOOLS = {"chat_send"}
_REPLY_LOCAL_TOOLS = {
    "task_start", "task_step", "task_list", "open_work", "enter_plan_mode",
    "exit_plan_mode", "clarify", "skill", "help", "tool_search", "job_output",
    "job_stop", "close_agent",
}


def capability_for_tool(tool_name: str, args: Mapping[str, Any] | None = None) -> str:
    name = str(tool_name or "")
    if name in {"cron", "scheduler"}:
        action = str((args or {}).get("action") or "").lower()
        return "schedule.create" if action == "create" else (
            "schedule.manage" if action in {"update", "delete"} else "fs.read"
        )
    if name == "remind":
        return "schedule.create"
    if name == "save_routine":
        # Writes a Skill file; with a cron schedule it also creates a task.
        return "schedule.create" if (args or {}).get("cron") else "fs.write"
    if name == "self_update":
        from openprogram.programs.tools.system.self_update import action_of
        action = action_of(args)
        return "fs.read" if action in ("status", "versions") else (
            "runtime.control" if action == "cancel" else "process.exec")
    if name == "memory_update":
        return "memory.source.append"
    if name in _CHAT_REPLY_TOOLS:
        return "reply"
    if name in _MEMORY_READ_TOOLS:
        return "memory.read"
    if name in _READ_TOOLS:
        return "fs.read"
    if name in _WRITE_TOOLS:
        return "fs.write"
    if name in _PROCESS_TOOLS:
        return "process.exec"
    if name == "computer_use_agent":
        # Built-in browser Pages are browser control; application windows, a
        # VM and code reach further and stay with the owner.
        call = args or {}
        page_only = call.get("target") == "browser" and not call.get("code")
        return "browser.control" if page_only else "process.exec"
    if name in _NETWORK_TOOLS:
        return "network.send"
    if name in _REPLY_LOCAL_TOOLS:
        return "runtime.control"
    # Installed agentic and MCP extensions may use arbitrary names. Treat an
    # unclassified extension as executable code: only the owner tier holds
    # process.exec, so a paired speaker cannot reach an unclassified tool.
    return "process.exec"


# Their capability depends on the call's arguments, so identity alone cannot hide them.
_ARGUMENT_DEPENDENT_TOOLS = {"cron", "scheduler", "save_routine", "self_update", "computer_use_agent"}


def static_capability(tool_name: str) -> str | None:
    """The one capability a tool always needs, or ``None`` when its arguments decide."""
    return None if tool_name in _ARGUMENT_DEPENDENT_TOOLS else capability_for_tool(tool_name)


def lacks_static_capability(value: Mapping[str, Any] | Any, tool_name: str) -> bool:
    """Whether every call of this tool would be refused for this identity."""
    capability = static_capability(tool_name)
    if capability is None or not normalize_authority(value):
        # A request with no identity is refused at the call, which says why.
        return False
    return not decide_capability(value, capability).allowed


def decide_tool_authority(
    value: Mapping[str, Any] | Any,
    tool_name: str,
    args: Mapping[str, Any] | None = None,
) -> AuthorityDecision:
    return decide_capability(value, capability_for_tool(tool_name, args))


__all__ = ["capability_for_tool", "decide_tool_authority", "lacks_static_capability", "static_capability"]
