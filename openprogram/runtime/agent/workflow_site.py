"""Which workflow function made a role call.

A role call (``role(prompt)``, ``role.choose(...)``) reads the Python call
stack once, before it runs. The frames whose code lives under a workflow
root form the call site: the nearest one supplies the docstring for that
single request, and the whole list backs the ``call_path`` provider.
Nothing is recorded; plain functions stay plain Python.

The stack breaks at a thread pool, ``create_task`` or ``gather``. The
running workflow entry is also kept in a contextvar, which asyncio tasks and
``asyncio.to_thread`` inherit, so the entry level survives such a break.
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path
from typing import Any

# The site and entry state lives in ``context.scope`` so Context renderers
# can read it without importing the call core; these are the same objects.
from openprogram.runtime.context.scope import (
    CallSite,
    SiteFrame,
    WorkflowEntry,
    _current_entry,
    _current_site,
)
import logging

_log = logging.getLogger(__name__)

_extra_roots: list[str] = []
_file_cache: dict[str, bool] = {}


# Built-in Program sources written in the workflow form, relative to
# ``openprogram/programs``: the Goal tool package, ``ask_user`` and the
# authoring helpers.
_BUILTIN_WORKFLOW_SOURCES = (
    "tools/planning/goal",
    "tools/interaction/ask_user.py",
    "_authoring",
)


def builtin_workflow_roots() -> tuple[str, ...]:
    programs = Path(__file__).resolve().parents[2] / "programs"
    return tuple(os.path.realpath(programs / source) for source in _BUILTIN_WORKFLOW_SOURCES)


def user_workflow_root() -> str:
    """The user's Programs directory, ``~/.openprogram/programs``."""
    from openprogram.state.program_directory import programs_root
    return os.path.realpath(programs_root())


def _runtime_file(real: str) -> bool:
    """A file the application runtime keeps beside the user's Programs.

    ``.runtime/`` holds application environments (whole site-packages) and
    ``.removed/`` holds set-aside directories; neither is Program source.
    """
    from openprogram.state.program_directory import REMOVED_DIR, RUNTIME_DIR
    root = user_workflow_root()
    return any(real.startswith(os.path.join(root, name) + os.sep) for name in (RUNTIME_DIR, REMOVED_DIR))


def add_workflow_root(path: str | os.PathLike) -> None:
    """Treat functions defined under ``path`` as workflow functions."""
    root = os.path.realpath(path)
    if root not in _extra_roots:
        _extra_roots.append(root)
        _file_cache.clear()


def workflow_roots() -> tuple[str, ...]:
    roots = [*builtin_workflow_roots(), user_workflow_root(), *_extra_roots]
    return tuple(dict.fromkeys(roots))


def is_workflow_file(filename: str) -> bool:
    cached = _file_cache.get(filename)
    if cached is not None:
        return cached
    if not filename or filename.startswith("<"):
        result = False
    else:
        real = os.path.realpath(filename)
        result = not _runtime_file(real) and any(
            real == root or real.startswith(root + os.sep) for root in workflow_roots()
        )
    _file_cache[filename] = result
    return result


def _frame_function(frame) -> Any:
    code = frame.f_code
    qualname = getattr(code, "co_qualname", code.co_name)
    if "<locals>" in qualname or "<lambda>" in qualname:
        return None
    value: Any = frame.f_globals
    for index, part in enumerate(qualname.split(".")):
        value = value.get(part) if index == 0 else getattr(value, part, None)
        if value is None:
            return None
    if isinstance(value, (staticmethod, classmethod)):
        value = value.__func__
    source = inspect.unwrap(value) if callable(value) else None
    return source if getattr(source, "__code__", None) is code else None


def capture_frames(start=None) -> tuple[SiteFrame, ...]:
    """Workflow frames on the current stack, outermost first."""
    frame = start if start is not None else sys._getframe(1)
    found: list[SiteFrame] = []
    while frame is not None:
        if is_workflow_file(frame.f_code.co_filename):
            function = _frame_function(frame)
            code = frame.f_code
            found.append(SiteFrame(
                name=getattr(code, "co_qualname", code.co_name),
                doc=(inspect.getdoc(function) or "") if function is not None else "",
                module=frame.f_globals.get("__name__", ""),
                function=function,
            ))
        frame = frame.f_back
    found.reverse()
    return tuple(found)


def capture_site(start=None) -> CallSite | None:
    """The call site of a role call made by the frame ``start`` (default: caller)."""
    frames = capture_frames(start if start is not None else sys._getframe(1))
    entry = _current_entry.get()
    if not frames and entry is None:
        return None
    return CallSite(frames=frames, entry=entry)


