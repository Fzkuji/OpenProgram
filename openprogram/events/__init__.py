"""The event layer, in one package.

Everything event-related lives here:

* ``bus.py``         — Event / make_event / emit_safe / emit_ws_frame,
                       EventBus (notify emit + gate emit_gate + legacy
                       channels), GateOutcome, the process singleton.
* ``registry.py``    — EVENTS: the admission boundary (EventSpec per type).
* ``tool_gate.py``   — the tool.before gate surface (register_tool_gate /
                       decide_tool_gate / ToolGateDenied).
* ``shell_hooks.py`` — config.json ``hooks`` shell subscribers
                       (install_config_hooks, exit-code protocol).
* ``event_log.py``   — per-session events.jsonl with 5 MB rotation.
* ``bridges.py``     — type-B subsystem bridges (auth → bus).

Design doc: docs/reference/design/proactive/event-layer.md. Dependency
rule: this package never imports webui — webui subscribes to the bus, the
bus does not know webui.
"""
from openprogram.events.bus import (
    ORIGINS,
    WS_FRAME_EVENT,
    Event,
    EventBus,
    GateOutcome,
    create_event_bus,
    emit_safe,
    emit_ws_frame,
    get_event_bus,
    make_event,
)
from openprogram.events.registry import EVENTS, EventSpec
from openprogram.events.shell_hooks import (
    DEFAULT_SHELL_TIMEOUT_S,
    install_config_hooks,
    make_shell_gate,
    make_shell_notifier,
)
from openprogram.events.tool_gate import (
    ToolGate,
    ToolGateDenied,
    decide_tool_gate,
    register_tool_gate,
)
from openprogram.events.bridges import install_event_bridges, translate_auth_event

__all__ = [
    "ORIGINS", "WS_FRAME_EVENT", "Event", "EventBus", "GateOutcome",
    "create_event_bus", "emit_safe", "emit_ws_frame", "get_event_bus",
    "make_event",
    "EVENTS", "EventSpec",
    "DEFAULT_SHELL_TIMEOUT_S", "install_config_hooks", "make_shell_gate",
    "make_shell_notifier",
    "ToolGate", "ToolGateDenied", "decide_tool_gate", "register_tool_gate",
    "install_event_bridges", "translate_auth_event",
]
