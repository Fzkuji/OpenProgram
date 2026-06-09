"""Cross-platform Claude-account add: the testable mechanics.

The live browser-OAuth round trip (Meridian -> `claude auth login` -> browser)
needs a real Claude login and so can't run in CI; these cover everything around
it that CAN be exercised deterministically on both POSIX and Windows.
"""
from __future__ import annotations

import re
import subprocess
import sys

import pytest


# --------------------------------------------------------------------------
# InteractivePty — the cross-platform pseudo-terminal driver
# --------------------------------------------------------------------------

def test_interactive_pty_available():
    from openprogram._compat import interactive_pty_available
    # POSIX always has pty; Windows has it iff pywinpty imported. Either way the
    # probe must return a bool without raising.
    assert isinstance(interactive_pty_available(), bool)


@pytest.mark.skipif(
    not __import__("openprogram._compat", fromlist=["interactive_pty_available"]).interactive_pty_available(),
    reason="no pseudo-terminal backend on this host",
)
def test_interactive_pty_roundtrip(tmp_path):
    """Spawn a child that prints a URL then reads a line — assert we capture the
    URL, deliver a typed line (with the plain "\\n" callers use), and collect
    the exit code. Mirrors the meridian login's read-URL / write-code dance."""
    from openprogram._compat import InteractivePty

    child = tmp_path / "child.py"
    child.write_text(
        "import sys\n"
        "print('go to https://claude.ai/oauth?state=xyz to sign in', flush=True)\n"
        "line = sys.stdin.readline().strip()\n"
        "sys.exit(0 if line == 'code#xyz' else 7)\n"
    )
    drv = InteractivePty([sys.executable, str(child)])
    try:
        import time
        buf, url, end = "", None, time.time() + 15
        while time.time() < end:
            buf += drv.read_nonblocking(0.5)
            m = re.search(r"https://\S+", buf)
            if m:
                url = m.group(0)
                break
        assert url and url.startswith("https://claude.ai/oauth")
        drv.write("code#xyz\n")  # plain \n; Windows path translates to \r\n
        rc = drv.wait(timeout=15)
        assert rc == 0
    finally:
        drv.close()
    assert drv.alive is False


# --------------------------------------------------------------------------
# Meridian capability detection + guards (no backend / network needed)
# --------------------------------------------------------------------------

def test_prerequisites_shape():
    from openprogram.providers.anthropic import _meridian_cli as m
    p = m.prerequisites()
    for key in ("claude_installed", "backend_installed", "browser_login", "token_login"):
        assert isinstance(p[key], bool), key
    assert p["token_login"] is True            # token paste is always offered
    assert p["claude_install_cmd"].startswith("npm install -g @anthropic-ai/claude-code")
    # browser_login must reflect the pty backend, not be hard-coded.
    from openprogram._compat import interactive_pty_available
    assert p["browser_login"] == interactive_pty_available()


def test_add_with_token_rejects_garbage_before_spawning(monkeypatch):
    """A token that isn't sk-ant-… is rejected up front — never reaching the
    backend (so this passes with no meridian installed)."""
    from openprogram.providers.anthropic import _meridian_cli as m

    def _boom(*a, **k):  # would be called only if we wrongly spawned
        raise AssertionError("must not spawn the backend for a malformed token")

    monkeypatch.setattr(m.subprocess, "run", _boom)
    assert m.add_with_token("x", "")["ok"] is False
    assert m.add_with_token("x", "not-a-token")["ok"] is False


def test_ensure_backend_install_timeout_is_caught(monkeypatch):
    """A wedged `npm install` surfaces as a clean error, not a hang/traceback —
    the root cause of the original 'Add account does nothing' symptom."""
    from openprogram.providers.anthropic import _meridian_cli as m

    monkeypatch.setattr(m, "_proxy_bin", lambda: None)      # force the install path
    monkeypatch.setattr(m.shutil, "which", lambda _n: "npm")  # npm present

    def _timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="npm", timeout=300)

    monkeypatch.setattr(m.subprocess, "run", _timeout)
    out = m.ensure_backend()
    assert out["ready"] is False
    assert "timed out" in out["error"].lower()


def test_poll_add_loopback_completion(monkeypatch):
    """poll_add finalizes when the backend process exited on its own (the
    localhost-loopback case — no code to paste), and reports 'waiting' while
    it's still alive."""
    from openprogram.providers.anthropic import _meridian_cli as m

    class _FakeDrv:
        def __init__(self, alive):
            self._alive = alive
        @property
        def alive(self):
            return self._alive
        def wait(self, timeout=None):
            return 0
        def close(self):
            pass

    monkeypatch.setattr(m, "_finalize_added_account", lambda e: e["name"])
    # still running -> not done
    m._PENDING_LOGINS["s1"] = {"driver": _FakeDrv(True), "name": "account-1", "auto": True}
    assert m.poll_add("s1") == {"done": False}
    # exited cleanly -> done + ok, and the session is consumed
    m._PENDING_LOGINS["s2"] = {"driver": _FakeDrv(False), "name": "account-1", "auto": True}
    out = m.poll_add("s2")
    assert out == {"done": True, "ok": True, "name": "account-1"}
    assert "s2" not in m._PENDING_LOGINS
    # unknown session -> done + not ok
    assert m.poll_add("nope")["done"] is True
    m._PENDING_LOGINS.pop("s1", None)


def test_proxy_bin_falls_back_to_npm_prefix(monkeypatch, tmp_path):
    """When the npm global bin isn't on PATH, _proxy_bin still finds the shim
    under the npm prefix (the Windows %APPDATA%\\npm case)."""
    from openprogram.providers.anthropic import _meridian_cli as m

    name = "meridian.cmd" if sys.platform == "win32" else "meridian"
    bindir = tmp_path if sys.platform == "win32" else (tmp_path / "bin")
    bindir.mkdir(parents=True, exist_ok=True)
    (bindir / name).write_text("@echo off\n" if sys.platform == "win32" else "#!/bin/sh\n")

    monkeypatch.setattr(m.shutil, "which", lambda _n: None)   # not on PATH
    monkeypatch.setattr(m, "_NPM_PREFIX_CACHE", str(tmp_path))  # cache the prefix
    found = m._proxy_bin()
    assert found is not None and found.endswith(name)
