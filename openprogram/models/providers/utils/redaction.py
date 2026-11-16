"""Request redaction — secrets and email addresses never reach the provider.

Before a request leaves, every API key, token, password assignment, private
key and email address in the system prompt, messages and tool definitions is
replaced by a placeholder such as ``[[SECRET_1]]`` or ``[[EMAIL_1]]``. The
reply streams back through :func:`redacting_stream_fn`, which puts the real
values back into text, thinking and tool-call arguments, so the transcript
and the tools the model calls see the original values.

Placeholders are numbered by first appearance in the request, so the same
history redacts to the same text on every call and the provider's prompt
cache keeps working. The vault lives for one provider call and only in
memory. Off by default; the ``privacy.redact_requests`` setting turns it on.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from pydantic import BaseModel

# High-precision shapes only: a false positive breaks the request it hides
# (a placeholder where a hash or id was needed), so generic long strings are
# not treated as secrets.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
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
_SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\bbearer\s+([A-Za-z0-9._~+/\-]{20,}=*)"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|secret(?:[_-]?key)?|password|passwd|pwd|access[_-]?token"
        r"|auth[_-]?token|client[_-]?secret)\b[\"']?\s*[:=]\s*[\"']?([^\s\"',;]{8,})"
    ),
)
_EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")
_PLACEHOLDER = re.compile(r"\[\[(?:SECRET|EMAIL)_\d+\]\]")
# The longest a placeholder can be while it is still arriving across deltas.
_HOLD = len("[[SECRET_99999]]")

# Fields that are not text the model reads or writes: binary payloads, opaque
# provider signatures and ids. Rewriting them would corrupt the request.
_SKIP_FIELDS = frozenset({
    "data", "thought_signature", "thinking_signature", "signature", "id",
    "tool_call_id", "api", "provider", "model", "mime_type", "type", "role",
    "cache_control", "redacted",
})


def remove_secrets(text: str, replacement: str = "[secret removed]") -> str:
    """``text`` with every secret shape above replaced for good.

    The same high-precision shapes as a request, without a vault: for
    copies that must never hold the value (an imported transcript), where
    nothing will ask for it back. Emails are not secrets and stay."""
    if not text:
        return text
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    for pattern in _SECRET_VALUE_PATTERNS:
        text = pattern.sub(lambda m: _replace_group(m, replacement), text)
    return text


def _replace_group(match: re.Match[str], replacement: str) -> str:
    """The match with only its value (group 1) replaced, by position."""
    start, end = match.span(1)
    base = match.start(0)
    whole = match.group(0)
    return whole[:start - base] + replacement + whole[end - base:]


class Vault:
    """Real value ↔ placeholder for one provider call."""

    def __init__(self) -> None:
        self._by_value: dict[str, str] = {}
        self._by_placeholder: dict[str, str] = {}

    def __bool__(self) -> bool:
        return bool(self._by_value)

    def _placeholder(self, value: str, kind: str) -> str:
        hit = self._by_value.get(value)
        if hit is None:
            hit = f"[[{kind}_{len(self._by_value) + 1}]]"
            self._by_value[value] = hit
            self._by_placeholder[hit] = value
        return hit

    def redact(self, text: str) -> str:
        if not text:
            return text
        for pattern in _SECRET_PATTERNS:
            text = pattern.sub(lambda m: self._placeholder(m.group(0), "SECRET"), text)
        for pattern in _SECRET_VALUE_PATTERNS:
            text = pattern.sub(
                lambda m: m.group(0).replace(m.group(1), self._placeholder(m.group(1), "SECRET")),
                text,
            )
        return _EMAIL.sub(lambda m: self._placeholder(m.group(0), "EMAIL"), text)

    def restore(self, text: str) -> str:
        if not text or not self._by_placeholder or "[[" not in text:
            return text
        return _PLACEHOLDER.sub(lambda m: self._by_placeholder.get(m.group(0), m.group(0)), text)


def map_strings(value: Any, fn: Callable[[str], str]) -> Any:
    """A copy of ``value`` with ``fn`` applied to every model-visible string
    (pydantic models, dicts and lists walked; model fields named in
    :data:`_SKIP_FIELDS` kept as they are). Unchanged parts are shared."""
    if isinstance(value, str):
        new = fn(value)
        return value if new == value else new
    if isinstance(value, BaseModel):
        update = {}
        for name in type(value).model_fields:
            if name in _SKIP_FIELDS:
                continue
            old = getattr(value, name)
            new = map_strings(old, fn)
            if new is not old:
                update[name] = new
        return value.model_copy(update=update) if update else value
    if isinstance(value, dict):
        # Dict keys are data here (tool arguments, JSON schemas), not fields.
        out = {k: map_strings(v, fn) for k, v in value.items()}
        return out if any(out[k] is not value[k] for k in value) else value
    if isinstance(value, list):
        out = [map_strings(v, fn) for v in value]
        return out if any(a is not b for a, b in zip(out, value)) else value
    return value


def _unfinished_tail(text: str) -> int:
    """Where a placeholder that is still arriving (``[[SECRET_1``, or just
    ``[``) starts at the end of ``text``; ``len(text)`` when there is none."""
    cut = text.rfind("[")
    if cut < 0 or len(text) - cut >= _HOLD or "]]" in text[cut:]:
        return len(text)
    if cut > 0 and text[cut - 1] == "[":
        cut -= 1
    return cut


class _DeltaRestorer:
    """Restores placeholders in streamed deltas, holding back a tail that may
    be the start of a placeholder until the next delta completes it."""

    def __init__(self, vault: Vault) -> None:
        self._vault = vault
        self._pending: dict[tuple[str, int], str] = {}

    def feed(self, kind: str, index: int, delta: str) -> str:
        key = (kind, index)
        text = self._pending.pop(key, "") + delta
        cut = _unfinished_tail(text)
        if cut < len(text):
            self._pending[key] = text[cut:]
            text = text[:cut]
        return self._vault.restore(text)

    def flush(self, kind: str, index: int) -> str:
        return self._vault.restore(self._pending.pop((kind, index), ""))

    def reset(self) -> None:
        """Forget every held tail: the reply they belonged to is void."""
        self._pending.clear()


def _without_unfinished_tail(message: Any, index: int) -> Any:
    """``message`` with the streaming block at ``index`` cut before a
    placeholder that is still arriving, so no half placeholder is shown."""
    content = list(getattr(message, "content", None) or [])
    if not 0 <= index < len(content):
        return message
    block = content[index]
    field = "text" if hasattr(block, "text") else "thinking" if hasattr(block, "thinking") else None
    if field is None:
        return message
    text = getattr(block, field) or ""
    cut = _unfinished_tail(text)
    if cut == len(text):
        return message
    content[index] = block.model_copy(update={field: text[:cut]})
    return message.model_copy(update={"content": content})


def redact_context(context: Any, vault: Vault) -> Any:
    return map_strings(context, vault.redact)


def redacting_stream_fn(base_stream_fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a ``stream_fn`` so each call sends a redacted context and streams
    back restored events. A request with nothing to redact passes through."""
    from .streams.event_stream import AssistantMessageEventStream, close_stream
    import asyncio

    def _fn(model: Any, context: Any, options: Any):
        vault = Vault()
        redacted = redact_context(context, vault)
        if not vault:
            return base_stream_fn(model, context, options)
        inner = base_stream_fn(model, redacted, options)
        out = AssistantMessageEventStream()
        deltas = _DeltaRestorer(vault)
        restore = vault.restore

        def restored(ev: Any) -> Any:
            etype = getattr(ev, "type", None)
            update: dict[str, Any] = {}
            for name in ("partial", "message", "error", "tool_call"):
                if hasattr(ev, name):
                    update[name] = map_strings(getattr(ev, name), restore)
            if hasattr(ev, "content") and isinstance(ev.content, str):
                update["content"] = vault.restore(ev.content)
            if etype in ("text_delta", "thinking_delta", "toolcall_delta"):
                update["delta"] = deltas.feed(etype.split("_")[0], ev.content_index, ev.delta)
                update["partial"] = _without_unfinished_tail(update["partial"], ev.content_index)
            return ev.model_copy(update=update) if isinstance(ev, BaseModel) else ev

        async def _run() -> None:
            try:
                async for ev in inner:
                    etype = getattr(ev, "type", None)
                    if etype == "retry" and getattr(ev, "restart", False):
                        # The reply starts over: a placeholder half received
                        # from the failed attempt belongs to nothing.
                        deltas.reset()
                    if etype in ("text_end", "thinking_end", "toolcall_end"):
                        tail = deltas.flush(etype.split("_")[0], ev.content_index)
                        if tail:
                            kind = etype.split("_")[0]
                            delta_cls = {
                                "text": "EventTextDelta",
                                "thinking": "EventThinkingDelta",
                                "toolcall": "EventToolCallDelta",
                            }[kind]
                            from openprogram.models.providers import types as _types

                            partial = map_strings(ev.partial, restore)
                            out.push(getattr(_types, delta_cls)(
                                content_index=ev.content_index, delta=tail, partial=partial,
                            ))
                    out.push(restored(ev))
                    if etype in ("done", "error"):
                        return
                out.fail(RuntimeError("stream ended without a final message"))
            except BaseException as exc:  # noqa: BLE001
                out.fail(exc)
                if isinstance(exc, asyncio.CancelledError):
                    # A stop: the provider request under this wrapper ends with it.
                    await close_stream(inner, timeout=0.5)
                    raise

        # Owned by the stream: a consumer that stops reading cancels this
        # task through ``close_stream``, and the task closes the provider's.
        out.attach_producer(asyncio.ensure_future(_run()))
        return out

    return _fn


def redaction_enabled() -> bool:
    try:
        from openprogram.state import config as user_config

        return bool(((user_config.read_config().get("privacy") or {}).get("redact_requests")))
    except Exception:
        return False


__all__ = [
    "Vault",
    "map_strings",
    "redact_context",
    "redacting_stream_fn",
    "redaction_enabled",
]
