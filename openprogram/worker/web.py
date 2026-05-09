"""Manage the Next.js frontend subprocess.

The worker hosts a FastAPI backend on a free port, and the Next.js
frontend on its own port. The Next.js process speaks to the backend
through Next's rewrites (configured to read ``OPENPROGRAM_BACKEND_URL``
at startup), so the user only ever sees the Next.js URL.

Lifecycle:
- :func:`start_web_frontend` spawns ``npm run start`` in ``web/``,
  passing ``OPENPROGRAM_BACKEND_URL=http://127.0.0.1:<backend_port>``.
- If ``web/.next/`` is missing it builds first.
- If ``node`` / ``npm`` is unavailable, returns ``None`` and the worker
  continues without the frontend (user can still use TUI).
- The returned :class:`subprocess.Popen` is stored so we can ``terminate``
  it on worker shutdown.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


def web_dir() -> Path:
    """Return the path to the ``web/`` directory bundled with the repo."""
    # openprogram/worker/web.py → repo_root/openprogram/worker/web.py
    # repo_root/web/
    return Path(__file__).resolve().parent.parent.parent / "web"


def _node_available() -> bool:
    return shutil.which("node") is not None and shutil.which("npm") is not None


def _ensure_built(wd: Path) -> bool:
    """Make sure ``web/.next/`` exists. Returns True on success."""
    next_dir = wd / ".next"
    if next_dir.exists():
        return True

    node_modules = wd / "node_modules"
    if not node_modules.exists():
        print("[worker] web: installing npm deps (first run, may take a while)…")
        r = subprocess.run(["npm", "install", "--silent"], cwd=str(wd))
        if r.returncode != 0:
            print("[worker] web: npm install failed")
            return False

    print("[worker] web: building production bundle (first run only)…")
    r = subprocess.run(["npm", "run", "build"], cwd=str(wd))
    if r.returncode != 0:
        print("[worker] web: build failed")
        return False
    return True


def start_web_frontend(
    *,
    backend_port: int,
    web_port: Optional[int] = None,
) -> Optional[subprocess.Popen]:
    """Spawn ``next start``. Returns the Popen, or None if unavailable."""
    if os.environ.get("OPENPROGRAM_NO_WEB", "").strip() in ("1", "true", "yes"):
        print("[worker] web: disabled by OPENPROGRAM_NO_WEB")
        return None

    wd = web_dir()
    if not wd.exists():
        return None

    if not _node_available():
        print("[worker] web: node/npm not found in PATH; skipping frontend")
        return None

    if not _ensure_built(wd):
        return None

    port = int(web_port or os.environ.get("OPENPROGRAM_WEB_PORT", "3000"))
    env = dict(os.environ)
    env["OPENPROGRAM_BACKEND_URL"] = f"http://127.0.0.1:{backend_port}"
    env["PORT"] = str(port)

    try:
        proc = subprocess.Popen(
            ["npm", "run", "start", "--", "-p", str(port)],
            cwd=str(wd),
            env=env,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
    except OSError as e:
        print(f"[worker] web: failed to spawn next start: {e}")
        return None

    print(f"[worker] web: http://127.0.0.1:{port} (backend → :{backend_port})")
    return proc


def stop_web_frontend(proc: Optional[subprocess.Popen], *, timeout: float = 5.0) -> None:
    if proc is None:
        return
    try:
        proc.terminate()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception:  # noqa: BLE001
        pass
