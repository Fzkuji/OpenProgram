"""Compatibility alias — the event layer lives in ``openprogram.events``.

``sys.modules`` aliasing makes this THE SAME module object as
``openprogram.events.bus``, so existing imports AND monkeypatch targets
(``openprogram.agent.event_bus._event_bus``, ``...emit_ws_frame``) keep
working against the real state.
"""
import sys

from openprogram.events import bus as _bus

sys.modules[__name__] = _bus
