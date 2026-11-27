"""The outside agents installed on this computer, their sign-in and models (``GET /api/harnesses``).

Read through a short-lived ``codex app-server`` as the Worker starts and kept;
a reading older than a few minutes is still answered and read again in the
background, so opening the Harness menu never waits on a process.
"""
from __future__ import annotations

import logging
from pathlib import Path
import threading
import time
from typing import Any, Optional

_log = logging.getLogger(__name__)

#: How long one reading of a harness is reused.
CACHE_SECONDS = 300.0
#: How soon a reading that found no models is read again.
RETRY_SECONDS = 15.0

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


def _probe_dir() -> Path:
    try:
        from openprogram.state.paths import get_state_dir
        root = Path(get_state_dir())
    except Exception:  # noqa: BLE001
        import tempfile
        root = Path(tempfile.gettempdir())
    folder = root / "cache" / "deepseek-harness-probe"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _forget_dsh_session(session_id: str) -> None:
    """Remove a session the listing opened only to read its options."""
    import shutil
    from openprogram.runtime.harnesses.deepseek.client import dsh_home
    home = dsh_home()
    for folder in (home / "sessions").glob(f"*/{session_id}"):
        shutil.rmtree(folder, ignore_errors=True)
        try:
            folder.parent.rmdir()  # the probe folder's own, when it is empty
        except OSError:
            pass
    (home / "storages" / "session_projcache" / "sessions" / f"{session_id}.json").unlink(missing_ok=True)


def read_deepseek() -> dict:
    """What DeepSeek Harness on this computer is: installed, version, sign-in and models.

    Its models are what a session offers: the ones a turn here last saw, or
    else those of a session opened for the reading and removed again."""
    from openprogram.runtime.harnesses.deepseek import client

    entry: dict[str, Any] = {
        "id": "deepseek-harness", "provider": "deepseek-harness", "label": "DeepSeek Harness",
        "installed": False, "version": "", "signedIn": False, "models": [],
        # Its agent is its own; the Agent chip shows it as Default.
        "agents": [{"id": "default", "label": "Default", "kind": "default", "default": True}],
    }
    if client.binary_path() is None:
        return entry
    entry["installed"] = True
    entry["version"] = client.app_version()
    entry["signedIn"] = client.signed_in()
    options = _seen_options.get("deepseek-harness")
    if options is None:
        try:
            server = client.start_acp_server()
        except (client.AppServerError, OSError) as exc:
            _log.info("DeepSeek Harness could not be read: %s", exc)
            entry["error"] = "not_started"
            return entry
        session_id = None
        try:
            answer = server.request("session/new", {"cwd": str(_probe_dir()), "mcpServers": []}, timeout=60) or {}
            session_id = answer.get("sessionId")
            options = answer.get("configOptions") or []
            if session_id:
                try:
                    server.request("session/close", {"sessionId": session_id}, timeout=15)
                except client.AppServerError:
                    pass
        except client.AppServerError as exc:
            _log.info("DeepSeek Harness models could not be read: %s", exc)
            options = []
        finally:
            server.close()
            if session_id:
                _forget_dsh_session(session_id)
        if options:
            _seen_options["deepseek-harness"] = options  # the next reading opens no session
    entry["models"] = client.read_options(options)[0]
    return entry


#: The config options a live session of each harness last offered.
_seen_options: dict[str, list] = {}


def remember_options(harness_id: str, options: list) -> None:
    """Keep the models a session offered as the harness's listing."""
    if not options:
        return
    _seen_options[harness_id] = options
    with _cache_lock:
        cached = _cache.get(harness_id)
    if cached and harness_id in ("deepseek-harness", "gemini-cli"):
        if harness_id == "gemini-cli":
            from openprogram.runtime.harnesses.gemini_cli.client import read_options
        else:
            from openprogram.runtime.harnesses.deepseek.client import read_options
        entry = {**cached[1], "models": read_options(options)[0]}
        with _cache_lock:
            _cache[harness_id] = (cached[0], entry)


def read_gemini_cli() -> dict:
    """What Gemini CLI on this computer is: installed, version, sign-in and models.

    Read without starting it: the version from its npm package, the models a
    turn here last saw (kept across restarts), else its own default. Only a
    session lists its models, and opening one would write a project and a
    session into the person's ``~/.gemini`` and start their MCP servers."""
    from openprogram.runtime.harnesses.gemini_cli import client

    entry: dict[str, Any] = {
        "id": "gemini-cli", "provider": "gemini-cli-harness", "label": "Gemini CLI",
        "installed": False, "version": "", "signedIn": False, "models": [],
        "agents": [{"id": "default", "label": "Default", "kind": "default", "default": True}],
    }
    binary = client.binary_path()
    if binary is None:
        return entry
    entry["installed"] = True
    entry["version"] = client.binary_version(binary)
    entry["signedIn"] = client.sign_in_now()[0]
    options = _seen_options.get("gemini-cli") or client.saved_options()
    entry["models"] = client.read_options(options)[0] or [dict(client.DEFAULT_MODEL)]
    return entry


