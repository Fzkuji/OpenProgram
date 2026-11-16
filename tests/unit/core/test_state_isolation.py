"""The suite must never resolve to the developer's real ``~/.openprogram``.

``get_state_dir()`` reads ``Path.home()``, so an unisolated test that builds a
SessionStore writes into live data — and ``SessionStore._startup_cleanup``
rewrites ``index.json`` and quarantines directories it judges shells on every
index load. A missing redirect is therefore not a tidiness issue, it alters
real conversations.

``tests/conftest.py`` redirects HOME at import time. This asserts the redirect
actually reaches the path layer, so the guard can't rot into a no-op.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from openprogram.state.paths import get_state_dir

pytestmark = pytest.mark.skipif(
    os.environ.get("OPENPROGRAM_TEST_REAL_HOME") == "1",
    reason="explicitly opted out of the HOME redirect",
)


def test_state_dir_is_a_throwaway():
    state = get_state_dir()
    assert "openprogram-test-home" in str(state), (
        f"tests resolve state to {state} — the conftest HOME redirect is not "
        "in effect, and a SessionStore built here would clean up REAL sessions"
    )


def test_state_dir_follows_home_rather_than_the_passwd_entry():
    """The redirect works by setting HOME, so ``Path.home()`` must honour it.

    On some platforms ``Path.home()`` can fall back to the passwd database,
    which env vars don't affect — that would silently defeat the isolation
    while the marker check above still passed for the wrong reason.
    """
    assert Path.home() == Path(os.environ["HOME"]), (
        "Path.home() ignores $HOME here, so redirecting HOME does not isolate "
        "state — the conftest guard needs a different mechanism on this platform"
    )


def test_session_store_root_is_a_throwaway():
    from openprogram.runtime.context.session_db import default_db

    root = str(getattr(default_db(), "root_path", ""))
    assert "openprogram-test-home" in root, (
        f"the default SessionStore points at {root} — its startup cleanup "
        "would delete real sessions"
    )


def _as_the_account_home(monkeypatch, folder):
    from openprogram.state import paths

    monkeypatch.setenv("HOME", str(folder))
    monkeypatch.setenv("USERPROFILE", str(folder))
    monkeypatch.setattr(paths, "_account_home_cache", [os.path.realpath(str(folder))])
    return paths


def test_a_test_that_resolves_the_accounts_own_state_is_refused(monkeypatch, tmp_path):
    """A test file run without this tree's conftest (a scratch copy,
    ``--noconftest``) keeps the real HOME; the path layer itself refuses."""
    paths = _as_the_account_home(monkeypatch, tmp_path)
    monkeypatch.delenv("OPENPROGRAM_TEST_REAL_HOME", raising=False)

    with pytest.raises(paths.RealStateUnderTest):
        paths.get_state_dir()
    monkeypatch.setenv("OPENPROGRAM_PROFILE", "dev")
    with pytest.raises(paths.RealStateUnderTest):
        paths.get_state_dir()
    assert not (tmp_path / ".openprogram").exists()
    assert not (tmp_path / ".openprogram-dev").exists()


def test_the_refusal_is_for_tests_only_and_can_be_opted_out_of(monkeypatch, tmp_path):
    paths = _as_the_account_home(monkeypatch, tmp_path)

    monkeypatch.setenv("OPENPROGRAM_TEST_REAL_HOME", "1")
    assert paths.get_state_dir() == tmp_path / ".openprogram"

    monkeypatch.delenv("OPENPROGRAM_TEST_REAL_HOME")
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    assert paths.get_state_dir() == tmp_path / ".openprogram"


def test_the_account_home_is_read_from_the_account_not_from_home():
    from openprogram.state import paths

    account = paths._account_home()
    assert account is not None
    assert "openprogram-test-home" not in account