class bind_site:
    """Make ``site`` the role-call site for the code inside the block."""

    def __init__(self, site: CallSite | None):
        self.site = site
        self.token = None

    def __enter__(self):
        from .call_state import current_call_id
        site = self.site
        if site is not None:
            site = CallSite(site.frames, site.entry, current_call_id() or None)
        self.token = _current_site.set(site)
        return site

    def __exit__(self, *exc):
        _current_site.reset(self.token)
        return False


class bind_entry:
    """Mark ``function`` as the running workflow entry."""

    def __init__(self, function):
        self.function = function
        self.token = None

    def __enter__(self):
        from openprogram.state.store import _store
        from .call_state import current_call_id
        entry = WorkflowEntry(self.function, _store.get(), current_call_id() or "")
        self.token = _current_entry.set(entry)
        return entry

    def __exit__(self, *exc):
        _current_entry.reset(self.token)
        return False


def in_role_call() -> CallSite | None:
    """The call site when the current frame is a workflow role call."""
    return _in_role_call()


def _in_role_call() -> CallSite | None:
    from .call_state import current_call_id
    site = _current_site.get()
    if site is not None and site.frame_call_id == (current_call_id() or None):
        return site
    return None


def request_doc() -> str:
    """The docstring for the request about to be sent.

    A role call made in the current frame supplies its caller's docstring.
    A deeper frame entered since then (a tool body, a function registered as
    a tool) supplies its own recorded docstring instead.
    """
    site = _in_role_call()
    if site is not None:
        return site.doc
    from .call_state import current_call_id
    from openprogram.state.store import _store
    frame_id = current_call_id()
    writer = _store.get()
    if not frame_id or writer is None:
        return ""
    try:
        node = writer.load().nodes.get(frame_id)
    except Exception:
        return ""
    return str(((node.metadata or {}).get("doc") if node is not None else "") or "")


def request_history(context, graph_history: list) -> list:
    """The history messages for one request, following ``context.history``.

    A workflow role call without an explicit setting sees no history.
    """
    site = _in_role_call()
    mode = context.history
    if site is not None and not context._history_explicit:
        mode = None
    if mode is None:
        return []
    if mode == "thread":
        return thread_messages(context.thread)
    if site is not None and site.entry is not None:
        return entry_chat_messages(site.entry, context)
    return list(graph_history)


def thread_messages(thread: list[dict]) -> list:
    from openprogram.models.providers.types import AssistantMessage, TextContent, UserMessage
    import time
    messages = []
    now = int(time.time() * 1000)
    for turn in thread:
        text = str(turn.get("text") or "")
        if turn.get("role") == "assistant":
            messages.append(AssistantMessage(
                role="assistant", content=[TextContent(text=text)], api="messages",
                provider="anthropic", model="history", timestamp=now))
        else:
            messages.append(UserMessage(role="user", content=[TextContent(text=text)], timestamp=now))
    return messages


def entry_chat_messages(entry: WorkflowEntry, context) -> list:
    """The conversation before the workflow entry was called."""
    writer = entry.writer
    if writer is None or not entry.call_id:
        return []
    from openprogram.runtime.context.nodes import render_context
    from openprogram.runtime.context.render import render_dag_messages
    graph = writer.load()
    node = graph.nodes.get(entry.call_id)
    if node is None:
        return []
    head, seen = node, set()
    while head.caller and head.caller in graph.nodes and head.id not in seen:
        seen.add(head.id)
        head = graph.nodes[head.caller]
    read_ids = render_context(graph, head_id=head.id, head_seq=node.seq - 1)
    if callable(context.history):
        read_ids = [node_id for node_id in read_ids if context.history(graph.nodes[node_id], context)]
    return render_dag_messages(graph, read_ids, None)


def role_name(instance, site: CallSite | None = None) -> str:
    """The module-level name a role instance is bound to, if any."""
    cached = getattr(instance, "_role_name", None)
    if cached is not None:
        return cached
    site = site if site is not None else _current_site.get()
    modules = [frame.module for frame in reversed(site.frames)] if site is not None else []
    for module_name in dict.fromkeys(modules):
        module = sys.modules.get(module_name)
        for key, value in list(vars(module).items()) if module is not None else ():
            if value is instance and not key.startswith("_"):
                try:
                    object.__setattr__(instance, "_role_name", key)
                except (AttributeError, TypeError):
                    # Slotted or builtin instances refuse the cache attribute; the name is
                    # simply re-resolved next time.
                    _log.debug("role name not cached on %r", type(instance).__name__, exc_info=True)
                return key
    return ""


__all__ = [
    "add_workflow_root",
    "bind_entry",
    "bind_site",
    "builtin_workflow_roots",
    "in_role_call",
    "capture_frames",
    "capture_site",
    "entry_chat_messages",
    "is_workflow_file",
    "request_doc",
    "request_history",
    "thread_messages",
    "role_name",
    "user_workflow_root",
    "workflow_roots",
]
