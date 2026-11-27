"""Cursor Private Inference, the build of Cursor whose agent runs on this machine.

After yetone/magpie ``internal/agent/cursorlocal.go``,
``cursorlocal_env_*.go`` and ``cursorlocal_installs_windows.go`` (MIT
License, Copyright (c) yetone). The build is installed as Cursor is (the same
app name, bundle id and ~/.cursor) and tells itself apart only by its
product.json (``"nameShort": "Cursor Private Inference"``). It takes its
model endpoint from a model's own settings, else its Open configuration
dialog's (kept in the state.vscdb regular Cursor shares), else its
environment: ``CURSOR_LOCAL_AGENT_BASE_URL`` and ``CURSOR_LOCAL_AGENT_API_KEY``.
Nothing of Cursor's is written: the Open configuration's base URL and key
are regular Cursor's own override settings too. The two variables, which
only this build reads, are set for the user instead (``userenv.py``), so an
app opened from the Dock or the Start menu has them; :func:`launch_command`
starts it with them from a shell. It lists the gateway's models itself, so
its row is a switch, with no model to pick. The list it reads with its own
key gives each model ``api_types`` and ``capabilities``
(``models_info.cursor_local_model``): it asks a model on the API listed, so a
Claude served on Anthropic Messages is asked there and keeps its thinking.

Its row's effort (magpie's per-agent effort), kept for the agent, is the
level the gateway asks a model that reasons for when a request names none.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from . import userenv
from ..agent_effort import effort_options
from .base import Agent, Field, Live, Wiring

ID = "cursor_local"
NAME = "Cursor Private Inference"
VARS = ["CURSOR_LOCAL_AGENT_BASE_URL", "CURSOR_LOCAL_AGENT_API_KEY"]
ENV_FILE = "openprogram-cursor-local"

_seen_lock = threading.Lock()
_seen: dict[str, Any] = {"app": "", "at": 0.0}


def roots() -> list[Path]:
    """The folders apps are installed in on this system; on Linux the build
    is an AppImage kept wherever it was downloaded, so those folders too."""
    home = Path.home()
    if sys.platform == "darwin":
        return [Path("/Applications"), home / "Applications"]
    if sys.platform == "win32":
        out = []
        for folder in (os.environ.get("LOCALAPPDATA"), os.environ.get("ProgramFiles")):
            if folder:
                out += [Path(folder) / "Programs", Path(folder)]
        return out
    return [Path("/opt"), Path("/usr/share"), Path("/usr/lib"), home / ".local" / "share", home / "Applications",
            home / ".local" / "bin", home / "bin", home / "Downloads", home]


def _product(resources: Path) -> Optional[dict[str, Any]]:
    try:
        product = json.loads((resources / "product.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(product, dict) and str(product.get("nameShort") or "").strip().lower() == NAME.lower():
        return product
    return None


def _mac_exe(product: dict[str, Any]) -> str:
    name = product.get("applicationName") or ""
    return (name[:1].upper() + name[1:]) if name else "Cursor"


def _plist_executable(path: Path) -> str:
    try:
        found = re.search(r"<key>CFBundleExecutable</key>\s*<string>([^<]+)</string>",
                          path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return ""
    return found.group(1).strip() if found else ""


def app_image(name: str) -> bool:
    """A file's name is the build's AppImage as downloaded
    (Cursor_Private_Inference-3.24.9-x86_64.AppImage), which regular Cursor's isn't."""
    lowered = name.lower()
    if not lowered.endswith(".appimage"):
        return False
    return lowered.replace("_", "").replace("-", "").replace(" ", "").startswith("cursorprivateinference")


def app_at(folder: Path) -> str:
    """The program of the build installed at ``folder``, ``""`` when it isn't it."""
    if folder.name.endswith(".app"):
        product = _product(folder / "Contents" / "Resources" / "app")
        if product is None:
            return ""
        exe = _plist_executable(folder / "Contents" / "Info.plist") or _mac_exe(product)
        return str(folder / "Contents" / "MacOS" / exe)
    if app_image(folder.name) and folder.is_file():
        return str(folder)
    product = _product(folder / "resources" / "app")
    if product is None:
        return ""
    exe = product.get("applicationName") or "Cursor"
    if sys.platform == "win32":
        exe = f"{product.get('nameShort')}.exe"
        if not (folder / exe).is_file():
            exe = _mac_exe(product) + ".exe"
    elif sys.platform == "darwin":
        exe = _mac_exe(product)
    return str(folder / exe)


