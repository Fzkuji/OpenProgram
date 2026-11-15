"""Per-agent runtime cache.

Previously ``openprogram.interfaces.server.runtime_management`` held a single
process-global chat runtime — fine for one-agent installs, wrong for
multi-agent where each agent's provider/model/thinking effort are
independent.

Here we build and cache one runtime per agent, on the Agent's own model
or, when it has none, the last used model. Cache key is
``(agent_id, provider, model, thinking_effort)``; if the user edits a model
pick we invalidate the entry automatically so the next turn creates a
fresh runtime.

Thread-safe: the cache is guarded by a Lock. Runtime construction can
be expensive (OAuth token refresh, subprocess spawn for Claude Code,
etc.) so we never build under the cache lock — only insertion does.
"""
from __future__ import annotations

import threading
from typing import Any, Tuple

from openprogram.runtime.agent_registry.manager import AgentSpec
import logging

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_cache: dict[str, Tuple[str, str, str, Any]] = {}
_cache_lock = threading.Lock()


def get_runtime_for(agent: AgentSpec) -> Any:
    """Return the runtime bound to this agent. Builds lazily; caches
    by (agent_id, provider, model, effort). Invalidates on config change."""
    key = agent.id
    provider, model_id = _model_for(agent)
    effort = agent.thinking_effort

    with _cache_lock:
        entry = _cache.get(key)
        if entry and entry[:3] == (provider, model_id, effort):
            return entry[3]

    runtime = _build_runtime(provider, model_id)
    _apply_thinking_effort(runtime, effort)
    with _cache_lock:
        _cache[key] = (provider, model_id, effort, runtime)
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

def _model_for(agent: AgentSpec) -> Tuple[str, str]:
    """The Agent's own model, else the utility tier's (Explorer), else the
    model the user ran last."""
    provider = agent.model.provider or ""
    model_id = agent.model.id or ""
    if provider and model_id:
        return provider, model_id
    from openprogram.runtime.agent_registry.builtin_agents import utility_tier_model
    pair = utility_tier_model(agent)
    if pair:
        return pair
    from openprogram.models.providers.catalog.last_used import read_last_used_model
    return read_last_used_model() or ("", "")


def _build_runtime(provider: str, model_id: str) -> Any:
    """Build a fresh runtime for (provider, model_id).

    There is no default model to fall back on: with neither an Agent
    model nor a last used model, the caller tells the user to pick one.
    """
    if provider and model_id:
        return _build_configured(provider, model_id)
    raise RuntimeError(
        "No model has been used yet. Pick one with /model <provider/id>."
    )


def _build_configured(provider: str, model_id: str) -> Any:
    from openprogram.models.providers.catalog.registry import PROVIDERS
    from openprogram.runtime.agent.runtime.factory import create_runtime
    if provider in PROVIDERS:
        kwargs: dict[str, Any] = {"provider": provider}
        if provider == "openai-codex":
            kwargs["search"] = True
        if model_id:
            kwargs["model"] = model_id
        return create_runtime(**kwargs)
    # Registry-based HTTP provider (openrouter, groq, ...).
    from openprogram.runtime.agent.runtime import Runtime
    from openprogram.models.providers import get_model as _get_model
    if _get_model(provider, model_id) is None:
        # Never swap in another model: say the pick is gone.
        raise RuntimeError(
            f"Model {provider}/{model_id} is not available. "
            "Pick another one with /model."
        )
    return Runtime(model=f"{provider}:{model_id}")


def _apply_thinking_effort(runtime: Any, effort: str) -> None:
    """Apply a stored thinking-effort string to the runtime in whatever
    way its concrete class expects."""
    if not effort:
        return
    try:
        runtime.thinking_level = effort
    except Exception:
        # A runtime class without a settable effort keeps its default.
        _log.warning("thinking effort %r not applied to %s", effort, type(runtime).__name__, exc_info=True)
