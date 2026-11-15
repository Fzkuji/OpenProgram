"""Applications mount through the Programs loader, one ProgramContext each.

An application package is a directory with ``application.json``, its UI
and an optional Python backend. :func:`mount` is the single path for both
forms an application takes: a built-in one under
``programs/applications/<name>/`` (called by ``_registry`` beside its
``PROGRAM_ENTRIES`` and ``setup``) and an installed one recorded in
``catalog.json`` (called by :func:`load`). Through the application's
context it registers

- the served UI: the definition the asset route serves from the source
  directory, keyed by the content digest computed now;
- the ``application.<id>`` resource provider;
- one entry per operation marked ``agent: true``, named
  ``<id>__<operation>`` (see :func:`entry_name`), which runs the
  operation through the worker's application service — the same
  per-process runner and capability checks as the resource's ``act``.

An application directory placed directly in ``~/.openprogram/programs/``
is recorded on the next :func:`sync` (see
``catalog.adopt_user_applications``); one that needs trust waits for
``openprogram programs install <dir> --trust``.

``ctx.dispose()`` removes all three. Installed applications load in the
worker (:func:`sync` at start, after install/enable/disable, and when the
Programs watcher sees a source change); disabling one disposes its
context, and a changed source is disposed and loaded again with a fresh
digest.
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
import uuid
from pathlib import Path

from . import catalog

logger = logging.getLogger(__name__)

_lock = threading.RLock()
#: Serializes load, unload and sync: the watcher and the routes both call them.
_load_lock = threading.RLock()
#: app id → the definition it loaded with (manifest, digest, path).
_loaded: dict[str, dict] = {}
#: Built-in application ids (mounted by ``_registry``, never by :func:`sync`).
_builtin: set[str] = set()
#: app id → the reason its source did not load.
_errors: dict[str, str] = {}
#: app id → the tree stamp its digest was computed from.
_stamps: dict[str, tuple] = {}
_active = False


def owner(app_id: str) -> str:
    return "application:" + app_id


def entry_name(app_id: str, operation: str) -> str:
    """The tool name of an application operation: ``<id>__<operation>``.

    Model APIs accept only ``[A-Za-z0-9_-]`` in tool names, so the dots an
    application id may contain become ``_``; the double underscore keeps
    the application and the operation apart. A name over 64 characters
    keeps its first 55 and a hash of the whole.
    """
    name = re.sub(r"[^A-Za-z0-9_-]", "_", f"{app_id}__{operation}")
    if len(name) > 64:
        name = name[:55] + "_" + hashlib.sha256(name.encode()).hexdigest()[:8]
    return name


# lookups

def loaded(app_id: str, row: dict | None = None) -> dict | None:
    """The definition ``app_id`` loaded with, or ``None``.

    With a registration, only a definition loaded from that
    registration's source counts.
    """
    with _lock:
        definition = _loaded.get(app_id)
        builtin_app = app_id in _builtin
    if definition is None:
        return None
    if row is None:
        return dict(definition)
    if builtin_app:
        return None
    try:
        root = catalog.source_root(row)
    except FileNotFoundError:
        return None
    return dict(definition) if definition["path"] == str(root) else None


def builtin(app_id: str) -> dict | None:
    with _lock:
        return dict(_loaded[app_id]) if app_id in _builtin and app_id in _loaded else None


def builtin_definitions() -> list[dict]:
    with _lock:
        return [dict(_loaded[i]) for i in sorted(_builtin) if i in _loaded]


def builtin_names() -> set[str]:
    from openprogram.programs._host.registry import builtin_application_names
    return set(builtin_application_names())


def error(app_id: str) -> str:
    with _lock:
        return _errors.get(app_id, "")


def active() -> bool:
    """Whether this process loads installed applications (the worker does)."""
    return _active


# mounting

def mount(ctx, root: Path, row: dict | None = None) -> dict:
    """Register the application package at ``root`` through ``ctx``."""
    from openprogram.programs._host.ui.resource_provider import application_provider
    stamp = catalog.tree_stamp(root)
    definition = catalog.resolve(root, row)
    app_id = definition["id"]
    if row is None:
        definition["source"] = str(root)
    with _lock:
        _loaded[app_id] = definition
        _stamps[app_id] = stamp
        if row is None:
            _builtin.add(app_id)

    def withdraw() -> None:
        with _lock:
            if _loaded.get(app_id) is definition:
                _loaded.pop(app_id, None)
                _stamps.pop(app_id, None)
                _builtin.discard(app_id)

    ctx.effect(withdraw, f"ui {app_id}")
    ctx._record("ui", app_id)
    shown = dict(definition, **{k: row["application"][k] for k in ("display_title",) if row and k in row["application"]})
    ctx.resource(application_provider(shown))
    for name, operation in definition["operations"].items():
        if operation.get("agent"):
            fn, options = _entry(shown, name, operation)
            ctx.tool(fn, **options)
    return definition


_FINISHED = {"completed", "failed", "cancelled", "interrupted"}


def _entry(definition: dict, operation: str, spec: dict):
    """An Agent entry that runs one operation and waits for its result."""
    app_id = definition["id"]
    provider = "application." + app_id
    title = definition.get("display_title") or definition["title"]
    name = entry_name(app_id, operation)
    properties = {"input": spec.get("input") or {"type": "object"}}
    if definition["scope"] == "project":
        properties["project_id"] = {"type": "string", "description": "Project the application instance is bound to"}
    parameters = {"type": "object", "properties": properties, "required": ["input"], "additionalProperties": False}
    summary = (spec.get("description") or f"Run the {operation} operation of the {title} application.").strip()

    def run(input: dict, project_id: str = "") -> dict:
        from openprogram.host.resources import registry
        opened = registry.invoke(provider, "open", {"project_id": project_id} if project_id else {})
        identity = {"instance_id": opened["instance_id"], "digest": opened["digest"]}
        result = registry.invoke(provider, "act", {**identity, "operation": operation, "input": input,
                                                   "request_key": uuid.uuid4().hex})
        deadline = time.monotonic() + 300
        while result.get("status") not in _FINISHED and not result.get("question"):
            if time.monotonic() > deadline:
                break
            time.sleep(0.25)
            result = registry.invoke(provider, "status", {**identity, "run_id": result["id"]})
        result.pop("events", None)
        return {**result, **identity, "provider": provider}

    run.__name__ = run.__qualname__ = name
    run.__doc__ = (
        f"{summary}\n\nReturns the run once it finishes. A run still waiting on a question "
        f"or still running is returned with its id; continue it with the resource tool "
        f"(provider {provider}: status, answer or cancel).\n\n"
        f"Args:\n    input: The operation input.\n    project_id: Project for a project-scoped application."
    )
    options = {
        "name": name, "label": f"{title}: {operation}", "parameters": parameters,
        "toolset": ("applications",), "unsafe_in": ("plan",),
        "requires_approval": True, "defer": True, "timeout": 360,
        # Calls the worker's application service, like the resource tool.
        "run_in_worker": True,
    }
    return run, options


# installed applications

def load(app_id: str) -> dict | None:
    """Mount the installed application ``app_id`` if it is enabled."""
    with _load_lock:
        return _load(app_id)


def _load(app_id: str) -> dict | None:
    from openprogram.programs._host.registry import dispose_program, program_context
    row = catalog.record(app_id)
    unload(app_id)
    if row is None or not row["application"].get("enabled", True):
        return None
    try:
        if app_id in builtin_names():
            raise ValueError("application id belongs to a built-in application")
        root = catalog.source_root(row)
        ctx = program_context(owner(app_id), app_id, str(root))
        definition = mount(ctx, root, row)
    except Exception as exc:  # noqa: BLE001 — one broken package never blocks the rest
        dispose_program(owner(app_id))
        with _lock:
            _errors[app_id] = str(exc)
        logger.warning("application %s did not load: %s", app_id, exc)
        return None
    return definition


def unload(app_id: str) -> list[str]:
    """Dispose an installed application's context: UI, provider and entries go."""
    from openprogram.programs._host.registry import dispose_program
    with _load_lock:
        with _lock:
            if app_id in _builtin:
                return []
            _errors.pop(app_id, None)
        return dispose_program(owner(app_id))


