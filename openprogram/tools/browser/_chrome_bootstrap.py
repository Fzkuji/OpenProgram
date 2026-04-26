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
            data = json.loads(path.read_text())
            picked = data.get("profile", {}).get("last_used")
            if picked:
                return picked
        except (OSError, ValueError):
            pass
    return "Default"


def ensure_sidecar_profile() -> bool:
    """Copy real profile to sidecar if sidecar missing. Returns True if ready."""
    sidecar = sidecar_dir()
    if (sidecar / "Default").exists():
        return True
    src_root = real_user_data_dir()
    src_default = Path(src_root) / "Default"
    src_local_state = Path(src_root) / "Local State"
    if not src_default.exists():
        return False
    sidecar.mkdir(parents=True, exist_ok=True)
    # `cp -R` is faster than shutil.copytree for huge profiles on macOS.
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


def launch_sidecar_chrome(port: int = DEFAULT_PORT, timeout_s: float = 30.0) -> bool:
    """Start the sidecar Chrome and wait for the CDP port to come up.

    Idempotent — if the port is already listening we return True without
    touching anything. Returns False on failure.
    """
    if is_port_listening(port):
        port_file().parent.mkdir(parents=True, exist_ok=True)
        port_file().write_text(str(port))
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
    # Detach so the child outlives our Python process — agents come and
    # go but the sidecar stays.
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
            port_file().write_text(str(port))
            return True
        time.sleep(0.25)
    return False


def cdp_url_if_available() -> Optional[str]:
    """Read the saved port file (or detect a running sidecar) and return
    the CDP URL ready for connect_over_cdp. None if nothing's up."""
    pf = port_file()
    if pf.exists():
        try:
            port = int(pf.read_text().strip())
            if is_port_listening(port):
                return f"http://localhost:{port}"
        except (OSError, ValueError):
            pass
    # Fallback probe: maybe sidecar is running but file is missing.
    if is_port_listening(DEFAULT_PORT):
        try:
            port_file().parent.mkdir(parents=True, exist_ok=True)
            port_file().write_text(str(DEFAULT_PORT))
        except OSError:
            pass
        return f"http://localhost:{DEFAULT_PORT}"
    return None
