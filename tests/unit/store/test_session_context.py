"""Tests for the unified session_context manager."""
from __future__ import annotations

import pytest

from openprogram.store.session.context import session_context
from openprogram.store import _store as _store_var, _current_turn_id as _turn_var
from openprogram.agentic_programming.function import _current_runtime as _rt_var


def test_installs_and_resets_store(monkeypatch, tmp_path):
    # Point the default store at a temp dir so we don't touch ~/.openprogram.
    from openprogram.store.session import session_store as ss
    monkeypatch.setattr(ss, "_default_store", None, raising=False)
    monkeypatch.setattr(ss, "_default_root", lambda: tmp_path / "sessions", raising=False)

    assert _store_var.get(None) is None  # standalone before
    with session_context(create_runtime_if_none=False) as h:
        # store + turn id installed inside the context
        assert _store_var.get(None) is not None
        assert _turn_var.get(None) == h.turn_id
        assert h.session_id  # minted
        assert h.created is True
    # reset after
    assert _store_var.get(None) is None
    assert _turn_var.get(None) is None


def test_reuses_passed_session_id(monkeypatch, tmp_path):
    from openprogram.store.session import session_store as ss
    monkeypatch.setattr(ss, "_default_store", None, raising=False)
    monkeypatch.setattr(ss, "_default_root", lambda: tmp_path / "sessions", raising=False)

    with session_context(session_id="mysess", create_runtime_if_none=False) as h1:
        assert h1.session_id == "mysess"
        assert h1.created is True
    # second call with same id reuses (created False), history continues
    with session_context(session_id="mysess", create_runtime_if_none=False) as h2:
        assert h2.session_id == "mysess"
        assert h2.created is False


def test_none_mints_new_id_each_unrelated_call(monkeypatch, tmp_path):
    from openprogram.store.session import session_store as ss
    monkeypatch.setattr(ss, "_default_store", None, raising=False)
    monkeypatch.setattr(ss, "_default_root", lambda: tmp_path / "sessions", raising=False)

    ids = []
    for _ in range(2):
        with session_context(create_runtime_if_none=False, id_prefix="research") as h:
            ids.append(h.session_id)
            assert h.session_id.startswith("research_")
    assert ids[0] != ids[1]  # unrelated calls -> distinct sessions
