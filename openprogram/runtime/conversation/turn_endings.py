"""Whether a turn's result ends its run.

A turn can stop at a point its run goes on from: a safe point it handed on
(``_execution_safe_point_handoff``, set where the turn runtime saves it) or
a turn held for a version switch (``maintenance.HELD_REASON``), which runs
after the switch. Only a terminal execution is an ending.
"""
from __future__ import annotations

from typing import Any

from openprogram.runtime.execution.model import TERMINAL_EXECUTION_STATUSES


def held_for_switch(result: Any) -> bool:
    """A turn saved during a version switch: the run happens after the switch."""
    from openprogram.runtime.maintenance import HELD_REASON

    return getattr(result, "error_reason", None) == HELD_REASON


def has_ended(result: Any, execution: Any) -> bool:
    """Whether the run is over, rather than stopped at a point it goes on from."""
    if getattr(result, "_execution_safe_point_handoff", False) or held_for_switch(result):
        return False
    return execution is None or execution.status in TERMINAL_EXECUTION_STATUSES


__all__ = ["has_ended", "held_for_switch"]
