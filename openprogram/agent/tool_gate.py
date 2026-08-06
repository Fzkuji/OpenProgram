"""Compatibility alias — the event layer lives in ``openprogram.events``.

Same module object as ``openprogram.events.tool_gate`` via ``sys.modules``
aliasing, so imports and monkeypatch targets keep working.
"""
import sys

from openprogram.events import tool_gate as _tool_gate

sys.modules[__name__] = _tool_gate
