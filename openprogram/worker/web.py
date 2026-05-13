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


_MANIFEST_FILES = ("routes-manifest.json",)


def _patch_manifest_ports(wd: Path, backend_port: int) -> bool:
    """Rewrite ``127.0.0.1:<port>`` in the next manifest(s) to the live port.

    Next bakes rewrite destinations into JSON files at build time. Rather
    than re-running ``next build`` (30+s), patch the JSON in place — the
    next-server reads these files at startup, so a fresh ``next start``
    picks up the new port immediately.

    Returns True if the manifest is now consistent with backend_port (or
    didn't need patching), False on parse / write failure.
    """
    import json
    import re
    target = f"127.0.0.1:{backend_port}"
    pattern = re.compile(r"127\.0\.0\.1:\d+")
    ok = True
    for fname in _MANIFEST_FILES:
        path = wd / ".next" / fname
        if not path.exists():
            continue
        try:
            text = path.read_text()
        except OSError:
            ok = False
            continue
        new_text = pattern.sub(target, text)
        if new_text == text:
            continue
        try:
            json.loads(new_text)  # sanity check
            path.write_text(new_text)
            print(f"[worker] web: patched {fname} → :{backend_port}")
        except (json.JSONDecodeError, OSError) as e:
            print(f"[worker] web: failed to patch {fname}: {e}")
            ok = False
    return ok


def _ensure_built(wd: Path, *, backend_port: int) -> bool:
    """Make sure ``web/.next/`` exists, then patch manifest port to match.

    Only runs ``next build`` on first launch (or if .next was wiped).
    Subsequent worker runs reuse the prebuilt bundle and just patch the
    manifest's rewrite destination, which is orders of magnitude faster.
    """
    next_dir = wd / ".next"

    if not next_dir.exists():
        node_modules = wd / "node_modules"
        if not node_modules.exists():
            print("[worker] web: installing npm deps (first run, may take a while)…")
            r = subprocess.run(["npm", "install", "--silent"], cwd=str(wd))
            if r.returncode != 0:
                print("[worker] web: npm install failed")
                return False

        print("[worker] web: building production bundle (first run only)…")
        build_env = dict(os.environ)
        build_env["OPENPROGRAM_BACKEND_URL"] = f"http://127.0.0.1:{backend_port}"
        r = subprocess.run(["npm", "run", "build"], cwd=str(wd), env=build_env)
        if r.returncode != 0:
            print("[worker] web: build failed")
            return False

    return _patch_manifest_ports(wd, backend_port)


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

    # Default frontend port is 8764 — sits adjacent to the backend
    # (8765) so the pair is easy to remember, and stays clear of the
    # crowded 3000 / 8080 / 5000 range that Next.js / Tomcat / Flask
    # default to. Override with OPENPROGRAM_WEB_PORT or `--web-port`.
    port = int(web_port or os.environ.get("OPENPROGRAM_WEB_PORT", "8764"))
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

    # Watch .next/BUILD_ID — when a fresh `npm run build` writes a new
    # build, the old next-server process is still serving the previous
    # BUILD_ID's chunks (it caches manifests in memory at startup).
    # Browsers then load HTML pointing at <hash>.css that no longer
    # exists on disk, and the page renders unstyled. Restarting the
    # next subprocess on every BUILD_ID change makes that race
    # invisible — the user's hard refresh just picks up a coherent
    # build without anyone running `worker restart` by hand.
    _start_build_id_watcher(wd, backend_port, port, env, cmd, proc)
    return proc


# In-process handle to the live next subprocess. The watcher thread
# updates this when it restarts next; stop_web_frontend reads it so a
# clean shutdown still kills the *current* child, not whatever Popen
# the caller saved at spawn time.
_LIVE_PROC_LOCK = threading.Lock() if False else None  # placeholder
import threading as _threading  # noqa: E402
_live_proc_lock = _threading.Lock()
_live_proc: Optional[subprocess.Popen] = None


def _set_live_proc(proc: Optional[subprocess.Popen]) -> None:
    global _live_proc
    with _live_proc_lock:
        _live_proc = proc


def _start_build_id_watcher(
    wd: Path,
    backend_port: int,
    port: int,
    env: dict,
    cmd: list,
    initial_proc: subprocess.Popen,
) -> None:
    _set_live_proc(initial_proc)

    build_id_path = wd / ".next" / "BUILD_ID"

    def _read_id() -> Optional[str]:
        try:
            return build_id_path.read_text().strip()
        except OSError:
            return None

    last_seen = _read_id()

    def _loop() -> None:
        import time as _t
        nonlocal last_seen
        while True:
            _t.sleep(2.0)
            cur = _read_id()
            if cur is None or cur == last_seen:
                continue
            last_seen = cur
            print(f"[worker] web: detected new BUILD_ID {cur} — restarting next")
            with _live_proc_lock:
                old = _live_proc
            if old is not None:
                try:
                    old.terminate()
                    try:
                        old.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        old.kill()
                except Exception:  # noqa: BLE001
                    pass
            # Reclaim the port (the watcher script around next will
            # have exited by now, but a SIGKILL on the wrapper can
            # leave next-server orphaned holding the socket).
            _reclaim_web_port(port)
            # Re-patch the freshly-built routes-manifest. `npm run
            # build` evaluates next.config.mjs's rewrites() with
            # whatever OPENPROGRAM_BACKEND_URL was in env at build
            # time, which often disagrees with the live worker port —
            # without this, the new next-server proxies /api → wrong
            # port and every request 500's.
            _patch_manifest_ports(wd, backend_port)
            try:
                new_proc = subprocess.Popen(
                    cmd,
                    cwd=str(wd),
                    env=env,
                    stdout=sys.stdout,
                    stderr=sys.stderr,
                )
                _set_live_proc(new_proc)
                print(f"[worker] web: respawned next (PID {new_proc.pid})")
            except OSError as e:
                print(f"[worker] web: respawn failed: {e}")

    _threading.Thread(
        target=_loop, name="web-build-id-watcher", daemon=True
    ).start()


def stop_web_frontend(proc: Optional[subprocess.Popen], *, timeout: float = 5.0) -> None:
    # Prefer the watcher's live handle (it tracks restarts); fall back
    # to whatever Popen the caller supplied.
    target = proc
    with _live_proc_lock:
        if _live_proc is not None:
            target = _live_proc
    if target is None:
        return
    try:
        target.terminate()
        try:
            target.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            target.kill()
    except Exception:  # noqa: BLE001
        pass
