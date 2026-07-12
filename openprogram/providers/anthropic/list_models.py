"""Anthropic model list — ``/v1/models`` (``x-api-key`` header).

Convention module (see ``openai_codex/list_models.py`` for the pattern): the
dispatcher loads ``fetch(provider_id, timeout)`` by directory name.

Differs from the generic OpenAI-compatible fetcher in two ways: auth header
(``x-api-key``, not ``Authorization: Bearer``) and the mandatory
``anthropic-version`` header. Works for any provider that speaks the Anthropic
Messages wire format, not just native Anthropic: native uses
``api.anthropic.com``, everyone else (minimax, minimax-cn, …) hits
``<their base_url>/v1/models``. The dispatcher routes anthropic-wire providers
here even without an explicit entry, via the ``anthropic-messages`` api route.

Contract: success → ``list[dict]``, failure → ``{"error": ...}``.
"""
from __future__ import annotations

from typing import Any


def fetch(provider_id: str, timeout: float) -> Any:
    import httpx

    from openprogram.webui._model_listing.storage import (
        _resolve_api_key,
        _resolve_base_url,
    )

    # claude-code runs on a Claude SUBSCRIPTION (OAuth, no api-key). Its
    # credentials live in the `anthropic` pool and the token is an
    # ``sk-ant-oat`` OAuth bearer — resolve it the same way the runtime does.
    if provider_id == "claude-code":
        from openprogram.auth.resolver import resolve_api_key_sync
        api_key = resolve_api_key_sync("anthropic")
        url = "https://api.anthropic.com/v1/models"
    else:
        api_key = _resolve_api_key(provider_id)
        # Native Anthropic has a fixed host; third-party Anthropic-wire
        # providers carry their own base_url (…/anthropic).
        if provider_id == "anthropic":
            url = "https://api.anthropic.com/v1/models"
        else:
            base = _resolve_base_url(provider_id)
            if not base:
                return {"error": f"No base URL resolvable for {provider_id}."}
            url = base.rstrip("/") + "/v1/models"
    if not api_key:
        from openprogram.webui._model_listing.providers import _env_var_for
        env = _env_var_for(provider_id)
        return {"error": f"No API key set ({env})." if env else "No credential — sign in first."}
    # An sk-ant-oat OAuth token authenticates as Bearer + Claude Code identity
    # headers (x-api-key 401s for OAuth). A plain api-key uses x-api-key.
    if "sk-ant-oat" in api_key:
        headers = {
            "authorization": f"Bearer {api_key}",
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "claude-code-20250219,oauth-2025-04-20",
            "user-agent": "claude-cli/2.1.62",
            "x-app": "cli",
        }
    else:
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    try:
        r = httpx.get(url, headers=headers, timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    out = []
    for it in (data.get("data") or []):
        mid = it.get("id")
        if not mid:
            continue
        entry = {
            "id": mid,
            "name": it.get("display_name") or mid,
        }
        # The per-model detail endpoint (/v1/models/<id>) returns the
        # AUTHORITATIVE context window (max_input_tokens) and output cap
        # (max_tokens) — a plain GET, no inference, no billing. This is how
        # we know 1M models (4.6/4.7/4.8, sonnet-4.5/4.6, fable = 1,000,000)
        # apart from 200K ones (opus-4.5/4.1, haiku) without probing.
        # Only native Anthropic / claude-code expose this detail shape;
        # third-party anthropic-wire hosts (minimax, …) don't, so skip them.
        # Use the SAME base as the list call (``url`` ends in /v1/models).
        if provider_id in ("anthropic", "claude-code"):
            try:
                det = httpx.get(
                    url.rstrip("/") + "/" + mid,
                    headers=headers, timeout=timeout,
                )
                if det.status_code == 200:
                    dj = det.json()
                    if dj.get("max_input_tokens"):
                        entry["context_window"] = int(dj["max_input_tokens"])
                    if dj.get("max_tokens"):
                        entry["max_tokens"] = int(dj["max_tokens"])
                    # Extract thinking/effort capabilities from the API
                    caps = dj.get("capabilities") or {}
                    _extract_thinking_caps(entry, caps)
            except Exception:
                pass
        out.append(entry)
    return out


def _extract_thinking_caps(entry: dict, caps: dict) -> None:
    """Extract thinking/effort capabilities from Anthropic /v1/models response.

    Sets ``reasoning``, ``thinking_levels``, and ``default_thinking_level``
    on ``entry`` based on the API's authoritative capability data.
    """
    # Effort capabilities
    effort = caps.get("effort") or {}
    # effort can be a dict or an object with attributes — normalize
    if hasattr(effort, "supported"):
        effort_supported = getattr(effort, "supported", False)
    elif isinstance(effort, dict):
        effort_supported = effort.get("supported", False)
    else:
        effort_supported = False

    if effort_supported:
        levels = []
        for lvl in ("minimal", "low", "medium", "high", "xhigh", "max"):
            val = getattr(effort, lvl, None) if hasattr(effort, lvl) else (
                effort.get(lvl) if isinstance(effort, dict) else None
            )
            if val is None:
                continue
            sup = getattr(val, "supported", None) if hasattr(val, "supported") else (
                val.get("supported") if isinstance(val, dict) else False
            )
            if sup:
                levels.append(lvl)
        if levels:
            entry["reasoning"] = True
            entry["thinking_levels"] = levels
            # Default: xhigh if available, else medium, else first
            if "xhigh" in levels:
                entry["default_thinking_level"] = "xhigh"
            elif "medium" in levels:
                entry["default_thinking_level"] = "medium"
            else:
                entry["default_thinking_level"] = levels[0]

    # Thinking type (adaptive vs budget)
    thinking = caps.get("thinking") or {}
    if hasattr(thinking, "types"):
        types = thinking.types
        adaptive = getattr(types, "adaptive", None)
        if adaptive and getattr(adaptive, "supported", False):
            entry["supports_adaptive"] = True
    elif isinstance(thinking, dict):
        types = thinking.get("types") or {}
        adaptive = types.get("adaptive") or {}
        if isinstance(adaptive, dict) and adaptive.get("supported"):
            entry["supports_adaptive"] = True
