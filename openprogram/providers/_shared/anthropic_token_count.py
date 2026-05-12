"""Wrapper around Anthropic's POST /v1/messages/count_tokens.

Used as a fallback when an upstream proxy (claude-max-api-proxy)
doesn't forward usage chunks to the OpenAI-compat stream we consume.
The count is authoritative — same tokenizer Anthropic uses for billing —
so it's safe to write to the messages.input_tokens column under the
``anthropic_count_api`` source label.

The endpoint is FREE per Anthropic docs and does not consume model
budget. Network failures here must never crash a turn — callers wrap
in try/except.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

try:
    import httpx  # type: ignore
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore


_API_URL = "https://api.anthropic.com/v1/messages/count_tokens"

# Real Anthropic API ids the count_tokens endpoint accepts. The proxy
# uses short aliases (claude-opus-4 / claude-sonnet-4 / claude-haiku-4)
# which Anthropic itself doesn't recognise — map them to the latest
# concrete release id so the call is accepted.
_ALIAS_TO_API_ID = {
    "claude-opus-4": "claude-opus-4-20250514",
    "claude-sonnet-4": "claude-sonnet-4-20250514",
    "claude-haiku-4": "claude-haiku-4-5-20251001",
}


def _resolve_api_id(model: str) -> str:
    # Strip 1M-context marker; the underlying model id is the same.
    base = model.replace("[1m]", "").strip()
    return _ALIAS_TO_API_ID.get(base, base)


def _flatten_content_to_text(content: Any) -> str:
    """Reduce structured content (lists of dicts, ToolCall blocks, etc.)
    to a single text string. count_tokens accepts plain ``"content": str``
    per message which is the simplest envelope to construct from
    heterogeneous history rows."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    parts.append(b.get("text") or "")
                elif b.get("type") in ("tool_use", "toolCall"):
                    name = b.get("name") or b.get("tool") or "tool"
                    args = b.get("input") or b.get("arguments") or {}
                    parts.append(f"[tool_use {name}({json.dumps(args, default=str)[:500]})]")
                elif b.get("type") in ("tool_result", "toolResult"):
                    parts.append(f"[tool_result {str(b.get('content') or b.get('result') or '')[:1000]}]")
                else:
                    parts.append(str(b.get("text") or b.get("content") or ""))
            else:
                parts.append(str(b))
        return "\n".join(p for p in parts if p)
    if content is None:
        return ""
    return str(content)


def count_tokens_via_anthropic(
    messages: list[dict],
    model: str,
    *,
    api_key: Optional[str] = None,
    timeout_s: float = 10.0,
) -> Optional[dict]:
    """Return ``{"input_tokens": N}`` for the given message list, or None.

    The API key is resolved via, in order:
      1. ``api_key`` parameter
      2. ``ANTHROPIC_API_KEY`` env var
      3. ``~/.agentic/config.json`` api_keys.ANTHROPIC_API_KEY

    Returns None on any failure — never raises. Caller treats None as
    'we don't know' and leaves the token columns NULL.
    """
    if httpx is None:
        return None

    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        try:
            from pathlib import Path
            cfg_path = Path(os.path.expanduser("~/.agentic/config.json"))
            if cfg_path.exists():
                cfg = json.loads(cfg_path.read_text())
                key = (cfg.get("api_keys") or {}).get("ANTHROPIC_API_KEY")
        except Exception:
            key = None
    if not key:
        return None

    # Convert to count_tokens wire format: list of {role, content}. Drop
    # system messages from the messages array and pass them as a top-level
    # `system` string instead (Anthropic schema requirement).
    system_parts: list[str] = []
    wire: list[dict] = []
    for m in messages:
        role = m.get("role")
        text = _flatten_content_to_text(m.get("content"))
        if not text:
            continue
        if role == "system":
            system_parts.append(text)
            continue
        if role in ("tool", "toolResult"):
            # Represent tool results as user messages with a tagged prefix —
            # count_tokens only accepts user/assistant roles.
            wire.append({"role": "user", "content": text})
            continue
        if role in ("assistant", "model"):
            wire.append({"role": "assistant", "content": text})
            continue
        wire.append({"role": "user", "content": text})

    if not wire:
        return None

    body: dict[str, Any] = {
        "model": _resolve_api_id(model),
        "messages": wire,
    }
    if system_parts:
        body["system"] = "\n\n".join(system_parts)

    try:
        with httpx.Client(timeout=timeout_s) as client:
            r = client.post(
                _API_URL,
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=body,
            )
        if r.status_code != 200:
            return None
        data = r.json()
        return {"input_tokens": int(data.get("input_tokens") or 0)}
    except Exception:
        return None
