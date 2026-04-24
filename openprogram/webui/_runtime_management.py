"""
Runtime / provider management for the web UI.

Owns the globals for chat + exec provider selection, runtime creation,
provider detection, and runtime switching. Broadcasts use a late import
to avoid a circular dep with server.py.
"""

from __future__ import annotations

import json
import threading
from typing import Optional


# ---------------------------------------------------------------------------
# Globals — live here so server.py doesn't own provider state directly.
# ---------------------------------------------------------------------------

_CLI_PROVIDERS = {"openai-codex", "claude-code", "gemini-cli"}

_runtime_lock = threading.Lock()

_chat_provider: Optional[str] = None
_chat_model: Optional[str] = None
_chat_runtime = None

_exec_provider: Optional[str] = None
_exec_model: Optional[str] = None

_default_provider: Optional[str] = None
_default_runtime = None
_providers_initialized = False

_available_providers: dict[str, dict] = {}


def _log(text: str) -> None:
    try:
        from openprogram.webui.server import _log as _srv_log
        _srv_log(text)
    except Exception:
        print(text)


def _broadcast(msg: str) -> None:
    try:
        from openprogram.webui.server import _broadcast as _srv_bc
        _srv_bc(msg)
    except Exception:
        pass


def _broadcast_chat_response(conv_id: str, msg_id: str, response: dict) -> None:
    try:
        from openprogram.webui.server import _broadcast_chat_response as _srv_bcr
        _srv_bcr(conv_id, msg_id, response)
    except Exception:
        pass


def _get_conversations():
    """Return (conversations dict, lock). Late import to avoid cycle."""
    from openprogram.webui.server import _conversations, _conversations_lock
    return _conversations, _conversations_lock


# ---------------------------------------------------------------------------
# Runtime inspection
# ---------------------------------------------------------------------------

def _prev_rt_closed(rt) -> bool:
    """Check if a Claude Code runtime's process has exited."""
    proc = getattr(rt, "_proc", None)
    return proc is None or proc.poll() is not None


# ---------------------------------------------------------------------------
# Runtime creation — provider-specific setup
# ---------------------------------------------------------------------------

def _preferred_default_model(provider: str) -> str | None:
    """Pick a non-hardcoded default model for a provider based on user config.

    Priority:
      1. Top-level ``default_model`` in ``~/.agentic/config.json``
         (only when ``default_provider`` matches or is unset).
      2. First id in ``providers.<provider>.enabled_models`` if the user
         has any models enabled for this provider.
      3. ``None`` — caller uses its hardcoded fallback.
    """
    try:
        from openprogram.webui._model_catalog import _read_providers_cfg
        from openprogram.paths import get_config_path
        import json
        try:
            with open(get_config_path(), "r", encoding="utf-8") as f:
                root_cfg = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            root_cfg = {}
        default_provider = root_cfg.get("default_provider")
        default_model = root_cfg.get("default_model")
        if default_model and (not default_provider or default_provider == provider):
            return default_model
        providers_cfg = _read_providers_cfg()
        enabled = (providers_cfg.get(provider, {}).get("enabled_models") or [])
        if enabled:
            return enabled[0]
    except Exception:
        pass
    return None


def _create_runtime_for_visualizer(provider: str, model: str | None = None):
    """Create a runtime appropriate for the web UI.

    Two shapes:
      - Provider listed in ``legacy_providers.PROVIDERS`` (CLI runtimes + the
        classic HTTP subclasses like AnthropicRuntime/OpenAIRuntime/...):
        route through ``legacy_providers.create_runtime`` so their per-provider
        conventions (Codex search=True, Claude Code has_session, ...) apply.
      - Any other provider id present in the HTTP model registry (openrouter,
        groq, cerebras, minimax, mistral, ...): build a plain ``Runtime`` with
        ``model="<provider>:<id>"``. These go through AgentSession end-to-end.
    """
    from openprogram.legacy_providers import create_runtime, PROVIDERS

    # If caller didn't pin a model, prefer user config (default_model or
    # the first entry in enabled_models) over the hardcoded PROVIDERS
    # default. Keeps fresh installs on the hardcoded fallback while
    # letting users promote e.g. Opus to default by enabling it in
    # Settings — no manual switch needed every session.
    if model is None:
        model = _preferred_default_model(provider)

    if provider in PROVIDERS:
        kwargs = {"provider": provider}
        if provider == "openai-codex":
            kwargs["search"] = True
        if model:
            kwargs["model"] = model
        return create_runtime(**kwargs)

    # Registry-based provider. Pick a default model if caller didn't specify.
    from openprogram.agentic_programming.runtime import Runtime
    from openprogram.providers import get_model as _get_model, get_models as _get_models
    if model is None:
        models = _get_models(provider)
        if not models:
            raise RuntimeError(f"Provider {provider!r} has no models registered")
        model = models[0].id
    if _get_model(provider, model) is None:
        raise RuntimeError(f"Unknown model {provider}:{model}")
    return Runtime(model=f"{provider}:{model}")


def _detect_default_provider() -> tuple:
    """Auto-detect best provider; return (name, runtime) or (None, None).

    Order prioritizes Claude Code CLI (sonnet) per project default.
    """
    for p in ("claude-code", "openai-codex", "gemini-cli", "anthropic", "gemini", "openai"):
        rt = None
        try:
            rt = _create_runtime_for_visualizer(p)
            if p in _CLI_PROVIDERS:
                import shutil
                cli_names = {"openai-codex": "codex", "claude-code": "claude", "gemini-cli": "gemini"}
                cli_bin = cli_names.get(p, p)
                if not shutil.which(cli_bin):
                    raise RuntimeError(f"{cli_bin} not installed")
            _log(f"[detect] {p} OK")
            return p, rt
        except Exception as e:
            _log(f"[detect] {p} failed: {e}")
            if rt and hasattr(rt, "close"):
                try:
                    rt.close()
                except Exception:
                    pass
            continue
    _log("[detect] No provider available — server will start without LLM support")
    return None, None


