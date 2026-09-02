"""Runtime-owned speaker attribution and two-tier authorization."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, Mapping
from urllib.parse import quote


class AuthorityError(RuntimeError):
    pass


AuthorityTier = Literal["owner", "paired", "mcp_browser"]

# Stamped on every turn node this build persists. Its presence is what
# separates "written before authority existed" from "written now and
# missing it": without a positive marker the two are the same absence,
# and a permanent "no field means legacy" rule would keep accepting
# unattributed writes forever — including any a bug started producing
# tomorrow. Legacy nodes are the closed set that predates this constant.
MESSAGE_SCHEMA_VERSION = 1
MESSAGE_SCHEMA_FIELD = "authority_schema"


def stamp_schema(message: dict[str, Any]) -> dict[str, Any]:
    """Mark a turn node as written by a build that records authority."""
    message[MESSAGE_SCHEMA_FIELD] = MESSAGE_SCHEMA_VERSION
    return message


def is_legacy_message(value: Mapping[str, Any] | Any) -> bool:
    """Whether a node predates authority stamping and may lack it."""
    raw = value if isinstance(value, Mapping) else {}
    return raw.get(MESSAGE_SCHEMA_FIELD) is None

_OWNER_CAPABILITIES = frozenset({
    "reply",
    "memory.read",
    "memory.source.append",
    "memory.trusted.promote",
    "schedule.create",
    "schedule.manage",
    "fs.read",
    "fs.write",
    "process.exec",
    "network.send",
    "approval.request",
    "runtime.control",
    "browser.control",
})
# Reading memory is its own capability, separate from ``fs.read``. A
# paired speaker gets the first and not the second: they may ask what
# memory holds about the conversation they are part of, which is what
# makes them useful to talk to, without thereby gaining the ability to
# read arbitrary files off the owner's disk. Folding memory reads into
# ``fs.read`` forced a choice between those two, and the safe answer
# there was to deny both.
_PAIRED_CAPABILITIES = frozenset({
    "reply", "memory.read", "memory.source.append",
})
_MCP_BROWSER_CAPABILITIES = frozenset({"browser.control"})
TIER_CAPABILITIES: Mapping[AuthorityTier, frozenset[str]] = {
    "owner": _OWNER_CAPABILITIES,
    "paired": _PAIRED_CAPABILITIES,
    "mcp_browser": _MCP_BROWSER_CAPABILITIES,
}

_OWNER_RE = re.compile(r"^owner/install/[0-9a-f]{16}$")
_MCP_CLIENT_RE = re.compile(r"^[0-9a-f]{16}$")
_owner_cache: dict[Path, str] = {}
_owner_lock = threading.Lock()
_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthorityDecision:
    """One auditable result from the fixed tier-capability table."""

    allowed: bool
    check: str
    reason_code: str
    tier: str | None
    capability: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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


def owner_authority(principal_id: str) -> dict[str, Any]:
    """Build owner authority for one validated installation principal."""
    if not _OWNER_RE.fullmatch(str(principal_id)):
        raise AuthorityError("owner principal ID is invalid")
    return {
        "speaker_kind": "owner",
        "speaker_id": "owner/local",
        "speaker_display": "Owner",
        "principal_id": str(principal_id),
        "authority_tier": "owner",
        "interaction": "interactive",
    }


def local_owner_authority() -> dict[str, Any]:
    return owner_authority(owner_principal_id())


def paired_channel_authority(
    channel: str,
    account_id: str,
    user_id: str,
    user_display: str,
) -> dict[str, Any]:
    if not all(str(value).strip() for value in (channel, account_id, user_id)):
        raise AuthorityError("paired authority requires platform stable IDs")
    # Preserve the channel-provided stable ID byte-for-byte. Source v2 owns
    # its existing percent-encoding and framing contract downstream.
    speaker_id = str(user_id)
    return {
        "speaker_kind": "human",
        "speaker_id": speaker_id,
        "speaker_display": sanitize_speaker_display(
            str(user_display or user_id)
        ),
        "principal_id": owner_principal_id(),
        "authority_tier": "paired",
        "interaction": "non-interactive",
    }


def mcp_client_authority(client_id: str) -> dict[str, Any]:
    """Build fixed paired authority for one credential-derived MCP client."""
    if not isinstance(client_id, str) or not _MCP_CLIENT_RE.fullmatch(client_id):
        raise AuthorityError("MCP client ID is invalid")
    return {
        "speaker_kind": "client",
        "speaker_id": f"mcp/{client_id}",
        "speaker_display": "MCP client",
        "principal_id": owner_principal_id(),
        "authority_tier": "paired",
        "interaction": "non-interactive",
    }


def mcp_web_control_authority(value: Mapping[str, Any]) -> dict[str, Any]:
    """Narrow authority used only by the first-class MCP web_use route."""
    authority = normalize_authority(value)
    if (
        authority.get("speaker_kind") != "client"
        or authority.get("authority_tier") != "paired"
        or not str(authority.get("speaker_id") or "").startswith("mcp/")
    ):
        raise AuthorityError("MCP browser-control authority is invalid")
    return {**authority, "authority_tier": "mcp_browser"}


_AUTHORITY_FIELDS = (
    "speaker_kind", "speaker_id", "speaker_display", "principal_id",
    "authority_tier", "interaction",
)


def normalize_authority(value: Mapping[str, Any] | Any) -> dict[str, Any]:
    """Validate and copy authority fields; return {} when incomplete."""
    raw = value if isinstance(value, Mapping) else {
        key: getattr(value, key, None) for key in _AUTHORITY_FIELDS
    }
    strings = {key: raw.get(key) for key in _AUTHORITY_FIELDS}
    if not all(isinstance(item, str) and item.strip() for item in strings.values()):
        return {}
    if strings["authority_tier"] not in TIER_CAPABILITIES:
        return {}
    normalized = {key: str(item) for key, item in strings.items()}
    normalized["speaker_display"] = sanitize_speaker_display(
        normalized["speaker_display"]
    )
    return normalized


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
    return decide_capability(value, capability).allowed


def _raw_tier(value: Mapping[str, Any] | Any) -> Any:
    if isinstance(value, Mapping):
        return value.get("authority_tier")
    return getattr(value, "authority_tier", None)


def decide_capability(
    value: Mapping[str, Any] | Any,
    capability: str,
) -> AuthorityDecision:
    raw_tier = _raw_tier(value)
    if raw_tier is None:
        decision = AuthorityDecision(
            False, "tier_capability_table",
            "AUTHORITY_TIER_MISSING", None, str(capability),
        )
    elif not isinstance(raw_tier, str) or raw_tier not in TIER_CAPABILITIES:
        decision = AuthorityDecision(
            False, "tier_capability_table",
            "AUTHORITY_TIER_UNKNOWN", str(raw_tier), str(capability),
        )
    else:
        allowed = str(capability) in TIER_CAPABILITIES[raw_tier]
        decision = AuthorityDecision(
            allowed, "tier_capability_table",
            "AUTHORITY_ALLOWED" if allowed else "AUTHORITY_CAPABILITY_DENIED",
            str(raw_tier), str(capability),
        )
    _log.info("authority decision %s", decision.to_dict())
    return decision


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
        "speaker_display": sanitize_speaker_display(
            str(speaker_display or "unknown")
        ),
        "content": str(content or ""),
    }, ensure_ascii=False, separators=(",", ":"))


def sanitize_speaker_display(value: str) -> str:
    from openprogram._text import normalize_identity_header_part

    return normalize_identity_header_part(str(value or "")) or "unknown"


def render_model_input_from(value: Mapping[str, Any] | Any, content: str) -> str:
    # The JSON envelope exists to attribute channel speech; a node with no
    # authority metadata is the local owner's own turn (or pre-authority
    # history) and renders as plain text — wrapping it would present the
    # owner to the model as an unknown speaker on every turn.
    authority = normalize_authority(value)
    if not authority:
        return content
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
    "read_conversation", "list_agents", "list_jobs",
}
# Reads that stay inside the memory workspace. They reach no other part
# of the filesystem, so they are gated on ``memory.read`` rather than on
# ``fs.read``.
_MEMORY_READ_TOOLS = {
    "memory_search", "memory_grep", "memory_get", "memory_browse",
    "memory_status",
}
_WRITE_TOOLS = {
    "write", "write_file", "edit", "edit_file", "apply_patch",
    "worktree_create", "worktree_merge", "worktree_discard", "worktree_keep",
}
_PROCESS_TOOLS = {
    "bash", "exec", "shell", "execute_code", "process", "agent",
    "gui_agent", "research_agent", "wiki_agent", "browser_agent",
    "playwright_browser",
}
_BROWSER_CONTROL_TOOLS = {"web_use"}
_NETWORK_TOOLS = {
    "send_message", "send_file", "web_search", "list_mcp_resources",
    "read_mcp_resource", "list_mcp_prompts", "get_mcp_prompt",
}
_REPLY_LOCAL_TOOLS = {
    "todo_create", "todo_update", "todo_list", "enter_plan_mode",
    "exit_plan_mode", "clarify", "skill", "tool_search", "job_output",
}


def capability_for_tool(tool_name: str, args: Mapping[str, Any] | None = None) -> str:
    name = str(tool_name or "")
    if name in {"cron", "scheduler"}:
        action = str((args or {}).get("action") or "").lower()
        return "schedule.create" if action == "create" else (
            "schedule.manage" if action in {"update", "delete"} else "fs.read"
        )
    if name == "memory_update":
        # memory_update requires the revision returned by memory_status;
        # both calls form the append handshake and expose no memory content.
        return "memory.source.append"
    if name == "memory_promote":
        return "memory.trusted.promote"
    if name in _MEMORY_READ_TOOLS:
        return "memory.read"
    if name in _READ_TOOLS:
        return "fs.read"
    if name in _WRITE_TOOLS:
        return "fs.write"
    if name in _PROCESS_TOOLS:
        return "process.exec"
    if name in _BROWSER_CONTROL_TOOLS:
        return "browser.control"
    if name in _NETWORK_TOOLS:
        return "network.send"
    if name in _REPLY_LOCAL_TOOLS:
        return "runtime.control"
    # Installed agentic and MCP extensions may use arbitrary names. Treat an
    # unclassified extension as executable code: only the owner tier holds
    # process.exec, so a paired speaker cannot reach an unclassified tool.
    return "process.exec"


def decide_tool_authority(
    value: Mapping[str, Any] | Any,
    tool_name: str,
    args: Mapping[str, Any] | None = None,
) -> AuthorityDecision:
    return decide_capability(value, capability_for_tool(tool_name, args))


__all__ = [
    "AuthorityError", "AuthorityDecision", "AuthorityTier", "TIER_CAPABILITIES",
    "MESSAGE_SCHEMA_VERSION", "MESSAGE_SCHEMA_FIELD",
    "stamp_schema", "is_legacy_message",
    "owner_principal_id", "owner_authority", "local_owner_authority",
    "mcp_web_control_authority",
    "paired_channel_authority", "mcp_client_authority",
    "runtime_authority", "normalize_authority",
    "authority_from_message", "has_capability", "decide_capability",
    "render_model_input", "render_model_input_from", "sanitize_speaker_display",
    "capability_for_tool", "decide_tool_authority",
]
