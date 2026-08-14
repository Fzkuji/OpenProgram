"""Cross-platform interactive pseudo-terminal driver — the testable mechanics.

Covers the InteractivePty backend that account-login flows drive (read a URL,
write a typed line, collect the exit code), deterministically on POSIX and
Windows. (The former Meridian `_meridian_cli` account-add tests were removed
along with that retired backend.)
"""
from __future__ import annotations

import re
import sys

import pytest


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
    the exit code. Mirrors a browser-OAuth login's read-URL / write-code dance."""
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
