"""Runtime-owned speaker attribution and authorization scope."""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote


class AuthorityError(RuntimeError):
    pass


LOCAL_OWNER_CAPABILITIES = frozenset({
    "reply",
    "memory.source.append",
    "memory.trusted.promote",
    "schedule.create",
    "schedule.manage",
    "fs.read",
    "fs.write",
    "process.exec",
    "network.send",
    "approval.request",
})
SHARED_CHANNEL_CAPABILITIES = frozenset({
    "reply", "memory.source.append",
})
UNKNOWN_EXTERNAL_CAPABILITIES = frozenset({"reply"})

_OWNER_RE = re.compile(r"^owner/install/[0-9a-f]{16}$")
_owner_cache: dict[Path, str] = {}
_owner_lock = threading.Lock()


def _owner_path() -> Path:
    from openprogram.paths import get_state_dir

    return Path(get_state_dir()) / "owner.json"


def _read_owner(path: Path) -> str:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AuthorityError(f"owner identity is unreadable: {path}") from exc
    principal = value.get("principal_id") if isinstance(value, dict) else None
    if not isinstance(principal, str) or not _OWNER_RE.fullmatch(principal):
        raise AuthorityError(f"owner identity is invalid: {path}")
    return principal


def owner_principal_id() -> str:
    """Return the stable per-profile owner principal, creating it once."""
    path = _owner_path()
    with _owner_lock:
        cached = _owner_cache.get(path)
        if cached is not None:
            return cached
        if path.exists():
            principal = _read_owner(path)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            principal = f"owner/install/{uuid.uuid4().hex[:16]}"
            payload = json.dumps(
                {"version": 1, "principal_id": principal},
                ensure_ascii=False,
                indent=2,
            ) + "\n"
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                principal = _read_owner(path)
            else:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                os.chmod(path, 0o600)
        _owner_cache[path] = principal
        return principal


def _reset_owner_cache_for_tests() -> None:
    _owner_cache.clear()


def _scope(origin: str, capabilities) -> dict[str, Any]:
    return {
        "origin": str(origin),
        "capabilities": sorted({str(item) for item in capabilities if str(item)}),
    }


def local_owner_authority() -> dict[str, Any]:
    return {
        "speaker_kind": "owner",
        "speaker_id": "owner/local",
        "speaker_display": "Owner",
        "principal_id": owner_principal_id(),
        "authority_scope": _scope("local-owner", LOCAL_OWNER_CAPABILITIES),
        "interaction": "interactive",
    }


def shared_channel_authority(
    channel: str,
    account_id: str,
    user_id: str,
    user_display: str,
) -> dict[str, Any]:
    trusted_speaker = bool(str(channel).strip() and str(account_id).strip()
                           and str(user_id).strip())
    capabilities = (SHARED_CHANNEL_CAPABILITIES if trusted_speaker
                    else UNKNOWN_EXTERNAL_CAPABILITIES)
    speaker_id = "unknown"
    if trusted_speaker:
        # Preserve the channel-provided stable ID byte-for-byte. Source v2
        # owns its existing percent-encoding and framing contract downstream.
        speaker_id = str(user_id)
    return {
        "speaker_kind": "human" if trusted_speaker else "unknown",
        "speaker_id": speaker_id,
        "speaker_display": str(user_display or user_id or "unknown"),
        "principal_id": owner_principal_id(),
        "authority_scope": _scope(
            "shared-channel" if trusted_speaker else "unknown-external",
            capabilities,
        ),
        "interaction": "shared",
    }


_AUTHORITY_FIELDS = (
    "speaker_kind", "speaker_id", "speaker_display", "principal_id",
    "authority_scope", "interaction",
)


def normalize_authority(value: Mapping[str, Any] | Any) -> dict[str, Any]:
    """Validate and copy authority fields; return {} when incomplete."""
    raw = value if isinstance(value, Mapping) else {
        key: getattr(value, key, None) for key in _AUTHORITY_FIELDS
    }
    strings = {
        key: raw.get(key) for key in _AUTHORITY_FIELDS if key != "authority_scope"
    }
    if not all(isinstance(item, str) and item.strip() for item in strings.values()):
        return {}
    scope = raw.get("authority_scope")
    if not isinstance(scope, Mapping):
        return {}
    origin = scope.get("origin")
    capabilities = scope.get("capabilities")
    if not isinstance(origin, str) or not origin.strip() \
            or not isinstance(capabilities, (list, tuple, set, frozenset)) \
            or not all(isinstance(item, str) and item for item in capabilities):
        return {}
    return {
        **{key: str(value) for key, value in strings.items()},
        "authority_scope": _scope(origin, capabilities),
    }


