"""ReferenceTracker — don't redact what the model is still citing.

When ``TurnAger`` ages a tool result older than the keep-window, the
naïve rule is "replace any large old result". Better rule, lifted from
Hermes' reference graph: if a later assistant message **quotes** the
tool result body (a file path mentioned, a specific line from a grep
output, a UUID from a JSON dump), the model is still working with that
data — don't redact it yet.

We don't need perfect citation tracking. A cheap substring scan over
"distinctive substrings" of each tool result catches 90% of real
references and costs ~O(messages * results) per session, which is fine
at our scale.

Distinctive substring extraction: pull capitalised words ≥4 chars,
hex/numeric ids ≥6 chars, paths containing ``/``, and quoted snippets
in backticks. Skip common English words.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from openprogram.context.types import ReferenceMap


# Regexes are cheap to compile once at module import.
_PATH_RE = re.compile(r"[A-Za-z0-9_./-]+/[A-Za-z0-9_./-]+")
_ID_RE = re.compile(r"\b[a-f0-9]{6,}\b|\b\d{6,}\b")
_BACKTICK_RE = re.compile(r"`([^`]{3,40})`")
_CAPS_WORD_RE = re.compile(r"\b[A-Z][A-Za-z0-9]{3,}\b")

_STOPWORDS = frozenset({
    "the", "and", "for", "with", "from", "into", "this", "that",
    "User", "Assistant", "System", "TextContent", "AssistantMessage",
    "UserMessage", "True", "False", "None", "Error", "Warning",
})


def _distinctive_strings(text: str, *, max_tokens: int = 32) -> set[str]:
    """Return a small set of substrings that uniquely identify ``text``.

    Capped at ``max_tokens`` to keep scan cost bounded for huge tool
    results (a 50K-line file dump shouldn't produce 50K reference
    tokens — we'd never use them all).
    """
    if not text:
        return set()
    out: set[str] = set()
    # Paths first — most uniquely identifying.
    for m in _PATH_RE.finditer(text):
        s = m.group(0)
        if len(s) >= 8 and s not in _STOPWORDS:
            out.add(s)
        if len(out) >= max_tokens:
            return out
    # Hex / numeric ids.
    for m in _ID_RE.finditer(text):
        out.add(m.group(0))
        if len(out) >= max_tokens:
            return out
    # Backtick-quoted snippets.
    for m in _BACKTICK_RE.finditer(text):
        s = m.group(1).strip()
        if 3 <= len(s) <= 40:
            out.add(s)
        if len(out) >= max_tokens:
            return out
    # Capitalised words (CamelCase symbols, etc).
    for m in _CAPS_WORD_RE.finditer(text):
        s = m.group(0)
        if s not in _STOPWORDS and len(s) >= 4:
            out.add(s)
        if len(out) >= max_tokens:
            return out
    return out


def _msg_text(msg: dict) -> str:
    """Concatenate all text in a message dict (content + extra blocks)."""
    parts: list[str] = []
    c = msg.get("content")
    if c:
        parts.append(str(c))
    extra_raw = msg.get("extra")
    if extra_raw:
        try:
            extra = (json.loads(extra_raw)
                     if isinstance(extra_raw, str) else extra_raw)
        except Exception:
            extra = {}
        for blk in (extra.get("blocks") or []):
            t = blk.get("content")
            if t and not blk.get("_redacted"):
                parts.append(str(t))
        for call in (extra.get("tool_calls") or []):
            if call.get("name"):
                parts.append(str(call["name"]))
            if call.get("input"):
                parts.append(json.dumps(call["input"], default=str))
    return "\n".join(parts)


class ReferenceTracker:
    """Build a ReferenceMap over a session's branch.

    Stateless per build call. Engines re-build on each ``prepare()``
    since branch contents change and re-scanning a few hundred messages
    is cheaper than maintaining a delta-aware index.
    """

    def build(self, history: list[dict]) -> ReferenceMap:
        ref = ReferenceMap(last_built_at=time.time())
        if not history:
            return ref

        # Walk forward. For each message, remember its distinctive
        # substrings. When we hit a later message whose text contains
        # any of those substrings, mark the earlier message as cited.
        per_msg: list[tuple[str, set[str]]] = []
        for m in history:
            mid = m.get("id") or ""
            text = _msg_text(m)
            per_msg.append((mid, _distinctive_strings(text)))

        for i, (mid_i, _) in enumerate(per_msg):
            if i == 0:
                continue
            later_text = _msg_text(history[i])
            if not later_text:
                continue
            # Check each EARLIER message's distinctive strings against
            # this one's text.
            for j in range(i):
                earlier_id, earlier_strings = per_msg[j]
                if not earlier_strings:
                    continue
                # Already marked? Skip the substring scan.
                if earlier_id in ref.cited_tool_use_ids:
                    continue
                for s in earlier_strings:
                    if s and s in later_text:
                        ref.cited_tool_use_ids.add(earlier_id)
                        ref.quoted_snippets_by_msg.setdefault(
                            earlier_id, set()
                        ).add(s)
                        break  # one hit is enough to protect this msg

        return ref

    @staticmethod
    def is_referenced(ref: ReferenceMap, msg_id: str | None) -> bool:
        if not msg_id:
            return False
        return msg_id in ref.cited_tool_use_ids


default_tracker = ReferenceTracker()
