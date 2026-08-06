"""Compatibility alias — the event layer lives in ``openprogram.events``.

Same module object as ``openprogram.events.bridges`` via ``sys.modules``
aliasing, so imports, the ``_installed`` idempotency flag, and monkeypatch
targets keep working.
"""
import sys

from openprogram.events import bridges as _bridges

sys.modules[__name__] = _bridges
