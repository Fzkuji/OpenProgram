"""Keeping the user's own Codex current, after T3 Code.

Codex lists only the models its own version can run. This module knows
which models Codex currently offers (``model-manifest.json``, bundled and
refreshed from the repository on GitHub), the newest Codex release, and how
the user's ``codex`` was installed, so the model menu can name the models
an update would bring and run the update with the same installer:

======================================  ===============================================
Where the ``codex`` file really lives    Update command
======================================  ===============================================
``.../node_modules/@openai/codex/``     ``npm``, ``bun`` or ``pnpm`` global add of
                                         ``@openai/codex@latest``
Homebrew ``Cellar`` / ``Caskroom``      ``brew upgrade [--cask] codex``
mise                                     ``mise upgrade codex``
Codex's standalone install              ``codex update``
anything else                            none: updated by hand
======================================  ===============================================
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

_log = logging.getLogger(__name__)

NPM_PACKAGE = "@openai/codex"
NPM_LATEST = "https://registry.npmjs.org/@openai/codex/latest"
NATIVE_LATEST = "https://releases.openai.com/codex/channels/latest"
MANIFEST_URL = ("https://raw.githubusercontent.com/Fzkuji/OpenProgram/main/"
                "openprogram/runtime/harnesses/codex/model-manifest.json")
#: How long the manifest and the latest version are reused.
REFRESH_SECONDS = 3600.0
#: Updates run for at most this long.
UPDATE_TIMEOUT_SECONDS = 600

_BUNDLED = Path(__file__).with_name("model-manifest.json")
_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = threading.Lock()
_update_lock = threading.Lock()


def version_key(version: str) -> tuple:
    """``0.171.0-alpha.13`` orders below ``0.171.0`` and above ``0.153.4``."""
    release, _, pre = (version or "").partition("-")
    numbers = tuple(int(part) if part.isdigit() else 0 for part in release.split(".") if part)
    pre_parts = tuple(int(p) if p.isdigit() else 0 for p in pre.split(".")[1:]) if pre else ()
    return (numbers, 0 if pre else 1, pre_parts)


def install_method(binary: str) -> Optional[dict]:
    """How ``binary`` was installed: ``{"kind", "argv", "env"}``, or ``None`` when unknown."""
    link = Path(binary)
    try:
        real = str(link.resolve()).replace("\\", "/")
    except OSError:
        return None
    lower = real.lower()
    env: dict[str, str] = {}
    if f"/node_modules/{NPM_PACKAGE}/" in real:
        spec = f"{NPM_PACKAGE}@latest"
        if "/.bun/" in lower:
            tool = _tool("bun", link.parent)
            return tool and {"kind": "bun", "argv": [tool, "add", "-g", spec], "env": env}
        if "pnpm" in lower:
            tool = _tool("pnpm", link.parent)
            return tool and {"kind": "pnpm", "argv": [tool, "add", "-g", spec], "env": env}
        tool = _tool("npm", link.parent)
        # npm is a node script: the bin folder beside it holds the node it needs.
        env["PATH"] = os.pathsep.join([str(link.parent), os.environ.get("PATH", "")])
        return tool and {"kind": "npm", "argv": [tool, "install", "-g", spec], "env": env}
    if "/cellar/codex/" in lower or "/caskroom/codex/" in lower:
        tool = _tool("brew", Path("/opt/homebrew/bin"), Path("/usr/local/bin"))
        cask = ["--cask"] if "/caskroom/" in lower else []
        return tool and {"kind": "homebrew", "argv": [tool, "upgrade", *cask, "codex"], "env": env}
    if "/mise/" in lower and "/mise/installs/node/" not in lower:
        tool = _tool("mise", Path.home() / ".local/bin")
        return tool and {"kind": "mise", "argv": [tool, "upgrade", "codex"], "env": env}
    standalone = re.match(r"^(.*)/packages/standalone/", real, re.IGNORECASE)
    if standalone:
        env["CODEX_HOME"] = standalone.group(1)
        return {"kind": "standalone", "argv": [str(link), "update"], "env": env}
    return None


def _tool(name: str, *folders: Path) -> Optional[str]:
    for folder in folders:
        candidate = folder / (f"{name}.cmd" if sys.platform == "win32" else name)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return shutil.which(name)


def _fetch_json(url: str) -> Optional[dict]:
    from openprogram.host.security import safe_http
    try:
        with safe_http.safe_client("harness.codex.release", timeout=10) as client:
            response = client.get(url, headers={"User-Agent": "openprogram",
                                                "Accept": "application/json"})
            if response.status_code != 200:
                return None
            payload = json.loads(response.content)
        return payload if isinstance(payload, dict) else None
    except Exception as exc:  # noqa: BLE001 - offline: the cached or bundled answer stands
        _log.info("Could not read %s: %s", url, exc)
        return None


def _cached(key: str, read):
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
    if hit and now - hit[0] < REFRESH_SECONDS:
        return hit[1]
    value = read()
    if value is None and hit:
        value = hit[1]
    with _cache_lock:
        _cache[key] = (now, value)
    return value


def latest_version(kind: Optional[str]) -> Optional[str]:
    """The newest Codex the installer of ``kind`` can reach."""
    if kind in ("npm", "bun", "pnpm"):
        def read():
            payload = _fetch_json(NPM_LATEST)
            return str(payload["version"]) if payload and payload.get("version") else None
        return _cached("npm", read)

    def read_native():
        payload = _fetch_json(NATIVE_LATEST)
        tag = str((payload or {}).get("tag_name") or "")
        match = re.fullmatch(r"rust-v(\d+\.\d+\.\d+)", tag)
        return match.group(1) if match else None
    return _cached("native", read_native)


def manifest_models() -> list[dict]:
    """The models Codex currently offers: the published manifest, else the bundled one."""
    def read():
        payload = _fetch_json(MANIFEST_URL)
        return payload if payload and isinstance(payload.get("models"), list) else None
    payload = _cached("manifest", read)
    if payload is None:
        try:
            payload = json.loads(_BUNDLED.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = {"models": []}
    return [m for m in payload.get("models") or []
            if isinstance(m, dict) and m.get("slug") and m.get("name")]


def update_info(binary: str, version: str, listed: list[dict]) -> dict:
    """What an update would bring: ``latest``, ``updatable`` and the ``newModels`` it unlocks."""
    method = install_method(binary)
    latest = latest_version(method["kind"] if method else None)
    if not latest or version_key(version) >= version_key(latest):
        return {}
    have = {m.get("id") for m in listed}
    new = [{"id": m["slug"], "label": m["name"]} for m in manifest_models()
           if m["slug"] not in have
           and version_key(version) < version_key(str(m.get("minVersion") or latest))]
    return {"latest": latest, "updatable": method is not None, "newModels": new}


def run_update(binary: str) -> dict:
    """Update the user's Codex with the installer it came from."""
    method = install_method(binary)
    if method is None:
        return {"ok": False, "error": "manual"}
    if not _update_lock.acquire(blocking=False):
        return {"ok": False, "error": "running"}
    try:
        from openprogram.host._platform.process_ownership import no_window_creation_flags
        out = subprocess.run(method["argv"], capture_output=True, text=True,
                             timeout=UPDATE_TIMEOUT_SECONDS, stdin=subprocess.DEVNULL,
                             env={**os.environ, **method["env"]},
                             creationflags=no_window_creation_flags())
        tail = ((out.stdout or "") + (out.stderr or "")).strip()[-2000:]
        if out.returncode != 0:
            _log.warning("Codex update failed (%s): %s", method["kind"], tail)
            return {"ok": False, "error": "failed", "kind": method["kind"], "output": tail}
        return {"ok": True, "kind": method["kind"]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": "failed", "kind": method["kind"], "output": str(exc)}
    finally:
        _update_lock.release()


def prompt_versions() -> list[str]:
    """The installed Codex's version, then the newest release: where the
    Codex app's model catalog takes Codex's base instructions from."""
    from openprogram.runtime.harnesses.codex.client import binary_path, binary_version
    binary = binary_path()
    versions = [binary_version(binary)] if binary else []
    method = install_method(binary) if binary else None
    latest = latest_version(method["kind"] if method else None)
    if latest and latest not in versions:
        versions.append(latest)
    return [v for v in versions if v]