def reload(app_id: str) -> dict | None:
    """Dispose and load again, recomputing the digest from the source.

    A built-in application is made to match its ``enabled`` setting: a
    disabled one is disposed, an enabled one is mounted again.
    """
    if app_id in builtin_names():
        from openprogram.programs._host.registry import sync_builtin_application
        ctx = sync_builtin_application(app_id)
        if ctx is None:
            return None
        return builtin(app_id) or {"id": app_id, "builtin": True, "enabled": True}
    return load(app_id)


def sync() -> list[str]:
    """Make the loaded applications match the catalog and their sources.

    Loads enabled applications that are not loaded, disposes ones that are
    disabled, uninstalled or moved, and reloads one whose source content
    changed. Returns the ids that changed.
    """
    global _active
    _active = True
    with _load_lock:
        return _sync()


def _sync() -> list[str]:
    catalog.adopt_user_applications()
    wanted = {r["application"]["id"]: r for r in catalog.records()
              if not r.get("uninstalled") and r["application"].get("enabled", True)}
    changed: list[str] = []
    with _lock:
        current = [i for i in _loaded if i not in _builtin]
        failed = list(_errors)
    for app_id in current:
        row = wanted.get(app_id)
        if row is None or loaded(app_id, row) is None:
            unload(app_id)
            changed.append(app_id)
    for app_id in failed:
        if app_id not in wanted:
            with _lock:
                _errors.pop(app_id, None)
    for app_id, row in wanted.items():
        definition = loaded(app_id, row)
        if definition is None:
            if load(app_id) is not None or app_id not in failed:
                changed.append(app_id)
            continue
        root = Path(definition["path"])
        stamp = catalog.tree_stamp(root)
        with _lock:
            if _stamps.get(app_id) == stamp:
                continue
        try:
            digest = catalog.package_digest(root)
        except (OSError, ValueError):
            digest = ""
        if digest == definition["digest"]:
            with _lock:
                _stamps[app_id] = stamp
            continue
        load(app_id)
        changed.append(app_id)
    return sorted(set(changed))


