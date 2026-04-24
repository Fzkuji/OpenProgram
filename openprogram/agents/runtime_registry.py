"""Per-agent runtime cache.

Previously ``openprogram.webui._runtime_management`` held a single
process-global chat runtime — fine for one-agent installs, wrong for
multi-agent where each agent's provider/model/thinking effort are
independent.

Here we build and cache one runtime per agent. Cache key is
``(agent_id, provider, model)``; if the user edits an agent's model
pick we invalidate the entry automatically so the next turn creates a
fresh runtime.

Thread-safe: the cache is guarded by a Lock. Runtime construction can
be expensive (OAuth token refresh, subprocess spawn for Claude Code,
etc.) so we never build under the cache lock — only insertion does.
"""
from __future__ import annotations

import threading
from typing import Any, Optional, Tuple

from openprogram.agents.manager import AgentSpec


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_cache: dict[str, Tuple[str, str, Any]] = {}
_cache_lock = threading.Lock()


def get_runtime_for(agent: AgentSpec) -> Any:
    """Return the runtime bound to this agent. Builds lazily; caches
    by (agent_id, provider, model). Invalidates on model change."""
    key = agent.id
    provider = agent.model.provider or ""
    model_id = agent.model.id or ""

    with _cache_lock:
        entry = _cache.get(key)
        if entry and entry[0] == provider and entry[1] == model_id:
            return entry[2]

    runtime = _build_runtime(provider, model_id)
    _apply_thinking_effort(runtime, agent.thinking_effort)
    with _cache_lock:
        _cache[key] = (provider, model_id, runtime)
    return runtime


def invalidate(agent_id: str) -> None:
    """Drop the cached runtime for one agent — call after delete /
    model change / auth rotation. Next ``get_runtime_for`` rebuilds.
    """
    with _cache_lock:
        _cache.pop(agent_id, None)


def invalidate_all() -> None:
    with _cache_lock:
        _cache.clear()


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

def _build_runtime(provider: str, model_id: str) -> Any:
    """Build a fresh runtime for (provider, model_id). Falls through
    to auto-detection (claude-code, openai-codex, etc. in the order
    used by the Web UI) when the agent hasn't picked a provider yet.

    Raises RuntimeError if we can't even construct a fallback — the
    caller shows a "configure a provider" error to the user.
    """
    if provider and model_id:
        return _build_configured(provider, model_id)
    return _build_autodetect()


def _build_configured(provider: str, model_id: str) -> Any:
    from openprogram.legacy_providers import PROVIDERS, create_runtime
    if provider in PROVIDERS:
        kwargs: dict[str, Any] = {"provider": provider}
        if provider == "openai-codex":
            kwargs["search"] = True
        if model_id:
            kwargs["model"] = model_id
        return create_runtime(**kwargs)
    # Registry-based HTTP provider (openrouter, groq, ...).
    from openprogram.agentic_programming.runtime import Runtime
    from openprogram.providers import get_model as _get_model
    if _get_model(provider, model_id) is None:
        # Fall back to auto-detect rather than failing hard — user
        # may have lost a model from their registry but still wants
        # a working chat.
        return _build_autodetect()
    return Runtime(model=f"{provider}:{model_id}")


def _build_autodetect() -> Any:
    """Same pass the Web UI uses on fresh installs: try Claude Code /
    Codex / Gemini CLI / Anthropic / OpenAI / Google in order. Raises
    if none are configured.
    """
    from openprogram.legacy_providers import create_runtime
    for p in ("claude-code", "openai-codex", "gemini-cli",
              "anthropic", "gemini", "openai"):
        try:
            rt = create_runtime(provider=p)
            if rt is not None:
                return rt
        except Exception:
            continue
    raise RuntimeError(
        "No provider configured. Run `openprogram providers setup`."
    )


def _apply_thinking_effort(runtime: Any, effort: str) -> None:
    """Apply a stored thinking-effort string to the runtime in whatever
    way its concrete class expects."""
    if not effort:
        return
    try:
        runtime.thinking_level = effort
    except Exception:
        pass
