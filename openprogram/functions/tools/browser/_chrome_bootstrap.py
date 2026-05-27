"""Auto-launch a sidecar Chrome instance with CDP enabled.

The browser tool's `open` action calls into here when it has no CDP
target wired up. The sidecar lives at `~/.openprogram/chrome-profile`
(a copy of the user's real Chrome profile so saved logins / extensions
work) and listens on a fixed port. Subsequent open() calls just
connect_over_cdp to the running sidecar.

Why a sidecar instead of attaching to the real Chrome:
  - Chrome 134+ silently refuses to expose CDP when the user-data-dir
    is the production profile (anti-credential-scraping measure).
  - Same flag against a different user-data-dir works fine.
  - Copying the production profile preserves cookies/extensions while
    bypassing the path check.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional


DEFAULT_PORT = 9222


def chrome_binary() -> Optional[str]:
    """Best-effort Chrome path lookup."""
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ]
    for p in candidates:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return shutil.which("google-chrome") or shutil.which("chromium") or None


def real_user_data_dir() -> str:
    """Where Chrome stores its real profile on this OS."""
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Application Support", "Google", "Chrome")
    if sys.platform.startswith("win"):
        return os.path.join(home, "AppData", "Local", "Google", "Chrome", "User Data")
    return os.path.join(home, ".config", "google-chrome")


def sidecar_dir() -> Path:
    return Path.home() / ".openprogram" / "chrome-profile"


def port_file() -> Path:
    return Path.home() / ".openprogram" / "browser-cdp-port"


def is_port_listening(port: int, host: str = "127.0.0.1", timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, port), timeout):
            return True
    except OSError:
        return False


def read_last_used_profile(user_data_dir: str) -> str:
    """Default profile name from Chrome's Local State JSON, fallback 'Default'."""
    path = Path(user_data_dir) / "Local State"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            picked = data.get("profile", {}).get("last_used")
            if picked:
                return picked
        except (OSError, ValueError):
            pass
    return "Default"


def ensure_sidecar_profile() -> bool:
    """Copy real profile to sidecar if sidecar missing. Returns True if ready.

    Skips the high-volume cache directories (Cache / Code Cache /
    GPUCache / ServiceWorker/CacheStorage / Crashpad / GraphiteDawnCache
    / GrShaderCache). They're regenerated on first browse and
    contribute the bulk of a Chrome profile's size — a 3-4GB profile
    typically shrinks to ~300-700MB after these are skipped, and the
    first launch takes seconds instead of minutes.
    """
    sidecar = sidecar_dir()
    if (sidecar / "Default").exists():
        return True
    src_root = real_user_data_dir()
    src_default = Path(src_root) / "Default"
    src_local_state = Path(src_root) / "Local State"
    if not src_default.exists():
        return False
    sidecar.mkdir(parents=True, exist_ok=True)

    # rsync with --exclude is dramatically faster than cp -R for big
    # profiles AND lets us drop cache dirs in one pass. Falls back to
    # cp -R when rsync isn't available.
    excludes = [
        "Cache", "Code Cache", "GPUCache", "GraphiteDawnCache",
        "GrShaderCache", "Service Worker/CacheStorage",
        "Service Worker/ScriptCache", "DawnGraphiteCache",
        "Application Cache", "ShaderCache", "Crashpad",
        "Crash Reports", "Storage/ext/*/def/Cache",
        "*-journal", "lockfile", "SingletonCookie",
        "SingletonLock", "SingletonSocket",
    ]
    rsync = shutil.which("rsync")
    if rsync:
        cmd = [rsync, "-a", "--delete"]
        for ex in excludes:
            cmd.extend(["--exclude", ex])
        cmd.extend([str(src_default) + "/", str(sidecar / "Default") + "/"])
        # rsync needs the destination to exist.
        (sidecar / "Default").mkdir(parents=True, exist_ok=True)
        subprocess.run(cmd, check=False)
    else:
        subprocess.run(
            ["cp", "-R", str(src_default), str(sidecar / "Default")],
            check=False,
        )
    if src_local_state.exists():
        subprocess.run(
            ["cp", str(src_local_state), str(sidecar / "Local State")],
            check=False,
        )
    return True


