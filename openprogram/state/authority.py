"""The authority record a stored turn carries, and what each tier may do.

Every turn node this build persists names its speaker (kind, id, display
name, principal, tier, interaction) and carries a schema stamp. This
module is that record: its fields, the stamp that separates legacy nodes
from new ones, the fixed tier-capability table and the check against it.
It also says how a stored speaker's words are shown to a model (the
envelope, :func:`render_model_input_from`, and its undoing,
:func:`unwrap_owner_input`) and the record a runtime-written turn inherits
from the one it follows (:func:`runtime_authority`). Store-side readers
(memory deciding which turns may become memory) use it directly; the
runtime's ``openprogram.runtime.permissions.authority`` builds on it with
the owner identity and the authority constructors that need it.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from typing import Any, Literal, Mapping
from urllib.parse import quote

_log = logging.getLogger(__name__)


AuthorityTier = Literal["owner", "paired"]

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
    "schedule.create",
    "schedule.manage",
    "fs.read",
    "fs.write",
    "process.exec",
    "network.send",
    "approval.request",
    "runtime.control",
    "runtime.wait",
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
    "runtime.wait",
})
TIER_CAPABILITIES: Mapping[AuthorityTier, frozenset[str]] = {
    "owner": _OWNER_CAPABILITIES,
    "paired": _PAIRED_CAPABILITIES,
}

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
    # An allowed decision happens on every request; only a refusal is worth a
    # line in the worker log.
    _log.log(logging.DEBUG if decision.allowed else logging.INFO,
             "authority decision %s", decision.to_dict())
    return decision


def sanitize_speaker_display(value: str) -> str:
    from openprogram.state.identity_header import normalize_identity_header_part

    return normalize_identity_header_part(str(value or "")) or "unknown"


#: The speaker id of the person who owns this installation, typing locally.
LOCAL_OWNER_SPEAKER = "owner/local"


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


def render_outside_input(value: Mapping[str, Any] | Any, content: str) -> str:
    """What an outside agent (Codex, Claude Code, DeepSeek Harness) is sent for a message.

    The local owner's words go as typed: the agent does not know the envelope and
    keeps the message in its own transcript, where the person reads it again.
    Anyone else's, a channel's full-access members' included, still go in the
    envelope, so the agent can tell who spoke and no one passes for the owner."""
    authority = normalize_authority(value)
    if not authority or authority.get("speaker_id") == LOCAL_OWNER_SPEAKER:
        return content
    return render_model_input_from(authority, content)


_ENVELOPE_START = '{"speaker_kind":"owner","speaker_id":"owner/local","speaker_display":"Owner","content":'


def unwrap_owner_input(text: str) -> str:
    """``text`` with the local owner's envelope (sent to an outside agent before
    :func:`render_outside_input`) replaced by the words in it. The envelope opens
    the text or follows a handoff note and a blank line; what the agent added
    after it (its picture placeholders) stays. An envelope whose end a credential
    redaction took still gives its words."""
    text = text or ""
    at = text.find(_ENVELOPE_START)
    if at < 0 or (at and not text[:at].endswith("\n\n")):
        return text
    try:
        value, end = json.JSONDecoder().raw_decode(text, at)
    except ValueError:
        value, end = None, -1
    if isinstance(value, dict) and set(value) == _ENVELOPE_KEYS and isinstance(value.get("content"), str):
        return text[:at] + value["content"] + text[end:]
    # Cut short: the words run to the string's closing quote, or to the end when
    # redaction took it; what follows the envelope stays as it is.
    body = text[at + len(_ENVELOPE_START):]
    if not body.startswith('"'):
        return text
    end, escaped = len(body), False
    for index in range(1, len(body)):
        char = body[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            end = index
            break
    words, tail = body[1:end], body[end + 1:]
    tail = tail[1:] if tail.startswith("}") else tail
    try:
        words = json.loads(f'"{words}"', strict=False)
    except ValueError:
        pass
    return text[:at] + words + tail


_ENVELOPE_KEYS = {"speaker_kind", "speaker_id", "speaker_display", "content"}


__all__ = [
    "LOCAL_OWNER_SPEAKER", "runtime_authority",
    "render_model_input", "render_model_input_from", "render_outside_input",
    "unwrap_owner_input",
    "AuthorityDecision", "AuthorityTier", "TIER_CAPABILITIES",
    "MESSAGE_SCHEMA_VERSION", "MESSAGE_SCHEMA_FIELD",
    "stamp_schema", "is_legacy_message",
    "normalize_authority", "has_capability", "decide_capability",
    "sanitize_speaker_display",
]
