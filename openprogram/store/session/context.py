"""Unified per-turn session context.

OpenProgram's core agentic features — function docstrings flowing into the
prompt, DAG persistence, ask_user tracking, nested called_by attribution —
all depend on a set of per-turn ContextVars (``_store`` /
``_current_turn_id`` / ``_current_runtime``) built from a ``SessionStore``
and a ``session_id``.

That wiring used to live ONLY inlined inside the web dispatcher
(``process_user_turn``), so command-line / client entry points (e.g. the
research harness) ran with ``_store=None`` and silently lost all of the
above — the same function behaved one way on the web and another on the CLI.

``session_context`` is the single place that installs and tears down that
wiring, so every entry point (dispatcher, CLI, research harness,
subprocess, tests) behaves identically.

Session boundary rule — by who passes ``session_id``, NOT by call count:

* pass an existing ``session_id``  -> reuse it (history continues)
* pass a new ``session_id``        -> create with that id
* pass ``None``                    -> create a fresh one; the id is on the
                                       returned handle so the caller can
                                       pass it back next time to continue

Sessions are append-only git history: there is no explicit "close". Exiting
the context only resets the ContextVars; it never deletes the session.
"""
from __future__ import annotations

import uuid as _uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, Optional


@dataclass
class SessionHandle:
    """What ``session_context`` yields — identifies the active session so a
    caller can persist / print the id and continue it later."""
    db: Any
    session_id: str
    runtime: Any
    turn_id: str
    created: bool  # True if this call created the session (vs reused)


def _short_uuid() -> str:
    return _uuid.uuid4().hex[:10]


@contextmanager
def session_context(
    session_id: Optional[str] = None,
    *,
    agent_id: str = "main",
    turn_id: Optional[str] = None,
    runtime: Any = None,
    create_runtime_if_none: bool = True,
    id_prefix: str = "session",
    source: Optional[str] = "cli",
) -> Iterator[SessionHandle]:
    """Install the per-turn session ContextVars; reset them on exit.

    Args:
        session_id: existing id to continue, a new id to create under, or
                    None to mint one (returned on the handle).
        agent_id:   agent the session belongs to (meta only).
        turn_id:    this turn's id (for file-backup attribution). Minted if
                    None.
        runtime:    reuse this runtime; else create one when
                    ``create_runtime_if_none`` and a provider is configured.
        create_runtime_if_none: build a runtime when none is supplied.
        id_prefix:  prefix for a minted session id (e.g. "research").
        source:     session source tag stored on meta.

    Yields:
        SessionHandle(db, session_id, runtime, turn_id, created).
    """
    # Lazy imports: keep module import cheap and avoid import cycles
    # (dispatcher / providers pull heavy chains).
    from openprogram.agent.session_db import default_db
    from openprogram.store import (
        _store as _store_var,
        _current_turn_id as _turn_id_var,
        GraphStoreShim,
    )
    from openprogram.agentic_programming.function import (
        _current_runtime as _runtime_var,
    )

    db = default_db()
    created = False
    sid = session_id or f"{id_prefix}_{_short_uuid()}"
    if db.get_session(sid) is None:
        db.create_session(sid, agent_id, source=source)
        created = True

    rt = runtime
    if rt is None and create_runtime_if_none:
        # A missing provider must NOT crash the whole run — install the
        # store anyway (DAG persistence + docstring rendering still want
        # it); only the auto-injected runtime is skipped, exactly like the
        # dispatcher's degrade path.
        try:
            from openprogram.providers.registry import create_runtime as _mk
            rt = _mk()
        except Exception:
            rt = None

    tid = turn_id or f"turn_{_short_uuid()}"

    # Install. Each entry: (ContextVar, token). Reset in reverse on exit.
    installed: list[tuple[Any, Any]] = []
    installed.append((_store_var, _store_var.set(GraphStoreShim(db, sid))))
    installed.append((_turn_id_var, _turn_id_var.set(tid)))
    if rt is not None:
        installed.append((_runtime_var, _runtime_var.set(rt)))

    try:
        yield SessionHandle(db=db, session_id=sid, runtime=rt,
                            turn_id=tid, created=created)
    finally:
        for var, token in reversed(installed):
            try:
                var.reset(token)
            except Exception:
                pass
