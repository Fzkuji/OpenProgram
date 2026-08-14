"""The suite must never resolve to the developer's real ``~/.openprogram``.

``get_state_dir()`` reads ``Path.home()``, so an unisolated test that builds a
SessionStore writes into live data — and ``SessionStore._startup_cleanup``
``shutil.rmtree``s any session it judges an empty shell on every index load.
A missing redirect is therefore not a tidiness issue, it deletes real
conversations.

``tests/conftest.py`` redirects HOME at import time. This asserts the redirect
actually reaches the path layer, so the guard can't rot into a no-op.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from openprogram.paths import get_state_dir

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
    from openprogram.agent.session_db import default_db

    root = str(getattr(default_db(), "root_path", ""))
    assert "openprogram-test-home" in root, (
        f"the default SessionStore points at {root} — its startup cleanup "
        "would delete real sessions"
    )
