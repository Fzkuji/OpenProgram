"""Google's Gemini CLI in its ACP mode (``gemini --acp``): one process per conversation.

``gemini --acp`` is the Agent Client Protocol server Google documents for
editors (docs/cli/acp-mode.md in google-gemini/gemini-cli): newline-delimited
JSON-RPC 2.0 on stdio, the line protocol DeepSeek Harness's ACP server speaks,
so that client's ``AcpServer`` and the Codex client's process pool are reused.
Its sessions are saved with Gemini CLI's own (``~/.gemini/tmp/<project>/chats``)
and reopened with ``session/load``; ``session/new`` and ``session/load`` answer
with the models it offers (``models.availableModels``), and
``session/set_model`` picks one. Nothing else lists its models, so the listing
offers its own default until a turn has seen them, and keeps them since.

Sign-in, as its ACP server does it (packages/cli/src/acp/acpSessionManager.ts):
``session/new`` uses the auth type chosen in its settings
(``security.auth.selectedType``), else a Gemini API key; ``session/load``
refuses with "authentication required" unless an auth type is chosen. ACP's
``authenticate`` would choose one by writing it into the person's own
``~/.gemini/settings.json`` (clearing a cached Google login when it switches),
so it is not called. The person's own setup counts as theirs and is never
overridden: an auth type chosen in a settings file (whatever key or login it
keeps, a key saved by its ``/auth`` dialog too), or ``GEMINI_API_KEY`` in the
environment or ``~/.gemini/.env``. With a key but no auth type chosen, Gemini
CLI is started with a system-defaults file of OpenProgram's
(``GEMINI_CLI_SYSTEM_DEFAULTS_PATH``, the lowest settings layer, the existing
defaults kept) that chooses ``gemini-api-key``. With neither, the Google API
key set in OpenProgram's Model Services is handed to it as ``GEMINI_API_KEY``
the same way, billed to that key's account. No credential of Gemini CLI's is read.

Gemini CLI's terminal config can be put on OpenProgram's gateway
(``models/gateway/agents/gemini.py``: ``~/.gemini/.env`` and
``settings.json``). The chats here still run on Gemini CLI's own models and
sign-in: while ``.env`` names the gateway, a turn's process is given the
person's own base URL and key (the environment wins over ``.env``) and a
system-settings file of OpenProgram's (``GEMINI_CLI_SYSTEM_SETTINGS_PATH``,
the highest layer, the existing system settings kept) with their own auth
type and model, as the gateway's stash recorded them.
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
from typing import Optional

from openprogram.runtime.harnesses.codex.client import AppServerError, ProcessPool
from openprogram.runtime.harnesses.deepseek.client import AcpServer

_log = logging.getLogger(__name__)

#: The auth type Gemini CLI reads ``GEMINI_API_KEY`` for (its ``AuthType.USE_GEMINI``).
API_KEY_AUTH = "gemini-api-key"
#: How long a lookup that found no ``gemini`` is kept.
_MISSING_SECONDS = 300.0


# -- the program ------------------------------------------------------------

def _login_shell_which(name: str) -> Optional[str]:
    """``command -v name`` in the person's login shell: an app started from the Dock gets a short PATH."""
    shell = os.environ.get("SHELL")
    if sys.platform == "win32" or not shell:
        return None
    try:
        out = subprocess.run([shell, "-lic", f"command -v {name}"], capture_output=True, text=True,
                             timeout=10, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in reversed((out.stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("/") and os.access(line, os.X_OK):
            return line
    return None


_found: dict[str, tuple[Optional[str], float]] = {}


def _which(name: str) -> Optional[str]:
    """``name`` on PATH or in the login shell; a miss is looked for again after a while."""
    path, at = _found.get(name, (None, 0.0))
    if path and Path(path).is_file():
        return path
    if name in _found and path is None and time.monotonic() - at < _MISSING_SECONDS:
        return None
    path = shutil.which(name) or _login_shell_which(name)
    _found[name] = (path, time.monotonic())
    return path


def binary_path() -> Optional[str]:
    """The ``gemini`` the person installed: ``GEMINI_CLI_BINARY``, else ``gemini`` on PATH
    or in their login shell. No install place is assumed."""
    configured = os.environ.get("GEMINI_CLI_BINARY")
    if configured:
        return configured if Path(configured).is_file() else None
    return _which("gemini")


def _path_env(binary: str) -> dict[str, str]:
    """A PATH that finds ``node`` too: ``gemini`` is a Node script (``#!/usr/bin/env node``)."""
    folders = [str(Path(binary).parent)]
    node = shutil.which("node") or _which("node")
    if node:
        folders.append(str(Path(node).parent))
    current = os.environ.get("PATH", "")
    extra = [f for f in dict.fromkeys(folders) if f not in current.split(os.pathsep)]
    return {"PATH": os.pathsep.join([*extra, current])} if extra else {}


_versions: dict[tuple[str, float], str] = {}


def binary_version(binary: str) -> str:
    """The installed version, without starting it: the npm package's ``package.json``
    (``@google/gemini-cli``, beside the script the launcher links to), else
    ``gemini --version``; kept until the launcher changes."""
    try:
        real = Path(binary).resolve()
        key = (str(real), real.stat().st_mtime)
    except OSError:
        return ""
    if key in _versions:
        return _versions[key]
    version = ""
    for folder in real.parents:
        manifest = folder / "package.json"
        if manifest.is_file():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            if isinstance(data, dict) and data.get("name") == "@google/gemini-cli":
                version = str(data.get("version") or "")
            break
    if not version:
        try:
            out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=30,
                                 stdin=subprocess.DEVNULL, env={**os.environ, **_path_env(binary)}).stdout
            match = re.search(r"\d+\.\d+\.\d+\S*", out or "")
            version = match.group(0) if match else ""
        except (OSError, subprocess.SubprocessError):
            version = ""
    _versions[key] = version
    return version


# -- sign-in ----------------------------------------------------------------

def gemini_home() -> Path:
    """Gemini CLI's user folder: ``$GEMINI_CLI_HOME/.gemini``, else ``~/.gemini``."""
    return Path(os.environ.get("GEMINI_CLI_HOME") or Path.home()) / ".gemini"


def _system_folder() -> Path:
    if sys.platform == "darwin":
        return Path("/Library/Application Support/GeminiCli")
    if sys.platform == "win32":
        return Path("C:/ProgramData/gemini-cli")
    return Path("/etc/gemini-cli")


def _system_defaults_path() -> Path:
    configured = os.environ.get("GEMINI_CLI_SYSTEM_DEFAULTS_PATH")
    return Path(configured) if configured else _system_settings_path().parent / "system-defaults.json"


def _system_settings_path() -> Path:
    configured = os.environ.get("GEMINI_CLI_SYSTEM_SETTINGS_PATH")
    return Path(configured) if configured else _system_folder() / "settings.json"


def _read_settings(path: Path) -> dict:
    """A settings file (it may hold comments, which are left out here)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        try:
            data = json.loads(re.sub(r'^\s*//[^\n]*|/\*.*?\*/', "", text, flags=re.M | re.S))
        except ValueError:
            return {}
    return data if isinstance(data, dict) else {}


def _chosen(settings: dict) -> Optional[str]:
    security = settings.get("security")
    auth = security.get("auth") if isinstance(security, dict) else None
    chosen = auth.get("selectedType") if isinstance(auth, dict) else None
    return str(chosen) if chosen else None


#: Gemini CLI's own setup, by the keys OpenProgram's gateway wiring replaces
#: (``models/gateway/agents/gemini.py``).
_WIRED_KEYS = {"security.auth.selectedType": "auth", "model.name": "model",
               "GEMINI_API_KEY": "key", "GOOGLE_GEMINI_BASE_URL": "base"}
_GATEWAY_URL = re.compile(r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?/gateway/")


def gateway_own() -> Optional[dict[str, Optional[str]]]:
    """While Gemini CLI's terminal config is on an OpenProgram gateway (its
    ``~/.gemini/.env`` names one), what the person had before it, as
    ``{"auth", "model", "key", "base"}`` (None for what they hadn't or this
    instance didn't record); None while it isn't. OpenProgram's own chats keep
    running on Gemini CLI's own models and sign-in, not on the gateway."""
    try:
        from openprogram.models.gateway.edit import load
        text = (gemini_home() / ".env").read_text(encoding="utf-8")
        base = str(load("dotenv", text).get("GOOGLE_GEMINI_BASE_URL") or "")
    except Exception:  # noqa: BLE001 - no .env, or one that doesn't parse: not wired
        return None
    if not _GATEWAY_URL.match(base):
        return None
    own: dict[str, Optional[str]] = dict.fromkeys(_WIRED_KEYS.values())
    try:
        from openprogram.models.gateway import stash
        record = stash.entry("gemini") or {}
    except Exception:  # noqa: BLE001 - wired by another instance, or a damaged stash
        record = {}
    for kept in (record.get("files") or {}).values():
        for key, original in (kept.get("originals") or {}).items():
            value = original.get("value") if isinstance(original, dict) and original.get("had") else None
            if key in _WIRED_KEYS and isinstance(value, str) and value:
                own[_WIRED_KEYS[key]] = value
    return own


def selected_auth() -> Optional[str]:
    """The auth type chosen for Gemini CLI (``security.auth.selectedType``) in a settings
    layer it reads for every folder: system settings, the person's, system defaults.
    While its config is on OpenProgram's gateway, the person's is the one they had before."""
    own = gateway_own()
    for path in (_system_settings_path(), gemini_home() / "settings.json", _system_defaults_path()):
        if own is not None and path == gemini_home() / "settings.json":
            chosen = own["auth"]
        else:
            chosen = _chosen(_read_settings(path))
        if chosen:
            return chosen
    return None


def _env_key() -> bool:
    """Whether ``GEMINI_API_KEY`` is set for it: in the environment or in ``~/.gemini/.env``
    (the person's own one while that names OpenProgram's gateway)."""
    if os.environ.get("GEMINI_API_KEY"):
        return True
    own = gateway_own()
    if own is not None:
        return bool(own["key"])
    try:
        lines = (gemini_home() / ".env").read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    return any(re.match(r"^\s*(?:export\s+)?GEMINI_API_KEY\s*=\s*\S", line) for line in lines)


def own_sign_in() -> bool:
    """Whether the person set Gemini CLI up to sign in: an auth type they chose (a Google
    login, Vertex AI, or an API key, wherever it keeps it), or their own ``GEMINI_API_KEY``."""
    return selected_auth() is not None or _env_key()


def settings_key() -> Optional[str]:
    """The Google API key set in OpenProgram's Model Services, or None (never a sign-in token)."""
    try:
        from openprogram.models.auth.resolver import resolve_store_api_key_sync
        return resolve_store_api_key_sync("google") or None
    except Exception as exc:  # no traceback: it could carry key material
        _log.debug("no Google key for Gemini CLI (%s)", type(exc).__name__)
        return None


def sign_in_now() -> tuple[bool, Optional[str]]:
    """Whether Gemini CLI can sign in now, and the Model Services key to hand it, if any.

    The person's own setup is checked first and always wins over the key."""
    if own_sign_in():
        return True, None
    key = settings_key()
    return key is not None, key


def _state_dir() -> Path:
    try:
        from openprogram.state.paths import get_state_dir
        root = Path(get_state_dir())
    except Exception:  # noqa: BLE001
        import tempfile
        root = Path(tempfile.gettempdir())
    folder = root / "cache" / "gemini-cli"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _cache_file(name: str) -> Path:
    """A file of Gemini CLI's own in OpenProgram's cache (``cache/gemini-cli``), regenerated when lost."""
    return _state_dir() / name


def _api_key_defaults() -> str:
    """OpenProgram's system-defaults file: the existing defaults, with ``gemini-api-key`` chosen."""
    settings = _read_settings(_system_defaults_path())
    security = settings.get("security")
    if not isinstance(security, dict):
        security = settings["security"] = {}
    auth = security.get("auth")
    if not isinstance(auth, dict):
        auth = security["auth"] = {}
    auth.setdefault("selectedType", API_KEY_AUTH)
    path = _cache_file("system-defaults.json")
    text = json.dumps(settings, indent=2)
    if not path.is_file() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")
    return str(path)


def _own_settings(auth: str, model: Optional[str]) -> str:
    """OpenProgram's system-settings file (the highest settings layer, the existing
    system settings kept) that puts back the person's own auth type and model over
    the gateway's in their settings.json."""
    settings = _read_settings(_system_settings_path())
    security = settings.get("security")
    if not isinstance(security, dict):
        security = settings["security"] = {}
    chosen = security.get("auth")
    if not isinstance(chosen, dict):
        chosen = security["auth"] = {}
    chosen.setdefault("selectedType", auth)
    picked = settings.get("model")
    if not isinstance(picked, dict):
        picked = settings["model"] = {}
    # the gateway's provider/model means nothing to Google: the person's own, else Gemini CLI's "auto"
    picked.setdefault("name", model or "auto")
    path = _cache_file("system-settings.json")
    text = json.dumps(settings, indent=2)
    if not path.is_file() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")
    return str(path)


def _gateway_launch_env(own: dict[str, Optional[str]], key: Optional[str]) -> dict[str, str]:
    """A chat's Gemini CLI while its terminal config is on the gateway: the person's own
    base URL and key over the ``.env`` ones (Gemini CLI loads ``.env`` only for what the
    environment doesn't set; empty is none), and their own auth type and model."""
    api_key = os.environ.get("GEMINI_API_KEY") or own["key"] or key or ""
    env = {"GOOGLE_GEMINI_BASE_URL": os.environ.get("GOOGLE_GEMINI_BASE_URL") or own["base"] or "",
           "GEMINI_API_KEY": api_key}
    auth = (own["auth"] or _chosen(_read_settings(_system_defaults_path()))
            or (API_KEY_AUTH if api_key else None))
    if auth:
        env["GEMINI_CLI_SYSTEM_SETTINGS_PATH"] = _own_settings(auth, own["model"])
    return env


def launch_env(key: Optional[str]) -> dict[str, str]:
    """The environment a turn starts Gemini CLI with: nothing when an auth type is chosen;
    else a chosen ``gemini-api-key`` for its own key or for ``key``, given as ``GEMINI_API_KEY``.
    While its terminal config is on OpenProgram's gateway, the person's own setup."""
    own = gateway_own()
    if own is not None:
        return _gateway_launch_env(own, key)
    if selected_auth() is not None:
        return {}
    if not key and not _env_key():
        return {}
    env = {"GEMINI_CLI_SYSTEM_DEFAULTS_PATH": _api_key_defaults()}
    if key:
        env["GEMINI_API_KEY"] = key
    return env


# -- the ACP server ---------------------------------------------------------

class GeminiAcpServer(AcpServer):
    """``gemini --acp``."""

    label = "Gemini CLI"
    thread_name = "gemini-acp"
    version = ""

    def _initialized(self, answer: dict) -> None:
        self.version = str((answer.get("agentInfo") or {}).get("version") or "")


def start_acp_server(extra_args: Optional[list[str]] = None, env: Optional[dict[str, str]] = None) -> GeminiAcpServer:
    binary = binary_path()
    if binary is None:
        raise AppServerError("Gemini CLI is not installed on this computer.")
    server = GeminiAcpServer([binary, "--acp", *(extra_args or [])], env={**_path_env(binary), **(env or {})})
    try:
        server.start()
    except (AppServerError, OSError) as exc:
        tail = server.stderr_tail().splitlines()[-1:] if server.stderr_tail() else []
        server.kill()
        raise AppServerError(f"Gemini CLI did not start: {tail[0] if tail else exc}") from exc
    return server


_pool: Optional[ProcessPool] = None


def pool() -> ProcessPool:
    global _pool
    if _pool is None:
        # Closed when its turn ends, like the other harnesses: the person's own
        # ``gemini`` can then go on with the session, and no process idles signed in.
        _pool = ProcessPool(starter=start_acp_server, idle_seconds=0)
        import atexit
        atexit.register(_pool.close_all)
    return _pool


# -- models -----------------------------------------------------------------

#: What the listing offers before any turn has seen Gemini CLI's models: its own choice.
DEFAULT_MODEL = {"id": "default", "label": "Default", "default": True, "efforts": [], "default_effort": None,
                 "images": True, "personality": False}


def options_of(answer: Optional[dict]) -> list:
    """A session's models (``models`` in ``session/new``'s answer) as one ACP select option."""
    models = (answer or {}).get("models") if isinstance(answer, dict) else None
    if not isinstance(models, dict):
        return []
    rows = [{"value": str(m["modelId"]), "name": str(m.get("name") or m["modelId"])}
            for m in models.get("availableModels") or [] if isinstance(m, dict) and m.get("modelId")]
    if not rows:
        return []
    return [{"id": "model", "currentValue": models.get("currentModelId"), "options": rows}]


def model_id(value: str) -> str:
    return str(value)


def option_value(model: str) -> str:
    return model


def read_options(options: list) -> tuple[list[dict], list[str], Optional[str]]:
    """The models a session offers; Gemini CLI takes no effort level over ACP."""
    models: list[dict] = []
    for option in options or []:
        if isinstance(option, dict) and option.get("id") == "model":
            current = option.get("currentValue")
            for row in option.get("options") or []:
                if isinstance(row, dict) and row.get("value"):
                    models.append({**DEFAULT_MODEL, "id": str(row["value"]),
                                   "label": str(row.get("name") or row["value"]),
                                   "default": row["value"] == current})
    return models, [], None


_models_lock = threading.Lock()


def save_options(options: list) -> None:
    """Keep the models a turn's session offered, for the listing after a restart too."""
    if not options:
        return
    with _models_lock:
        try:
            _cache_file("models.json").write_text(json.dumps(options), encoding="utf-8")
        except OSError:
            _log.debug("Gemini CLI models not saved", exc_info=True)


def saved_options() -> list:
    with _models_lock:
        try:
            data = json.loads(_cache_file("models.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
    return data if isinstance(data, list) else []