def read_claude_code() -> dict:
    """What Claude Code on this computer is: installed, version, sign-in, models and agents.

    Read from its ``initialize`` answer, which starts no turn (T3 Code merges
    the same answer's models into its own list)."""
    from openprogram.runtime.harnesses.claude_code import client

    entry: dict[str, Any] = {
        "id": "claude-code", "provider": "claude-code-harness", "label": "Claude Code",
        "installed": False, "version": "", "signedIn": False, "models": [], "agents": [],
    }
    binary = client.binary_path()
    if binary is None:
        return entry
    entry["installed"] = True
    entry["version"] = client.binary_version(binary)
    try:
        answer = client.read_listing(binary)
    except (client.ClaudeError, OSError) as exc:
        _log.info("Claude Code could not be read: %s", exc)
        entry["error"] = "not_started"
        return entry
    # Whether it is signed in, from the kind of sign-in it reports; no credential is read.
    # The Claude app hands its own Claude Code a sign-in that a separate start does not have;
    # without one, the Anthropic API key set in Model Services is handed to it.
    # Whether it may sign in is checked again at each reading of the entry (``read_harness``).
    entry["otherSignIn"] = client.own_sign_in(answer.get("account") or {}) and not client.saved_login()
    entry["models"] = [{
        "id": str(row.get("value")), "label": str(row.get("displayName") or row.get("value")),
        "default": row.get("value") == "default",
        "efforts": [str(level) for level in row.get("supportedEffortLevels") or []],
        "default_effort": None, "images": True, "personality": False,
    } for row in answer.get("models") or [] if isinstance(row, dict) and row.get("value")]
    agents = [{"id": "default", "label": "Default", "kind": "default", "default": True}]
    for agent in answer.get("agents") or []:
        name = agent.get("name") if isinstance(agent, dict) else agent
        if name:
            agents.append({"id": f"agent:{name}", "label": str(name), "kind": "agent"})
    entry["agents"] = agents
    return entry


_READERS = {"codex": read_codex, "claude-code": read_claude_code, "deepseek-harness": read_deepseek,
            "gemini-cli": read_gemini_cli}


def list_harnesses(*, refresh: bool = False) -> list[dict]:
    """Every harness OpenProgram can drive, as this computer has it."""
    return [read_harness(harness_id, refresh=refresh) for harness_id in _READERS]


def read_harness(harness_id: str, *, refresh: bool = False) -> dict:
    """The harness as last read, its sign-in as it is now."""
    entry = _read_harness(harness_id, refresh=refresh)
    if harness_id == "claude-code" and "otherSignIn" in entry:
        # A login or a Model Services key added or removed since shows at once.
        from openprogram.runtime.harnesses.claude_code import client
        signed_in, key = client.sign_in_now(bool(entry["otherSignIn"]))
        entry = {**entry, "signedIn": signed_in, "usesSettingsKey": key is not None}
    elif harness_id == "gemini-cli" and entry.get("installed"):
        from openprogram.runtime.harnesses.gemini_cli import client
        signed_in, key = client.sign_in_now()
        entry = {**entry, "signedIn": signed_in, "usesSettingsKey": key is not None}
    return entry


def _read_harness(harness_id: str, *, refresh: bool = False) -> dict:
    """The harness as last read. An old reading is returned at once and read
    again in the background, so only the very first reading waits."""
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(harness_id)
        lock = _read_locks.setdefault(harness_id, threading.Lock())
    if cached and not refresh:
        if now - cached[0] >= CACHE_SECONDS and not lock.locked():
            threading.Thread(target=_read_harness, args=(harness_id,), kwargs={"refresh": True},
                             daemon=True, name=f"harness-read-{harness_id}").start()
        return cached[1]
    with lock:
        with _cache_lock:
            cached = _cache.get(harness_id)
        if cached and (not refresh or time.monotonic() - cached[0] < 1.0):
            return cached[1]
        entry = _READERS[harness_id]()
        read_at = time.monotonic()
        if entry.get("installed") and not entry.get("models"):
            read_at -= CACHE_SECONDS - RETRY_SECONDS  # a reading that found no models is tried again soon
        with _cache_lock:
            _cache[harness_id] = (read_at, entry)
        return entry


def harness_model(provider: str, model_id: Optional[str]) -> Optional[dict]:
    """The listed model ``model_id`` of the harness behind ``provider`` (cached reading only)."""
    for harness_id in _READERS:
        with _cache_lock:
            cached = _cache.get(harness_id)
        entry = cached[1] if cached else None
        if entry is None or entry.get("provider") != provider:
            continue
        models = entry.get("models") or []
        for model in models:
            if model.get("id") == model_id:
                return model
        alias = listed_alias(models, model_id)
        if alias is not None:
            return alias
    return None


def listed_alias(models: list[dict], model_id: Optional[str]) -> Optional[dict]:
    """The listed model a full id belongs to: Claude Code lists ``opus``, a transcript says ``claude-opus-5-5``."""
    tokens = set(str(model_id or "").lower().replace("[", "-").replace("]", "-").split("-"))
    found = [m for m in models if str(m.get("id") or "").lower() in tokens]
    return found[0] if len(found) == 1 else None


def cached_harness(provider: str) -> Optional[dict]:
    """The kept reading of the harness behind ``provider``, without reading it."""
    with _cache_lock:
        for entry in (cached[1] for cached in _cache.values()):
            if entry.get("provider") == provider:
                return entry
    return None


def warm(provider: str) -> None:
    """Read the harness behind ``provider`` in the background."""
    from openprogram.runtime.harnesses import HARNESS_IDS

    def read_then_tell(harness_id: str) -> None:
        read_harness(harness_id)
        # A conversation asked before the reading sees its model's efforts now.
        try:
            from openprogram.runtime.events import emit_ws_frame
            emit_ws_frame({"type": "agent_settings_changed", "data": {}})
        except Exception:  # noqa: BLE001 - the next read of the settings has them
            pass

    for harness_id in _READERS:
        if harness_id == HARNESS_IDS.get(provider):
            threading.Thread(target=read_then_tell, args=(harness_id,), daemon=True,
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
