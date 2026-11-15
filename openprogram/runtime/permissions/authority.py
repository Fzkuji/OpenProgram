"""Runtime-owned speaker attribution and two-tier authorization.

The authority record itself (its fields, the schema stamp, the fixed
tier-capability table and the check against it) is stored data and lives
in :mod:`openprogram.state.authority`. This module builds on it: the
owner identity, the authority constructors and how speech is shown to the
model. It knows no
tool names; :mod:`openprogram.runtime.permissions.tool_authority` maps a
tool call onto one capability and decides it with this table.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote

from openprogram.state.authority import normalize_authority, sanitize_speaker_display


class AuthorityError(RuntimeError):
    pass


_OWNER_RE = re.compile(r"^owner/install/[0-9a-f]{16}$")
_MCP_CLIENT_RE = re.compile(r"^[0-9a-f]{16}$")
_owner_cache: dict[Path, str] = {}
_owner_lock = threading.Lock()


def _owner_path() -> Path:
    from openprogram.state.paths import get_state_dir

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


LOCAL_OWNER_SPEAKER = "owner/local"


def owner_authority(principal_id: str) -> dict[str, Any]:
    """Build owner authority for one validated installation principal."""
    if not _OWNER_RE.fullmatch(str(principal_id)):
        raise AuthorityError("owner principal ID is invalid")
    return {
        "speaker_kind": "owner",
        "speaker_id": LOCAL_OWNER_SPEAKER,
        "speaker_display": "Owner",
        "principal_id": str(principal_id),
        "authority_tier": "owner",
        "interaction": "interactive",
    }


def local_owner_authority() -> dict[str, Any]:
    return owner_authority(owner_principal_id())


_process_owner: str | None = None


def process_owner_principal_id() -> str:
    """The owner principal of a run that belongs to no profile.

    A host that imports the package and runs a function with its own model
    client is the owner of that run, and no profile is involved: the
    principal is drawn once per process and never stored, so the run reads
    and creates nothing under the profile directory. It equals no profile's
    owner, so what a profile keeps for its owner (schedules, approvals,
    updates) stays closed to it.
    """
    global _process_owner
    with _owner_lock:
        if _process_owner is None:
            _process_owner = f"owner/install/{uuid.uuid4().hex[:16]}"
        return _process_owner


def standalone_owner_authority(*, profile_model: bool) -> dict[str, Any]:
    """Owner authority for a run nobody sent a turn request for.

    ``profile_model`` says the run is on a model built from the profile's
    providers and credentials, so it is the profile owner's. Until a run
    uses one it is owned by the process: a host that brings its own model
    client (``Runtime(call=...)``), wherever it creates it, never opens a
    profile to find out who the owner is.
    """
    if profile_model:
        return local_owner_authority()
    return owner_authority(process_owner_principal_id())


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


def channel_owner_authority(
    channel: str,
    account_id: str,
    user_id: str,
    user_display: str,
) -> dict[str, Any]:
    """A chat-channel sender the owner treats as themselves.

    Used for a bound contact while the account's ``sender_access`` setting
    is ``owner``, in a direct message or on a group's full-access list: the
    turn gets the owner's tier and approvals, exactly as a turn typed in
    the local UI, while the speaker fields keep naming the platform sender
    so the transcript and the model still see who spoke.
    """
    if not all(str(value).strip() for value in (channel, account_id, user_id)):
        raise AuthorityError("channel owner authority requires platform stable IDs")
    return {
        "speaker_kind": "owner",
        "speaker_id": str(user_id),
        "speaker_display": sanitize_speaker_display(
            str(user_display or user_id)
        ),
        "principal_id": owner_principal_id(),
        "authority_tier": "owner",
        "interaction": "interactive",
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
        from openprogram.runtime.context.session_db import default_db

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


def turn_origin(session_id: str, turn_id: str) -> tuple[str, dict[str, Any]]:
    """The message that asked for the turn ``turn_id`` runs in, with its authority.

    A tool call runs under the assistant reply's id, and the reply carries
    the runtime's own non-interactive authority. What a tool may do on the
    caller's behalf, and which message memory cites as the request, comes
    from the message the reply answers: its predecessor. A turn id that is
    not a reply (a sub-agent prompt, a merge anchor) stands for itself, so
    a turn the runtime started keeps the runtime's authority.
    """
    if not session_id or not turn_id:
        return str(turn_id or ""), {}
    try:
        from openprogram.runtime.context.session_db import default_db

        messages = default_db().get_messages(session_id) or []
    except Exception:
        return str(turn_id), {}
    by_id = {str(row.get("id")): row for row in messages if isinstance(row, dict)}
    row = by_id.get(str(turn_id))
    if row is not None and row.get("role") == "assistant":
        predecessor = str(row.get("predecessor") or "")
        if predecessor in by_id:
            return predecessor, normalize_authority(by_id[predecessor])
    return str(turn_id), authority_from_message(session_id, str(turn_id))


__all__ = [
    "AuthorityError",
    "owner_principal_id",
    "owner_authority",
    "local_owner_authority",
    "process_owner_principal_id",
    "standalone_owner_authority",
    "channel_owner_authority",
    "paired_channel_authority",
    "mcp_client_authority",
    "runtime_authority",
    "authority_from_message",
    "turn_origin",
    "render_model_input",
    "render_model_input_from",
]
