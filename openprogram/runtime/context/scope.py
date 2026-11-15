"""Task-local scope that context construction reads.

The call core (``openprogram.runtime.agent``) sets these values while a
call runs; ``Context`` and the renderers read them. They live here, below
the call core, so ``context`` never imports ``agent``: ``agent.call_state``
and ``agent.workflow_site`` import the same objects from this module, and
there is exactly one copy of each ContextVar.
"""
from __future__ import annotations

import inspect
import os
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Optional

# DAG call_id of the Agent method currently being executed in this
# task. The decorator sets it at entry; Python's ContextVar set/reset
# token gives us scope-bound semantics for free, so nested invocations
# automatically restore the outer caller's id on exit. Downstream code
# (``Runtime.exec``, ``ask_user``) reads this to stamp the
# ``caller`` field on whatever DAG node it appends.
_call_id: ContextVar[Optional[str]] = ContextVar(
    '_call_id', default=None,
)

# Explicit conversation-predecessor for a top-level run (empty _call_id).
# Normally a top-level manual call chains off the session's current head.
# A RETRY needs to fork off a SPECIFIC node (the original run's
# predecessor), not the head — the forced-tool path sets this so the code
# node lands as a sibling of the original rather than the newest tip.
# Unset → fall back to the head.
_forced_predecessor: ContextVar[Optional[str]] = ContextVar(
    '_forced_predecessor', default=None,
)

_VALID_EXPOSE = ("io", "llm", "full", "hidden")


def default_expose() -> str:
    """What ``expose`` means when a decorator doesn't say.

    ``OPENPROGRAM_EXPOSE_DEFAULT`` (io|llm|full|hidden) moves it, for
    ablating how much of a function's internals the caller's context
    should see. An unrecognised value falls back to ``"io"`` rather than
    raising — a bad env var must not break every import in the process.
    """
    val = (os.environ.get("OPENPROGRAM_EXPOSE_DEFAULT") or "").strip().lower()
    return val if val in _VALID_EXPOSE else "io"


@dataclass(frozen=True)
class SiteFrame:
    """One workflow function on the stack."""

    name: str
    doc: str
    module: str
    function: Any = None


@dataclass(frozen=True)
class WorkflowEntry:
    """The workflow entry currently running in this task."""

    function: Any
    writer: Any = None
    call_id: str = ""


@dataclass(frozen=True)
class CallSite:
    """The workflow frames of one role call, outermost first."""

    frames: tuple[SiteFrame, ...] = ()
    entry: WorkflowEntry | None = None
    frame_call_id: str | None = field(default=None, compare=False)

    @property
    def doc(self) -> str:
        """The docstring of the function that made the call."""
        return self.frames[-1].doc if self.frames else ""

    def path(self) -> list[SiteFrame]:
        """Frames from the entry to the caller, adding the entry when the stack broke."""
        frames = list(self.frames)
        entry = self.entry.function if self.entry is not None else None
        if entry is not None:
            entry_code = getattr(inspect.unwrap(entry), "__code__", None)
            if not frames or getattr(frames[0].function, "__code__", None) is not entry_code:
                source = inspect.unwrap(entry)
                frames.insert(0, SiteFrame(
                    name=getattr(source, "__qualname__", getattr(source, "__name__", "")),
                    doc=inspect.getdoc(source) or "",
                    module=getattr(source, "__module__", ""),
                    function=source,
                ))
        return frames


_current_site: ContextVar[CallSite | None] = ContextVar("workflow_call_site", default=None)
_current_entry: ContextVar[WorkflowEntry | None] = ContextVar("workflow_entry", default=None)


def current_site() -> CallSite | None:
    """The workflow call site bound for the running role call, if any."""
    return _current_site.get()


def current_entry() -> WorkflowEntry | None:
    """The workflow entry running in this task, if any."""
    return _current_entry.get()


__all__ = [
    "CallSite",
    "SiteFrame",
    "WorkflowEntry",
    "current_entry",
    "current_site",
    "default_expose",
]
