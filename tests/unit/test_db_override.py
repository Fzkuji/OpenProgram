"""ContextVar-scoped override for default_db() (E part 3 plumbing)."""
from __future__ import annotations

import pytest

from openprogram.agent.session_db import (
    default_db, set_db_override, reset_db_override,
)
from openprogram.store.session_store import SessionStore


def test_default_db_returns_singleton_without_override():
    a = default_db()
    b = default_db()
    assert a is b


def test_override_swaps_returned_store(tmp_path):
    sub = SessionStore(tmp_path / "sub")
    tok = set_db_override(sub)
    try:
        assert default_db() is sub
    finally:
        reset_db_override(tok)
    # After reset, singleton is back.
    assert default_db() is not sub


def test_override_does_not_persist_across_threads(tmp_path):
    """ContextVar overrides shouldn't leak to threads that did not
    inherit the override's context — Thread() doesn't copy_context."""
    import threading
    sub = SessionStore(tmp_path / "sub")
    seen = []

    def in_thread():
        seen.append(default_db() is sub)

    tok = set_db_override(sub)
    try:
        t = threading.Thread(target=in_thread)
        t.start()
        t.join()
    finally:
        reset_db_override(tok)
    assert seen == [False]


def test_override_propagates_via_copy_context(tmp_path):
    """ContextVar overrides DO propagate when the thread inherits via
    copy_context — the dispatcher's runtime wraps tool execution this
    way, so the sub-agent override has to survive the bridge."""
    import contextvars, threading
    sub = SessionStore(tmp_path / "sub2")
    seen = []

    def in_ctx():
        seen.append(default_db() is sub)

    tok = set_db_override(sub)
    try:
        ctx = contextvars.copy_context()
        t = threading.Thread(target=lambda: ctx.run(in_ctx))
        t.start()
        t.join()
    finally:
        reset_db_override(tok)
    assert seen == [True]
