"""Plugin hooks — direct subscribers on the event bus.

A plugin's ``hooks`` entrypoint resolves to a dict mapping **bus event
names** (``tool.before``, ``tool.after``, ``session.start``,
``chat.before_send``, ``plugin.enable``, ``plugin.disable``, ...) to
callables. :func:`register_plugin_hooks` subscribes each handler on the
process-wide bus; :func:`unregister_plugin_hooks` disposes the
subscriptions. Handlers receive the :class:`openprogram.events.Event`.

Dispatch kind follows the registry (``openprogram/events/registry.py``):

* **notify** events — ``bus.subscribe``; the handler observes, its return
  value is ignored, and any exception is logged as a warning (a bad plugin
  never breaks the emitting code path).
* **gate** events (``tool.before`` and ``turn.stop``) —
  ``bus.subscribe_gate``; the handler participates in the veto: return
  ``None``/falsy to allow, a reason
  string to deny; raising :class:`ToolGateDenied` denies with its message;
  any other exception is logged as a warning and allows (fail-open).

The plugin sandbox / trust gate (``sandbox.py``) decides whether a
plugin's handlers get registered at all; once registered, dispatch
trusts them.
"""
from __future__ import annotations

import logging
from threading import RLock
from typing import Any, Callable

from openprogram.events import Event, ToolGateDenied, get_event_bus
from openprogram.events.registry import EVENTS

_log = logging.getLogger(__name__)

_lock = RLock()
# {plugin_name: [unsubscribe, ...]}
_subscriptions: dict[str, list[Callable[[], None]]] = {}


def _wrap_notify(plugin_name: str, event_name: str,
                 handler: Callable[..., Any]) -> Callable[[Event], None]:
    def _notify(event: Event) -> None:
        try:
            handler(event)
        except Exception as exc:  # noqa: BLE001
            _log.warning("plugin hook %s@%s raised %s: %s",
                         plugin_name, event_name, type(exc).__name__, exc)
    return _notify


def _wrap_gate(plugin_name: str, event_name: str,
               handler: Callable[..., Any]) -> Callable[[Event], "str | None"]:
    def _gate(event: Event) -> "str | None":
        try:
            verdict = handler(event)
        except ToolGateDenied as exc:
            return str(exc) or f"denied by plugin {plugin_name}"
        except Exception as exc:  # noqa: BLE001
            _log.warning("plugin gate %s@%s raised %s: %s (fail-open)",
                         plugin_name, event_name, type(exc).__name__, exc)
            return None
        return str(verdict) if verdict else None
    return _gate


def register_plugin_hooks(plugin_name: str,
                          mapping: dict[str, Callable[..., Any]]) -> None:
    """Subscribe a plugin's hook handlers on the process bus. ``mapping``
    is the dict the plugin's ``entrypoints.hooks`` resolved to (after
    manifest load). A second call for the same plugin replaces the
    previous subscriptions."""
    unregister_plugin_hooks(plugin_name)
    bus = get_event_bus()
    disposables: list[Callable[[], None]] = []
    for event_name, handler in (mapping or {}).items():
        if not isinstance(event_name, str) or not callable(handler):
            continue
        spec = EVENTS.get(event_name)
        if spec is not None and spec.kind == "gate":
            disposables.append(bus.subscribe_gate(
                event_name, _wrap_gate(plugin_name, event_name, handler)))
        else:
            disposables.append(bus.subscribe(
                _wrap_notify(plugin_name, event_name, handler),
                types={event_name}))
    with _lock:
        if disposables:
            _subscriptions[plugin_name] = disposables


def unregister_plugin_hooks(plugin_name: str) -> None:
    """Dispose every subscription the plugin holds on the bus."""
    with _lock:
        disposables = _subscriptions.pop(plugin_name, [])
    for dispose in disposables:
        dispose()