def runtime_authority(parent: Mapping[str, Any] | Any, source: str) -> dict[str, Any]:
    inherited = normalize_authority(parent)
    if not inherited:
        return {}
    inherited.update({
        "speaker_kind": "runtime",
        "speaker_id": f"runtime/{quote(str(source), safe='-_.')}",
        "speaker_display": str(source).replace("_", " "),
        "interaction": "non-interactive",
    })
    return inherited


def has_capability(value: Mapping[str, Any] | Any, capability: str) -> bool:
    authority = normalize_authority(value)
    return bool(authority and capability in authority["authority_scope"]["capabilities"])


def render_model_input(
    content: str,
    *,
    speaker_kind: str = "unknown",
    speaker_id: str = "unknown",
    speaker_display: str = "unknown",
) -> str:
    """Encode untrusted text as one value in the model-visible envelope."""
    return json.dumps({
        "speaker_kind": str(speaker_kind or "unknown"),
        "speaker_id": str(speaker_id or "unknown"),
        "speaker_display": str(speaker_display or "unknown"),
        "content": str(content or ""),
    }, ensure_ascii=False, separators=(",", ":"))


def render_model_input_from(value: Mapping[str, Any] | Any, content: str) -> str:
    authority = normalize_authority(value)
    return render_model_input(
        content,
        speaker_kind=authority.get("speaker_kind", "unknown"),
        speaker_id=authority.get("speaker_id", "unknown"),
        speaker_display=authority.get("speaker_display", "unknown"),
    )


def authority_from_message(session_id: str, message_id: str) -> dict[str, Any]:
    """Read authority persisted on a turn node; no ambient identity fallback."""
    if not session_id or not message_id:
        return {}
    try:
        from openprogram.agent.session_db import default_db

        messages = default_db().get_messages(session_id) or []
    except Exception:
        return {}
    by_id = {str(row.get("id")): row for row in messages if isinstance(row, dict)}
    row = by_id.get(str(message_id))
    authority = normalize_authority(row or {})
    if authority:
        return authority
    predecessor = (row or {}).get("predecessor")
    return normalize_authority(by_id.get(str(predecessor), {})) if predecessor else {}


_READ_TOOLS = {
    "read", "read_file", "grep", "glob", "list", "list_files",
    "memory_search", "memory_grep", "memory_get", "memory_browse",
    "memory_status", "read_conversation", "list_agents", "list_tasks",
}
_WRITE_TOOLS = {
    "write", "write_file", "edit", "edit_file", "apply_patch",
    "worktree_create", "worktree_merge", "worktree_discard", "worktree_keep",
}
_PROCESS_TOOLS = {
    "bash", "exec", "shell", "execute_code", "process", "agent",
    "gui_agent", "research_agent", "wiki_agent", "playwright_browser",
}
_NETWORK_TOOLS = {
    "send_message", "send_file", "web_search", "list_mcp_resources",
    "read_mcp_resource", "list_mcp_prompts", "get_mcp_prompt",
}
_REPLY_LOCAL_TOOLS = {
    "todo_create", "todo_update", "todo_list", "enter_plan_mode",
    "exit_plan_mode", "clarify", "skill", "tool_search", "task_output",
    "task_stop",
}


def capability_for_tool(tool_name: str, args: Mapping[str, Any] | None = None) -> str:
    name = str(tool_name or "")
    if name == "cron":
        action = str((args or {}).get("action") or "").lower()
        return "schedule.create" if action == "create" else (
            "schedule.manage" if action == "delete" else "fs.read"
        )
    if name == "memory_update" or name == "memory_promote":
        return "memory.trusted.promote"
    if name in _READ_TOOLS:
        return "fs.read"
    if name in _WRITE_TOOLS:
        return "fs.write"
    if name in _PROCESS_TOOLS:
        return "process.exec"
    if name in _NETWORK_TOOLS:
        return "network.send"
    if name in _REPLY_LOCAL_TOOLS:
        return "reply"
    # Installed agentic and MCP extensions may use arbitrary names. Treat an
    # unclassified extension as executable code: local-owner can still use
    # existing extensions, while shared and unknown external scopes cannot.
    return "process.exec"


__all__ = [
    "AuthorityError", "LOCAL_OWNER_CAPABILITIES", "SHARED_CHANNEL_CAPABILITIES",
    "owner_principal_id", "local_owner_authority", "shared_channel_authority",
    "runtime_authority", "normalize_authority",
    "authority_from_message", "has_capability",
    "render_model_input", "render_model_input_from", "capability_for_tool",
]
