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

def _create_runtime_for_visualizer(provider: str):
    """Create a runtime appropriate for the web UI.

    Strategy per provider:
      - Codex CLI:       session_id="auto" (default) + search=True → capture
                         thread id on first call, resume on every subsequent
                         call so one research run shares one codex thread
      - Claude Code CLI: default (persistent process), has_session=True → process
                         manages its own context, summarize() skipped
      - Gemini CLI:      default → session auto-managed by CLI
      - API providers:   default → stateless, Context tree injects history
    """
    from openprogram.legacy_providers import create_runtime
    if provider == "openai-codex":
        return create_runtime(provider=provider, search=True)
    return create_runtime(provider=provider)


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