def _init_providers():
    """Initialize chat and exec provider defaults + probe available providers."""
    global _chat_provider, _chat_model, _chat_runtime
    global _exec_provider, _exec_model
    global _default_provider, _default_runtime
    global _providers_initialized

    with _runtime_lock:
        if _providers_initialized:
            return
        _providers_initialized = True

        provider_name, rt = _detect_default_provider()

        _chat_provider = provider_name
        _chat_model = rt.model if rt else None
        _chat_runtime = rt

        _exec_provider = provider_name
        _exec_model = rt.model if rt else None

        _default_provider = provider_name
        _default_runtime = rt

        import shutil as _shutil
        _cli_bins = {"openai-codex": "codex", "claude-code": "claude", "gemini-cli": "gemini"}
        for p_name in ("claude-code", "openai-codex", "gemini-cli", "anthropic", "gemini", "openai"):
            try:
                if p_name in _CLI_PROVIDERS:
                    if not _shutil.which(_cli_bins.get(p_name, p_name)):
                        raise RuntimeError(f"{p_name} not installed")
                probe_rt = _create_runtime_for_visualizer(p_name)
                models = probe_rt.list_models() if hasattr(probe_rt, "list_models") else []
                if probe_rt.model and probe_rt.model not in models:
                    models = [probe_rt.model] + models
                _available_providers[p_name] = {"models": models, "default_model": probe_rt.model}
                if hasattr(probe_rt, "close"):
                    probe_rt.close()
            except Exception as e:
                _log(f"[probe] {p_name} unavailable: {e}")
                continue


def _get_conv_runtime(conv_id: str, msg_id: str = None):
    """Get chat runtime for a conversation, creating if needed."""
    _init_providers()

    _conversations, _ = _get_conversations()
    conv = _conversations.get(conv_id)
    if conv and conv.get("runtime"):
        return conv["runtime"]

    if not _chat_provider:
        raise RuntimeError(
            "No provider available. Install a CLI (codex/claude/gemini) or set an API key."
        )

    rt = _create_runtime_for_visualizer(_chat_provider)
    if _chat_model:
        rt.model = _chat_model
    if conv:
        conv["runtime"] = rt
        conv["provider_name"] = _chat_provider
    return rt


def _get_exec_runtime(no_tools: bool = False):
    """Create a fresh runtime for function execution."""
    _init_providers()
    if not _exec_provider:
        raise RuntimeError(
            "No provider available. Install a CLI (codex/claude/gemini) or set an API key."
        )
    if no_tools and _exec_provider == "openai-codex":
        from openprogram.legacy_providers import create_runtime
        rt = create_runtime(
            provider="openai-codex", session_id=None, search=False,
            full_auto=False, sandbox="read-only",
        )
    elif no_tools and _exec_provider == "claude-code":
        from openprogram.legacy_providers import create_runtime
        rt = create_runtime(provider="claude-code", tools="")
    else:
        rt = _create_runtime_for_visualizer(_exec_provider)
    if _exec_model:
        rt.model = _exec_model
    return rt


def _switch_runtime(provider: str, conv_id: str = None, msg_id: str = None):
    """Switch provider. Updates current conversation + global default."""
    global _default_provider, _default_runtime

    with _runtime_lock:
        if conv_id and msg_id:
            _broadcast_chat_response(conv_id, msg_id, {
                "type": "status",
                "content": f"Switching to {provider}...",
            })

        try:
            if provider == "auto":
                name, rt = _detect_default_provider()
                if name is None:
                    raise RuntimeError("No provider available")
            else:
                name, rt = provider, _create_runtime_for_visualizer(provider)
        except Exception as e:
            if conv_id and msg_id:
                _broadcast_chat_response(conv_id, msg_id, {
                    "type": "error",
                    "content": f"Failed to set up {provider}: {e}",
                })
            raise

        _default_provider = name
        _default_runtime = rt

        if conv_id:
            _conversations, _conversations_lock = _get_conversations()
            with _conversations_lock:
                conv = _conversations.get(conv_id)
            if conv:
                conv["runtime"] = _create_runtime_for_visualizer(name)
                conv["provider_name"] = name

        if conv_id and msg_id:
            _broadcast_chat_response(conv_id, msg_id, {
                "type": "status",
                "content": f"Using {name} ({rt.model})",
            })

        _broadcast(json.dumps({
            "type": "provider_changed",
            "data": _get_provider_info(conv_id),
        }))

        return rt


def _get_provider_info(conv_id: str = None) -> dict:
    """Get provider info. If conv_id given, return that conversation's provider."""
    provider_name = _default_provider
    runtime = _default_runtime

    if conv_id:
        _conversations, _conversations_lock = _get_conversations()
        with _conversations_lock:
            conv = _conversations.get(conv_id)
        if conv and conv.get("runtime"):
            runtime = conv["runtime"]
            provider_name = conv.get("provider_name", _default_provider)

    if runtime is None:
        return {"provider": None, "type": None, "model": None,
                "runtime": None, "session_id": None}

    provider_type = "CLI" if provider_name in _CLI_PROVIDERS else "API"
    session_id = getattr(runtime, "_session_id", None)
    return {
        "provider": provider_name,
        "type": provider_type,
        "model": runtime.model,
        "runtime": type(runtime).__name__,
        "session_id": session_id,
    }
