"""The outside agents installed on this computer, their sign-in and models (``GET /api/harnesses``).

Read through a short-lived ``codex app-server`` as the Worker starts and kept;
a reading older than a few minutes is still answered and read again in the
background, so opening the Harness menu never waits on a process.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

_log = logging.getLogger(__name__)

#: How long one reading of a harness is reused.
CACHE_SECONDS = 300.0

_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()
_read_locks: dict[str, threading.Lock] = {}


def _codex_models(data: list[dict]) -> list[dict]:
    models = []
    for row in data or []:
        if not isinstance(row, dict) or row.get("hidden"):
            continue
        efforts = [
            str(level.get("reasoningEffort"))
            for level in row.get("supportedReasoningEfforts") or []
            if isinstance(level, dict) and level.get("reasoningEffort")
        ]
        models.append({
            "id": str(row.get("id") or row.get("model") or ""),
            "label": str(row.get("displayName") or row.get("id") or ""),
            "default": bool(row.get("isDefault")),
            "efforts": efforts,
            "default_effort": row.get("defaultReasoningEffort"),
            "images": "image" in (row.get("inputModalities") or ["text", "image"]),
            "personality": bool(row.get("supportsPersonality")),
        })
    return [model for model in models if model["id"]]


#: Codex's personalities (``personality`` on ``thread/start`` and ``turn/start``).
PERSONALITIES = (("pragmatic", "Pragmatic"), ("friendly", "Friendly"), ("none", "Plain"))


def _codex_agents(config: dict) -> list[dict]:
    """What Codex offers in place of an Agent: its own default, the profiles
    in its ``config.toml`` (``[profiles.<name>]``), and its personalities
    (only some models take one; ``models[].personality`` says which)."""
    agents: list[dict] = [{"id": "default", "label": "Default", "kind": "default", "default": True}]
    profiles = config.get("profiles") if isinstance(config.get("profiles"), dict) else {}
    agents += [{"id": f"profile:{name}", "label": str(name), "kind": "profile"} for name in sorted(profiles)]
    agents += [{"id": f"personality:{value}", "label": label, "kind": "personality"}
               for value, label in PERSONALITIES]
    return agents


def read_codex() -> dict:
    """What Codex on this computer is: installed, version, sign-in, plan and models."""
    from openprogram.runtime.harnesses.codex.client import AppServerError, binary_path, binary_version, start_app_server

    entry: dict[str, Any] = {
        "id": "codex", "provider": "codex-harness", "label": "Codex",
        "installed": False, "version": "", "signedIn": False, "models": [], "agents": [],
    }
    binary = binary_path()
    if binary is None:
        return entry
    entry["installed"] = True
    entry["version"] = binary_version(binary)
    try:
        server = start_app_server()
    except (AppServerError, OSError) as exc:
        _log.info("Codex could not be read: %s", exc)
        entry["error"] = "not_started"
        return entry
    try:
        try:
            account = (server.request("account/read", {}, timeout=15) or {}).get("account")
        except AppServerError:
            account = None
        if isinstance(account, dict):
            entry["signedIn"] = True
            entry["account"] = str(account.get("type") or "")
            if account.get("planType"):
                entry["plan"] = str(account["planType"])
        listing = server.request("model/list", {}, timeout=20) or {}
        entry["models"] = _codex_models(listing.get("data") or [])
        try:
            config = (server.request("config/read", {}, timeout=15) or {}).get("config") or {}
        except AppServerError:
            config = {}
        entry["agents"] = _codex_agents(config if isinstance(config, dict) else {})
        try:
            from openprogram.runtime.harnesses.codex.maintenance import update_info
            entry.update(update_info(binary, entry["version"], entry["models"]))
        except Exception:  # noqa: BLE001 - the listing stands without update details
            _log.debug("Codex update details could not be read", exc_info=True)
    except AppServerError as exc:
        _log.info("Codex models could not be read: %s", exc)
    finally:
        server.close()
    return entry


_READERS = {"codex": read_codex}


def list_harnesses(*, refresh: bool = False) -> list[dict]:
    """Every harness OpenProgram can drive, as this computer has it."""
    return [read_harness(harness_id, refresh=refresh) for harness_id in _READERS]


def read_harness(harness_id: str, *, refresh: bool = False) -> dict:
    """The harness as last read. An old reading is returned at once and read
    again in the background, so only the very first reading waits."""
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(harness_id)
        lock = _read_locks.setdefault(harness_id, threading.Lock())
    if cached and not refresh:
        if now - cached[0] >= CACHE_SECONDS and not lock.locked():
            threading.Thread(target=read_harness, args=(harness_id,), kwargs={"refresh": True},
                             daemon=True, name=f"harness-read-{harness_id}").start()
        return cached[1]
    with lock:
        with _cache_lock:
            cached = _cache.get(harness_id)
        if cached and (not refresh or time.monotonic() - cached[0] < 1.0):
            return cached[1]
        entry = _READERS[harness_id]()
        with _cache_lock:
            _cache[harness_id] = (time.monotonic(), entry)
        return entry


def harness_model(provider: str, model_id: Optional[str]) -> Optional[dict]:
    """The listed model ``model_id`` of the harness behind ``provider`` (cached reading only)."""
    for harness_id in _READERS:
        with _cache_lock:
            cached = _cache.get(harness_id)
        entry = cached[1] if cached else None
        if entry is None or entry.get("provider") != provider:
            continue
        for model in entry.get("models") or []:
            if model.get("id") == model_id:
                return model
    return None


def cached_harness(provider: str) -> Optional[dict]:
    """The kept reading of the harness behind ``provider``, without reading it."""
    with _cache_lock:
        for entry in (cached[1] for cached in _cache.values()):
            if entry.get("provider") == provider:
                return entry
    return None


def warm(provider: str) -> None:
    """Read the harness behind ``provider`` in the background."""
    for harness_id in _READERS:
        if harness_id == provider.removesuffix("-harness"):
            threading.Thread(target=read_harness, args=(harness_id,), daemon=True,
                             name=f"harness-read-{harness_id}").start()


def warm_all() -> None:
    """Read every harness in the background, as the Worker starts."""
    for harness_id in _READERS:
        threading.Thread(target=read_harness, args=(harness_id,), daemon=True,
                         name=f"harness-read-{harness_id}").start()


def update_harness(harness_id: str) -> dict:
    """Update the harness with the installer it came from, then read it again."""
    if harness_id != "codex":
        return {"ok": False, "error": "unknown_harness"}
    from openprogram.runtime.harnesses.codex.client import binary_path
    from openprogram.runtime.harnesses.codex.maintenance import run_update
    binary = binary_path()
    if binary is None:
        return {"ok": False, "error": "not_installed"}
    result = run_update(binary)
    result["harness"] = read_harness(harness_id, refresh=True)
    return result


def forget() -> None:
    with _cache_lock:
        _cache.clear()
