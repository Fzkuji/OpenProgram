"""Removing secrets for good from text and structured values.

Two families of shapes, each used where it fits:

* :func:`remove_secret_values` walks dicts and lists: a key named like a
  secret (:data:`SECRET_FIELD_NAMES`, or ending in ``token``/``key``/
  ``secret``/``password``) loses its whole value, and strings lose bearer
  tokens, ``sk-`` keys and credentials carried in URLs. Provider
  recordings, exported transcripts and diagnostics bundles go through it.
* :func:`remove_secrets` replaces the high-precision shapes of
  :data:`SECRET_PATTERNS` and :data:`SECRET_ASSIGNMENT_PATTERNS` in free
  text: the same shapes request redaction swaps for placeholders
  (``models.providers.utils.redaction``), without keeping the value, for
  copies that must never hold it, such as an imported transcript.

Both live in the state layer because the store's import and export paths
need them as much as the provider layer above does.
"""
from __future__ import annotations

import re
from typing import Any

PLACEHOLDER = "[secret removed]"

# Field names whose value is a secret wherever it appears — request options,
# model headers, nested provider-specific dicts.
SECRET_FIELD_NAMES = frozenset({
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "id_token",
    "token",
    "cookie",
    "set-cookie",
    "authorization",
    "proxy-authorization",
    "x-api-key",
    "x-goog-api-key",
    "api-key",
    "secret",
    "client_secret",
    "password",
    "session_key",
})
_SECRET_FIELD_SUFFIX = re.compile(r"(?:^|[-_])(?:token|key|secret|password)$", re.IGNORECASE)

# Secret-looking values that survive field-name matching, e.g. a bearer token
# pasted into a free-form string field or a URL query parameter.
_SECRET_VALUE_PATTERNS = (
    re.compile(r"(?i)\b(?:bearer|basic|bot|token|key)\s+[\w.:\-~+/]+=*"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)([?&](?:api[_-]?key|access[_-]?token|token)=)[^&\s]+"),
    re.compile(r"(?i)(https?://)[^/@\s:]*:[^/@\s]+@"),
)


def remove_secret_values(value: Any) -> Any:
    """Return ``value`` with every secret field and secret-looking string replaced.

    Recurses through dicts and lists. Dict keys are matched case-insensitively
    against :data:`SECRET_FIELD_NAMES`; matching keys lose their whole value.
    Remaining strings are scanned for bearer tokens, ``sk-`` keys and secrets
    carried in URL query parameters.
    """
    if isinstance(value, dict):
        return {
            key: PLACEHOLDER if is_secret_field_name(key)
            else remove_secret_values(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [remove_secret_values(item) for item in value]
    if isinstance(value, str):
        cleaned = value
        for pattern in _SECRET_VALUE_PATTERNS:
            cleaned = pattern.sub(
                lambda match: (
                    match.group(1) + PLACEHOLDER + "@"
                    if match.groups() and match.group(1).lower().startswith("http")
                    else match.group(1) + PLACEHOLDER
                    if match.groups()
                    else PLACEHOLDER
                ),
                cleaned,
            )
        return cleaned
    return value


def is_secret_field_name(value: Any) -> bool:
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", str(value)).lower()
    return name in SECRET_FIELD_NAMES or _SECRET_FIELD_SUFFIX.search(name) is not None


# High-precision shapes only: a false positive breaks the request it hides
# (a placeholder where a hash or id was needed), so generic long strings are
# not treated as secrets.
SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{35}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
)
# ``Bearer <token>`` and ``password=…`` style assignments: only the value
# (group 1) is a secret.
SECRET_ASSIGNMENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\bbearer\s+([A-Za-z0-9._~+/\-]{20,}=*)"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|secret(?:[_-]?key)?|password|passwd|pwd|access[_-]?token"
        r"|auth[_-]?token|client[_-]?secret)\b[\"']?\s*[:=]\s*[\"']?([^\s\"',;]{8,})"
    ),
)


def remove_secrets(text: str, replacement: str = "[secret removed]") -> str:
    """``text`` with every secret shape above replaced for good.

    The same high-precision shapes as a request, without a vault: for
    copies that must never hold the value (an imported transcript), where
    nothing will ask for it back. Emails are not secrets and stay."""
    if not text:
        return text
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    for pattern in SECRET_ASSIGNMENT_PATTERNS:
        text = pattern.sub(lambda m: _replace_group(m, replacement), text)
    return text


def _replace_group(match: re.Match[str], replacement: str) -> str:
    """The match with only its value (group 1) replaced, by position."""
    start, end = match.span(1)
    base = match.start(0)
    whole = match.group(0)
    return whole[:start - base] + replacement + whole[end - base:]


__all__ = [
    "PLACEHOLDER",
    "SECRET_ASSIGNMENT_PATTERNS",
    "SECRET_FIELD_NAMES",
    "SECRET_PATTERNS",
    "is_secret_field_name",
    "remove_secret_values",
    "remove_secrets",
]
