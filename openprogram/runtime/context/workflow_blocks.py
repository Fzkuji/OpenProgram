"""Context providers that describe where a workflow role call sits.

Both are opt-in: add them to a Context as providers.

    ctx = Context(providers={"call path": call_path, "program": workflow_source})
    ctx = Context(providers={"call path": call_path(docs=True)})
"""
from __future__ import annotations

import inspect
from typing import Any


class _CallPath:
    """List the workflow functions that led to the role call."""

    def __init__(self, docs: bool = False):
        self.docs = docs

    def __call__(self, context: Any = None, *, docs: bool | None = None):
        from openprogram.runtime.context.model import Context
        if not isinstance(context, Context):
            return _CallPath(docs=self.docs if docs is None else bool(docs))
        from openprogram.runtime.context.scope import current_site
        site = current_site()
        if site is None:
            return ""
        frames = site.path()
        if not self.docs:
            return " → ".join(frame.name for frame in frames)
        lines = []
        for index, frame in enumerate(frames):
            prefix = "→ " if index else ""
            lines.append(f"{prefix}{frame.name}: {frame.doc}" if frame.doc else f"{prefix}{frame.name}")
        return "\n".join(lines)


call_path = _CallPath()


def workflow_source(context: Any) -> str:
    """The source of the running workflow entry function."""
    from openprogram.runtime.context.scope import current_site
    site = current_site()
    if site is None:
        return ""
    frames = site.path()
    function = frames[0].function if frames else None
    if function is None:
        return ""
    try:
        return inspect.getsource(inspect.unwrap(function))
    except (OSError, TypeError):
        return ""


__all__ = ["call_path", "workflow_source"]
