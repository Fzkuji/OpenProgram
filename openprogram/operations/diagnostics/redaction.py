"""Scrub credential-shaped values out of text and JSON-like data.

Builds on :func:`openprogram.models.providers.recording.recording.remove_secret_values`,
the recursive scrubber the provider recorder already trusts, so the support
bundle, credential-helper errors and recordings cannot drift into
disagreeing about what counts as a secret. Two gaps in it matter here and
are closed locally:

* it matches dict keys *exactly* against ``SECRET_FIELD_NAMES``, so a
  config written as ``openai_api_key`` or ``github_token`` slips
  through unless the value happens to look like a key. Config files are
  exactly where such compound names live, so this module matches keys
  by substring as well.
* its value patterns cover bearer / ``sk-`` / URL-embedded credentials,
  which is right for HTTP recordings but misses the GitHub, AWS, Google
  and JWT shapes that turn up in logs and user config.
"""
from __future__ import annotations

import re
from typing import Any

from openprogram.models.providers.recording.recording import (
    PLACEHOLDER,
    SECRET_FIELD_NAMES,
    remove_secret_values,
)

# Substring-matched against a lowercased mapping key, to catch the
# compound names (``openai_api_key``, ``github_token``) that the
# recorder's exact-match set does not. Deliberately broad: a false
# positive costs a maintainer one question, a false negative posts a
# live credential to a bug tracker.
SENSITIVE_KEY_PARTS = tuple(SECRET_FIELD_NAMES) + (
    "credential", "passwd", "private_key", "signature", "session_id",
    "access_key", "auth",
)

# Credential shapes the recorder's patterns don't cover.
EXTRA_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"),          # GitHub
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),        # Slack
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                  # AWS access key id
    re.compile(r"\bAIza[A-Za-z0-9_-]{20,}"),              # Google API key
    re.compile(                                            # JWT
        r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}"),
)

# ``some_token: <opaque blob>`` in otherwise unstructured text, where
# there is no dict key to inspect because the line is a log message.
# The match starts at the sensitive word and its tail is bounded: an open
# run of name characters on either side makes a long token cost quadratic
# time, and whatever precedes the word is kept anyway.
_KEYED_VALUE_PATTERN = re.compile(
    r"(?i)((?:%s)[A-Za-z0-9_.-]{0,64}[\"']?\s*[:=]\s*[\"']?)"
    r"([A-Za-z0-9._~+/-]{8,}=*)"
    % "|".join(re.escape(part) for part in SENSITIVE_KEY_PARTS)
)
# Credentials in a URL of any scheme; the recorder's pattern covers http(s).
_URL_CREDENTIAL_PATTERN = re.compile(r"(?i)\b([a-z][a-z0-9+.-]{0,31}://)[^/@\s:]*:[^/@\s]+@")
# A password is secret at any length, so this one has no minimum.
_PASSWORD_PATTERN = re.compile(
    r"(?i)((?:password|passwd|passphrase|pwd)[\"']?\s*[:=]\s*)"
    r"(?:\"[^\"\n]*\"|'[^'\n]*'|[^\s\"',;&]+)"
)
# Everything after a cookie header's name is secret, including ``name=value`` pairs.
_COOKIE_PATTERN = re.compile(r"(?i)(cookie[\"']?\s*[:=]\s*)[^\r\n]+")


def is_sensitive_key(key: str) -> bool:
    lowered = str(key).lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def redact_text(text: str) -> str:
    """Scrub credential-shaped substrings out of free text."""
    text = remove_secret_values(text)
    for pattern in EXTRA_VALUE_PATTERNS:
        text = pattern.sub(PLACEHOLDER, text)
    text = _URL_CREDENTIAL_PATTERN.sub(lambda m: m.group(1) + PLACEHOLDER + "@", text)
    for pattern in (_COOKIE_PATTERN, _PASSWORD_PATTERN, _KEYED_VALUE_PATTERN):
        text = pattern.sub(lambda m: m.group(1) + PLACEHOLDER, text)
    return text


def redact_value(value: Any) -> Any:
    """Recursively redact a JSON-like structure.

    A sensitive *key* redacts its whole subtree: an ``auth`` block has
    nothing inside worth keeping, and descending would risk emitting the
    parts that match no pattern.
    """
    if isinstance(value, dict):
        return {
            k: (PLACEHOLDER if is_sensitive_key(k) else redact_value(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_value(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return remove_secret_values(value)


__all__ = ["EXTRA_VALUE_PATTERNS", "SENSITIVE_KEY_PARTS", "is_sensitive_key", "redact_text", "redact_value"]
