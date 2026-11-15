"""The cancellation primitive: the error a stop raises and the checkpoint that raises it.

Cancellation reaches below the runtime: a command the host collects polls the
same checkpoint an Agent method does. So the primitive lives here, under
both. Two things can cancel the code running in this context:

* the cancel event bound to the in-flight tool or ``_execute`` call
  (``_current_cancel``; an ``asyncio.Event`` or anything with ``is_set`` /
  ``aborted``), which the Agent method wrapper sets;
* the checkpoint the driving host installs with :func:`set_cancellation_check`
  (the runtime's stop button, at ``runtime.conversation.run_control`` import).

Exactly one host owns the checkpoint at a time: installing a second one
replaces the first. Without one, nothing but the bound event cancels.
"""
from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar


class CancelledError(BaseException):
    """Raised to abort an Agent method call or a command the user stopped.

    Inherits from BaseException (not Exception) so user-written except clauses
    inside Agent method bodies don't accidentally swallow cancellation.
    """


def _no_cancellation() -> None:
    """Default cancel checkpoint: nothing can cancel an embedded run."""


_cancellation_check: Callable[[], None] = _no_cancellation

# Cancel event for the in-flight tool/_execute invocation (asyncio.Event
# or any object with is_set/aborted). Nested exec() reads this via
# check_cancelled().
_current_cancel: ContextVar = ContextVar("_current_cancel", default=None)


def set_cancellation_check(hook: Callable[[], None] | None) -> None:
    """Install the checkpoint the exec loop calls before each LLM attempt.

    The hook raises (typically CancelledError) to abort. Passing None restores
    the never-cancelled default.
    """
    global _cancellation_check
    _cancellation_check = hook or _no_cancellation


def run_cancellation_check() -> None:
    """Run only the installed checkpoint, not the bound cancel event."""
    _cancellation_check()


def check_cancelled() -> None:
    """Raise CancelledError if the bound event is set or the checkpoint says stop."""
    ev = _current_cancel.get()
    if ev is not None:
        if getattr(ev, "is_set", lambda: False)():
            raise CancelledError("Cancelled by user")
        if getattr(ev, "aborted", False):
            raise CancelledError("Cancelled by user")
    _cancellation_check()


__all__ = [
    "CancelledError",
    "check_cancelled",
    "run_cancellation_check",
    "set_cancellation_check",
]
