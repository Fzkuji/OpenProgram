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


def _reclaim_web_port(port: int) -> None:
    """Kill any leftover Next.js process holding ``port``.

    A previous worker that crashed (or was killed without running its
    shutdown hook) can leave its child ``next-server`` orphaned and still
    bound to the web port. The new worker would then fail with EADDRINUSE.
    Detect that case and clear the port before we spawn our own.

    Conservative: only kills processes whose command line looks like the
    Next.js server, never anything else listening on that port.
    """
    try:
        out = subprocess.run(
            ["lsof", "-iTCP:%d" % port, "-sTCP:LISTEN", "-nP", "-Fp"],
            capture_output=True, text=True, timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    pids = [int(line[1:]) for line in out.stdout.splitlines() if line.startswith("p")]
    if not pids:
        return

    import signal
    import time as _time
    for pid in pids:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmdline = f.read().decode("utf-8", "replace")
        except OSError:
            try:
                ps = subprocess.run(
                    ["ps", "-p", str(pid), "-o", "command="],
                    capture_output=True, text=True, timeout=2,
                )
                cmdline = ps.stdout
            except (OSError, subprocess.TimeoutExpired):
                continue
        if "next-server" not in cmdline and "next/dist/bin/next" not in cmdline:
            print(f"[worker] web: port {port} held by PID {pid} (not next); leaving alone")
            continue
        print(f"[worker] web: reclaiming port {port} from leftover next PID {pid}")
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            continue
        for _ in range(20):
            _time.sleep(0.1)
            try:
                os.kill(pid, 0)
            except OSError:
                break
        else:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


def _manifest_backend_port(wd: Path) -> Optional[int]:
    """Read the backend port baked into ``.next/routes-manifest.json``.

    Returns None if the manifest is missing or unparseable. Next bakes
    rewrite destinations at ``next build`` time, so this tells us which
    backend the bundle is hard-wired to.
    """
    import json
    import re
    manifest = wd / ".next" / "routes-manifest.json"
    if not manifest.exists():
        return None
    try:
        data = json.loads(manifest.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    for rewrite in data.get("rewrites", []) or []:
        dest = rewrite.get("destination", "") if isinstance(rewrite, dict) else ""
        m = re.search(r"127\.0\.0\.1:(\d+)", dest)
        if m:
            return int(m.group(1))
    return None


def _ensure_built(wd: Path, *, backend_port: int) -> bool:
    """Make sure ``web/.next/`` exists and is wired to the live backend port.

    Rebuilds when the manifest is missing OR when its baked rewrite target
    points at a different port than the worker is currently listening on
    (e.g. the previous worker grabbed a different free port). Build env
    sets OPENPROGRAM_BACKEND_URL so the new manifest matches.
    """
    next_dir = wd / ".next"
    needs_build_reason = None
    if not next_dir.exists():
        needs_build_reason = "first run"
    else:
        baked = _manifest_backend_port(wd)
        if baked is None:
            needs_build_reason = "manifest missing rewrites"
        elif baked != backend_port:
            needs_build_reason = f"manifest points at :{baked}, worker on :{backend_port}"

    if needs_build_reason is None:
        return True

    node_modules = wd / "node_modules"
    if not node_modules.exists():
        print("[worker] web: installing npm deps (first run, may take a while)…")
        r = subprocess.run(["npm", "install", "--silent"], cwd=str(wd))
        if r.returncode != 0:
            print("[worker] web: npm install failed")
            return False

    print(f"[worker] web: rebuilding bundle ({needs_build_reason})…")
    build_env = dict(os.environ)
    build_env["OPENPROGRAM_BACKEND_URL"] = f"http://127.0.0.1:{backend_port}"
    r = subprocess.run(["npm", "run", "build"], cwd=str(wd), env=build_env)
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

    if not _ensure_built(wd, backend_port=backend_port):
        return None

    port = int(web_port or os.environ.get("OPENPROGRAM_WEB_PORT", "3000"))
    _reclaim_web_port(port)
    env = dict(os.environ)
    env["OPENPROGRAM_BACKEND_URL"] = f"http://127.0.0.1:{backend_port}"
    env["PORT"] = str(port)
    env["OPENPROGRAM_PARENT_PID"] = str(os.getpid())

    watcher = wd / "scripts" / "with-parent-watch.mjs"
    cmd = (
        ["node", str(watcher)]
        if watcher.exists()
        else ["npm", "run", "start", "--", "-p", str(port)]
    )

    try:
        proc = subprocess.Popen(
            cmd,
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
