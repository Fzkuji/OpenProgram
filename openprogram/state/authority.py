"""The authority record a stored turn carries, and what each tier may do.

Every turn node this build persists names its speaker (kind, id, display
name, principal, tier, interaction) and carries a schema stamp. This
module is that record: its fields, the stamp that separates legacy nodes
from new ones, the fixed tier-capability table and the check against it.
Store-side readers (memory deciding which turns may become memory) use it
directly; the runtime's ``openprogram.runtime.permissions.authority``
builds on it with the owner identity, the authority constructors and how
speech is shown to the model.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any, Literal, Mapping

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
    from openprogram.host.security.identity_header import normalize_identity_header_part

    return normalize_identity_header_part(str(value or "")) or "unknown"


__all__ = [
    "AuthorityDecision", "AuthorityTier", "TIER_CAPABILITIES",
    "MESSAGE_SCHEMA_VERSION", "MESSAGE_SCHEMA_FIELD",
    "stamp_schema", "is_legacy_message",
    "normalize_authority", "has_capability", "decide_capability",
    "sanitize_speaker_display",
]
