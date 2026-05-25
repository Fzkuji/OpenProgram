"""Plugin lifecycle hooks — opencode PluginV2.HookSpec equivalent.

Each plugin can expose a ``hooks`` entrypoint resolving to a dict that
maps hook event names to callables. When the host hits a known
lifecycle point it calls :func:`dispatch_hook` which iterates every
registered plugin's handler for that event.

This is intentionally a thin façade — handlers run in the host
process inline. The plugin sandbox / trust gate (see ``sandbox.py``)
decides whether a plugin's handlers get registered at all; once
registered, dispatch trusts them.
"""
from __future__ import annotations

from threading import RLock
from typing import Any, Callable


# Known event names. Plugins can also register against arbitrary
# strings — these are just the ones the host actively fires.
class HookEvent:
    PLUGIN_ENABLE = "plugin.enable"
    PLUGIN_DISABLE = "plugin.disable"
    PLUGIN_RELOAD = "plugin.reload"
    SKILL_INVOKED = "skill.invoked"
    SKILL_INSTALLED = "skill.installed"
    SESSION_START = "session.start"
    SESSION_STOP = "session.stop"
    TOOL_BEFORE_USE = "tool.before_use"
    TOOL_AFTER_USE = "tool.after_use"
    CHAT_BEFORE_SEND = "chat.before_send"
    CHAT_AFTER_RESPONSE = "chat.after_response"


_lock = RLock()
# {plugin_name: {event_name: callable}}
_handlers: dict[str, dict[str, Callable[..., Any]]] = {}


def register_plugin_hooks(plugin_name: str, mapping: dict[str, Callable[..., Any]]) -> None:
    """Register a plugin's hook handlers. ``mapping`` is the dict the
    plugin's ``entrypoints.hooks`` resolved to (after manifest load).
    A second call for the same plugin replaces the previous mapping."""
    clean: dict[str, Callable[..., Any]] = {}
    for event, handler in (mapping or {}).items():
        if not isinstance(event, str) or not callable(handler):
            continue
        clean[event] = handler
    with _lock:
        if clean:
            _handlers[plugin_name] = clean
        else:
            _handlers.pop(plugin_name, None)


def unregister_plugin_hooks(plugin_name: str) -> None:
    with _lock:
        _handlers.pop(plugin_name, None)


def list_handlers(event: str) -> list[tuple[str, Callable[..., Any]]]:
    """Return ``(plugin_name, handler)`` pairs that subscribe to ``event``."""
    with _lock:
        return [
            (pn, handlers[event])
            for pn, handlers in _handlers.items()
            if event in handlers
        ]


def dispatch_hook(event: str, payload: dict[str, Any] | None = None) -> list[Any]:
    """Call every registered handler for ``event`` with ``payload``.
    Returns the list of return values. Exceptions are caught per
    handler so one misbehaving plugin can't poison the chain."""
    payload = payload or {}
    out: list[Any] = []
    for plugin_name, handler in list_handlers(event):
        try:
            out.append(handler(**payload))
        except TypeError:
            # Allow handlers that take a single dict positional arg.
            try:
                out.append(handler(payload))
            except Exception as e:  # noqa: BLE001
                _log_handler_error(plugin_name, event, e)
        except Exception as e:  # noqa: BLE001
            _log_handler_error(plugin_name, event, e)
    return out


def _log_handler_error(plugin_name: str, event: str, exc: Exception) -> None:
    try:
        from openprogram.webui import server as _srv
        _srv._log(f"[hooks] {plugin_name}@{event} raised {type(exc).__name__}: {exc}")
    except Exception:
        pass


def clear_all() -> None:
    """Test helper — wipe every registered handler."""
    with _lock:
        _handlers.clear()
