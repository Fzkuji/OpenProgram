# `openprogram/events/`

> The event layer, in one package.

## Overview

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

## Files in this directory

- **`bridges.py`** — B 类系统事件桥：把子系统已有的信号翻译成统一 Event 进总线。
- **`bus.py`** — Event bus: the framework-wide event layer
- **`event_log.py`** — Event log
- **`registry.py`** — Central event registry
- **`shell_hooks.py`** — Config-driven shell subscribers (Claude Code hooks exit-code protocol)
- **`tool_gate.py`** — tool.before gate

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._