def unload_all() -> None:
    """Dispose every installed application, as the worker stops."""
    global _active
    with _lock:
        ids = [i for i in _loaded if i not in _builtin]
    for app_id in ids:
        unload(app_id)
    with _lock:
        _errors.clear()
    _active = False


def watch_stamp() -> tuple:
    """What the Programs watcher compares: the catalog and each enabled source."""
    try:
        stat = catalog.catalog_store.path().stat()
        stamp: list = [(stat.st_mtime_ns, stat.st_size)]
    except OSError:
        stamp = [None]
    try:
        # Unlocked: the catalog is replaced atomically, and a torn read
        # only delays the reload to the next poll.
        rows = [r for r in catalog.catalog_store._read()
                if r.get("kind") == "application" and isinstance(r.get("application"), dict)]
    except (OSError, ValueError):
        return tuple(stamp)
    for row in rows:
        if row.get("uninstalled") or not row["application"].get("enabled", True):
            continue
        try:
            root = catalog.source_root(row)
        except FileNotFoundError:
            stamp.append((row["application"]["id"], None))
            continue
        if _sealed_in_a_version(root):
            continue
        stamp.append((row["application"]["id"], str(root), catalog.tree_stamp(root)))
    return tuple(stamp)


def _sealed_in_a_version(root: Path) -> bool:
    """A built-in application inside a sealed version changes only by switching."""
    from openprogram.operations.self_update.versions import layout
    from openprogram.programs._host.registry import _builtin_applications_root

    if not layout.managed():
        return False
    return root.resolve().is_relative_to(Path(_builtin_applications_root()).resolve())
