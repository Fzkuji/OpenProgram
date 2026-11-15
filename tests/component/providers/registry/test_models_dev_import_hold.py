"""The models.dev refresh thread never starts while the providers package imports.

Importing any ``openprogram.models.providers.utils.*`` module first (as the
runtime's agent loop does) used to fail with ``_DeadlockError``: the package
import built the enabled-model registry, that read found the disk catalogue
stale and started the refresh thread, and the thread's import of
``utils.http_proxy`` crossed the importing thread's import of ``utils``.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from openprogram.models.providers.catalog.sources import models_dev as md

REPO_ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def spawned(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(md, "_spawn_refresh_thread", lambda: calls.append(1))
    monkeypatch.setattr(md, "_refresh_held", False)
    monkeypatch.setattr(md, "_refresh_wanted", False)
    return calls


def test_refresh_starts_at_once_without_a_hold(spawned):
    md._start_background_refresh()
    assert spawned == [1]


def test_refresh_under_hold_waits_for_the_release(spawned):
    md.hold_background_refresh()
    md._start_background_refresh()
    md._start_background_refresh()
    assert spawned == [], "no thread while the package imports"

    md.release_background_refresh()
    assert spawned == [1], "one thread for the refresh the import wanted"
    assert md._refresh_wanted is False

    md.release_background_refresh()
    assert spawned == [1], "a release with nothing pending starts nothing"


def test_release_without_a_pending_refresh_starts_nothing(spawned):
    md.hold_background_refresh()
    md.release_background_refresh()
    assert spawned == []


def test_importing_a_utils_module_first_with_a_stale_catalogue(tmp_path):
    """A fresh interpreter, a provider the registry resolves through models.dev
    and a stale disk catalogue: the refresh thread is wanted mid-import."""
    home = tmp_path / "home"
    state = home / ".openprogram"
    (state / "cache").mkdir(parents=True)
    (state / "cache" / "models_dev.json").write_text(json.dumps({
        "deepinfra": {"id": "deepinfra", "name": "DeepInfra", "api": "https://api.deepinfra.com/v1/openai", "models": {}},
    }), encoding="utf-8")
    (state / "config.json").write_text(json.dumps({
        "providers": {"deepinfra": {"enabled": True, "models": [{"id": "x"}]}},
    }), encoding="utf-8")
    script = textwrap.dedent(
        """
        import openprogram.host.security.safe_http as safe_http

        class _NoNetwork:
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def get(self, *_, **__):
                raise OSError("no network in tests")

        def fake_client(*_, **__):
            # What the real client does on its way to the socket.
            from openprogram.models.providers.utils.http_proxy import get_proxy_mounts  # noqa: F401
            return _NoNetwork()

        safe_http.safe_client = fake_client
        import openprogram.models.providers.utils.failover  # noqa: F401
        from openprogram.models.providers.catalog.sources import models_dev
        thread = models_dev._refresh_thread
        assert thread is not None, "the stale catalogue did not ask for a refresh"
        thread.join(timeout=10)
        print("imported")
        """
    )
    env = {**os.environ, "HOME": str(home), "PYTHONPATH": str(REPO_ROOT)}
    env.pop("OPENPROGRAM_PROFILE", None)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert "imported" in result.stdout