def _bootstrap_lock_path() -> Path:
    return Path.home() / ".openprogram" / "browser-cdp.lock"


def _acquire_bootstrap_lock(timeout_s: float = 60.0):
    """File-lock so two simultaneous bootstrap calls don't race.

    Returns an open file object that the caller MUST keep alive (close
    releases the lock). Blocks up to timeout_s for another bootstrap to
    finish, then proceeds (the second caller will likely find the port
    already up and short-circuit).
    """
    from openprogram import _compat as fcntl
    lock_path = _bootstrap_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fp = open(lock_path, "w", encoding="utf-8")
    deadline = time.time() + timeout_s
    while True:
        try:
            fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fp
        except OSError:
            if time.time() >= deadline:
                # Give up gracefully — fall through without the lock.
                return fp
            time.sleep(0.2)


def launch_sidecar_chrome(
    port: int = DEFAULT_PORT,
    timeout_s: float = 30.0,
    headless: bool = True,
) -> bool:
    """Start the sidecar Chrome and wait for the CDP port to come up.

    Default ``headless=True`` runs the sidecar with --headless=new — no
    window pops up on the user's screen. The ariaSnapshot / locator API
    works identically; the only thing missing is the visible browser
    chrome the user can interact with by hand.

    Pass ``headless=False`` if you actually want a window (e.g. to
    log into a new site by hand inside the sidecar).

    Idempotent + concurrency-safe — if the port is already listening we
    return True without touching anything. If two callers race, only one
    actually launches; the other waits behind a flock and discovers the
    port live. Returns False on failure.
    """
    if is_port_listening(port):
        port_file().parent.mkdir(parents=True, exist_ok=True)
        port_file().write_text(str(port), encoding="utf-8")
        return True
    lock = _acquire_bootstrap_lock()
    try:
        # Re-check inside the lock — the other caller may have finished.
        if is_port_listening(port):
            port_file().parent.mkdir(parents=True, exist_ok=True)
            port_file().write_text(str(port), encoding="utf-8")
            return True

        chrome = chrome_binary()
        if chrome is None:
            return False
        if not ensure_sidecar_profile():
            return False
        sidecar = str(sidecar_dir())
        profile_dir = read_last_used_profile(sidecar)
        args = [
            chrome,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={sidecar}",
            f"--profile-directory={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        if headless:
            # `--headless=new` is Chrome's modern headless flag (since v109).
            # The legacy `--headless` runs a different, less-compatible mode.
            args.append("--headless=new")
        # Detach so the child outlives our Python process — agents come
        # and go but the sidecar stays.
        subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if is_port_listening(port):
                port_file().parent.mkdir(parents=True, exist_ok=True)
                port_file().write_text(str(port), encoding="utf-8")
                return True
            time.sleep(0.25)
        return False
    finally:
        try:
            lock.close()
        except Exception:
            pass


def cdp_url_if_available() -> Optional[str]:
    """Read the saved port file (or detect a running sidecar) and return
    the CDP URL ready for connect_over_cdp. None if nothing's up."""
    pf = port_file()
    if pf.exists():
        try:
            port = int(pf.read_text(encoding="utf-8").strip())
            if is_port_listening(port):
                return f"http://localhost:{port}"
        except (OSError, ValueError):
            pass
    # Fallback probe: maybe sidecar is running but file is missing.
    if is_port_listening(DEFAULT_PORT):
        try:
            port_file().parent.mkdir(parents=True, exist_ok=True)
            port_file().write_text(str(DEFAULT_PORT), encoding="utf-8")
        except OSError:
            pass
        return f"http://localhost:{DEFAULT_PORT}"
    return None