def installs() -> list[Path]:
    """The folders Windows' list of installed programs says the build is in:
    its installer takes any folder the user picks."""
    if sys.platform != "win32":
        return []
    import winreg  # type: ignore[import-not-found]
    found = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for root in (r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
                     r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"):
            try:
                key = winreg.OpenKey(hive, root)
            except OSError:
                continue
            with key:
                for index in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        with winreg.OpenKey(key, winreg.EnumKey(key, index)) as entry:
                            display = str(winreg.QueryValueEx(entry, "DisplayName")[0])
                            if not display.lower().startswith(NAME.lower()):
                                continue
                            try:
                                folder = str(winreg.QueryValueEx(entry, "InstallLocation")[0])
                            except OSError:
                                folder = ""
                            if not folder:
                                icon = str(winreg.QueryValueEx(entry, "DisplayIcon")[0]).rsplit(",", 1)[0]
                                folder = os.path.dirname(icon.strip('"')) if icon else ""
                    except OSError:
                        continue
                    if os.path.isabs(folder):
                        found.append(Path(folder))
    return found


def find_app(where: list[Path], more: tuple[Path, ...] | list[Path] = ()) -> str:
    for root in where:
        try:
            entries = sorted(os.scandir(root), key=lambda e: e.name)
        except OSError:
            continue
        for entry in entries:
            exe = app_at(Path(entry.path))
            if exe:
                return exe
    for folder in more:
        exe = app_at(folder)
        if exe:
            return exe
    return ""


def installed_app() -> str:
    """Where the build is installed, looked for again after half a minute."""
    with _seen_lock:
        if time.monotonic() - _seen["at"] > 30 or not _seen["at"]:
            _seen["app"], _seen["at"] = find_app(roots(), installs()), time.monotonic()
        return _seen["app"]


def launch_command(app: str, base: str, key: str) -> str:
    """The command that starts the build at ``app`` on the gateway, in this system's shell."""
    if sys.platform == "win32":
        quoted = app.replace("'", "''")
        return f'$env:{VARS[0]}="{base}"; $env:{VARS[1]}="{key}"; & \'{quoted}\''
    quoted = app.replace("'", "'\\''")
    return f"{VARS[0]}={base} {VARS[1]}={key} '{quoted}'"


def mark_path() -> Path:
    """OpenProgram's note that the variables are set for the user."""
    from ..keys import gateway_dir
    return gateway_dir() / "cursor-local.env"


def env_for(at: Wiring) -> dict[str, str]:
    return {VARS[0]: at.base("openai-chat"), VARS[1]: at.key}


def _on(env: dict[str, str]) -> None:
    from .engine import AgentConfigError
    try:
        userenv.set_user_env(VARS, env, file=ENV_FILE)
    except userenv.EnvError as exc:
        raise AgentConfigError(str(exc)) from exc
    from ..edit import write_bytes_atomic
    # a marker only: the address, never the key, which lives in the user's
    # environment alone
    write_bytes_atomic(mark_path(), f"{VARS[0]}={env[VARS[0]]}\n".encode("utf-8"), keep_mode=False, mode=0o600)


def _connect(at: Wiring, kept: dict[str, Any]) -> dict[str, Any]:
    _on(env_for(at))
    return {"env": [at.base("openai-chat")]}


def _disconnect(kept: dict[str, Any]) -> None:
    from .engine import AgentConfigError
    try:
        userenv.set_user_env(VARS, None, file=ENV_FILE)
    except userenv.EnvError as exc:
        raise AgentConfigError(str(exc)) from exc
    mark_path().unlink(missing_ok=True)


def _check(at: Wiring, kept: dict[str, Any]) -> list[str]:
    return [] if mark_path().is_file() else [VARS[0]]


def _start(at: Wiring, kept: dict[str, Any]) -> None:
    """Set the variables again as the Worker starts, while the build is
    connected: launchd forgets them at a restart (magpie ``KeepCursorLocalEnv``)."""
    if mark_path().is_file():
        _on(env_for(at))


def _notice() -> str:
    return (f"{NAME} reads OpenProgram's gateway from {VARS[0]} and {VARS[1]}, now set for your user: quit it "
            "(the app, not only its window) and open it again, as it keeps the model list it was first given "
            "until it quits. A base URL or API key set in its Open configuration or a model's settings comes "
            "first, so leave both empty.")


def agent() -> Agent:
    return Agent(
        id=ID, name=NAME, icon="cursor", aliases=("cursor-local", "cursor-private-inference"), kind="app",
        protocol="openai-chat", path=mark_path, wire=lambda at: [],
        # how hard its models reason, which its environment can't say: the
        # gateway asks its requests for it (agent_effort, magpie's effort field)
        fields=[Field("effort", "Effort", quiet=True, options=lambda: effort_options(ID), kept=True)],
        detect_only=True, detect=lambda: bool(installed_app()), notice=_notice,
        live=Live(connect=_connect, disconnect=_disconnect, check=_check, start=_start),
    )
