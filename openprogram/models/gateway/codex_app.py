"""The Codex app on OpenProgram's models, after magpie.

The Codex app (inside ChatGPT.app) runs its own ``codex app-server`` on the
same ``CODEX_HOME`` as the CLI, so "Also in the terminal" reaches it through
``config.toml`` too, with two gaps this module closes:

* Its model picker lists ``model/list``, which for a provider Codex does not
  ship shows Codex's own GPT models only. The gateway's models are written
  as a ``model_catalog_json`` file (``openprogram-models.json`` beside
  ``config.toml``), each entry what Codex itself uses for a model it has no
  metadata for (``model_info_from_slug``): the slug as its name, a 272K
  window unless OpenProgram knows the model's own, the model's OpenProgram
  thinking levels as its reasoning efforts, and Codex's base
  instructions (``codex-rs/models-manager/prompt.md`` of the installed
  Codex's release, read once and kept).
* Its app-server reads ``config.toml`` and the catalog only as it starts. When
  where Codex sends its requests changes (turned on or off), the app's own
  app-server is ended as a clean exit once no Codex session has been written
  for a while; the app starts another at once and keeps its windows and
  chats. A change of model alone restarts nothing: the app reads it at its
  next launch.
* It lists only the threads saved on the provider it uses now: the
  person's threads move to the gateway's provider with the switch, and
  back when it is turned off (``move_history``).
* A signed-in app (26.1021 on) can't always take the app-server that was
  ended: its own AppServerManager finds the account it cached cleared,
  logs ``fatal_error_broadcasted`` and shows "ChatGPT hit a snag" over the
  whole window till it is quit and opened again. For a minute after the end
  its logs are read for that line (:func:`app_crashed`); when it shows the
  page and no Codex turn ran since, the app is quit as its own Quit does and
  opened again, never forced (after magpie ``codex_app_crash.go`` and
  ``provider/codex_app_crash.go``). The switch the user turned is the ask
  for it: the page has no composer, and only a quit and open clears it.
"""
from __future__ import annotations

import json
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

_log = logging.getLogger(__name__)

CATALOG_NAME = "openprogram-models.json"
PROMPT_URL = "https://raw.githubusercontent.com/openai/codex/rust-v{version}/codex-rs/models-manager/prompt.md"
#: Codex's window for a model it has no metadata for.
FALLBACK_WINDOW = 272_000
#: No Codex session file written for this long counts as no turn running.
QUIET_SECONDS = 20.0
#: How long a restart waits for Codex to go quiet before it gives up.
RESTART_WAIT_SECONDS = 15 * 60

_restart_lock = threading.Lock()
_restart_pending = False


def catalog_path(codex_home: Path) -> Path:
    return codex_home / CATALOG_NAME


def _prompt_cache(version: str) -> Path:
    from openprogram.state.paths import get_state_dir
    return get_state_dir() / "cache" / f"codex-prompt-{version}.md"


def base_instructions(versions: list[str]) -> Optional[str]:
    """Codex's base instructions for an unknown model, of the first of ``versions``
    (the installed Codex's, then the newest release) whose release has them;
    the runtime names the versions (``maintenance.prompt_versions``)."""
    for version in versions:
        if not re.fullmatch(r"\d+\.\d+\.\d+", version or ""):
            continue
        cached = _prompt_cache(version)
        try:
            return cached.read_text(encoding="utf-8")
        except OSError:
            pass
        text = _fetch(PROMPT_URL.format(version=version))
        if text:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(text, encoding="utf-8")
            return text
    return None


def _fetch(url: str) -> Optional[str]:
    from openprogram.host.security import safe_http
    try:
        with safe_http.safe_client("harness.codex.release", timeout=15) as client:
            response = client.get(url, headers={"User-Agent": "openprogram"})
            return response.text if response.status_code == 200 and response.text.strip() else None
    except Exception as exc:  # noqa: BLE001 - offline: no catalog, Codex lists its own
        _log.info("Could not read %s: %s", url, exc)
        return None


_LEVEL_DESCRIPTIONS = {
    "minimal": "Minimal reasoning", "low": "Quick reasoning", "medium": "Balanced",
    "high": "Deep reasoning", "xhigh": "Extended effort", "max": "Maximum effort",
    "ultra": "Beyond maximum",
}


def _efforts(levels: Any, default: Any) -> tuple[list[dict[str, str]], Optional[str]]:
    """The model's OpenProgram thinking levels as Codex's effort presets and default."""
    levels = [str(level) for level in levels or [] if level and level != "off"]
    presets = [{"effort": level, "description": _LEVEL_DESCRIPTIONS.get(level, level)} for level in levels]
    if not levels:
        return [], None
    return presets, default if default in levels else levels[len(levels) // 2]


def _entry(slug: str, name: str, window: Optional[int], priority: int, instructions: str,
           levels: Any = None, default_level: Any = None) -> dict[str, Any]:
    window = int(window) if window else FALLBACK_WINDOW
    presets, default = _efforts(levels, default_level)
    return {
        "slug": slug, "display_name": name, "description": None,
        "default_reasoning_level": default, "supported_reasoning_levels": presets,
        "shell_type": "unified_exec", "visibility": "list", "supported_in_api": True,
        "priority": priority, "additional_speed_tiers": [], "service_tiers": [],
        "availability_nux": None, "upgrade": None,
        "model_messages": {"instructions_template": instructions},
        "include_skills_usage_instructions": False, "include_plugin_usage_instructions": False,
        "include_apps_usage_instructions": False,
        "supports_reasoning_summary_parameter": True, "default_reasoning_summary": "auto",
        "support_verbosity": False, "default_verbosity": None, "apply_patch_tool_type": None,
        "web_search_tool_type": "text", "truncation_policy": {"mode": "bytes", "limit": 10_000},
        "supports_image_detail_original": False,
        "context_window": window, "max_context_window": window,
        "effective_context_window_percent": 95, "experimental_supported_tools": [],
        "input_modalities": ["text", "image"], "supports_search_tool": False,
        "supports_experimental_context": False, "use_responses_lite": False,
        "supports_reasoning_effort_updates": False,
        "node_repl_auto_review_required": False, "node_repl_disabled": False,
    }


def catalog_text(chosen: str, versions: list[str], rows: list[dict[str, Any]]) -> Optional[str]:
    """The enabled models (``rows``) as Codex's catalog, ``chosen`` first;
    ``None`` without instructions. The agent registry writes it beside
    ``config.toml`` and removes it on disconnect (``agents/codex.py``)."""
    instructions = base_instructions(versions)
    if not instructions:
        return None
    rows = sorted(rows, key=lambda row: row["id"] != chosen)
    if chosen not in {row["id"] for row in rows}:
        rows.insert(0, {"id": chosen, "name": chosen, "context_window": None})
    models = [_entry(row["id"], str(row.get("name") or row["id"]), row.get("context_window"), index, instructions,
                     row.get("thinking_levels"), row.get("default_thinking_level"))
              for index, row in enumerate(rows)]
    return json.dumps({"models": models}, indent=1)


def move_history(codex_home: Path, to_provider: str, from_provider: str) -> int:
    """Keep the person's threads in the app's history across the switch.

    Codex lists only the threads saved on the provider it uses now, so
    turning "Also in the terminal" on would hide every earlier chat, and
    turning it off every chat made through the gateway; their provider
    moves with the switch (magpie's bring-over and bring-back)."""
    from openprogram.state.store.session.external_import.codex_threads import move_provider
    moved = move_provider(codex_home, to_provider, from_provider=from_provider)
    if moved:
        _log.info("%d Codex thread(s) moved from %s to %s", moved, from_provider, to_provider)
    return moved


# The app's own app-server

def is_app_own_server(args: str) -> bool:
    """A Codex app's bundled ``codex app-server`` it talks to over stdio.

    The program lives inside an app bundle (``…/X.app/Contents/…/codex``);
    one started with ``--listen`` is another program's and is left alone.
    """
    match = re.match(r"^(.*?/codex(?:\.exe)?)(?:\s+(.*))?$", args.strip())
    if not match or ".app/Contents/" not in match.group(1):
        return False
    words = (match.group(2) or "").split()
    if "app-server" not in words:
        return False
    following = words[words.index("app-server") + 1:]
    if following and not following[0].startswith("-"):
        return False
    return not any(w == "--listen" or w.startswith("--listen=") for w in words)


def _home_of(pid: int) -> Optional[str]:
    """The Codex home process ``pid`` runs on (its CODEX_HOME, else HOME/.codex)."""
    try:
        out = subprocess.run(["ps", "eww", "-o", "command=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    env = dict(re.findall(r"(?:^|\s)(CODEX_HOME|HOME)=(\S+)", out))
    if env.get("CODEX_HOME"):
        return env["CODEX_HOME"]
    return str(Path(env["HOME"]) / ".codex") if env.get("HOME") else None


def app_servers(codex_home: Path) -> list[int]:
    """The pids of the Codex app's own app-servers on ``codex_home`` (macOS and Linux)."""
    if sys.platform == "win32":
        return []
    try:
        out = subprocess.run(["ps", "-axo", "pid=,args="], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    pids = []
    for line in out.splitlines():
        pid, _, args = line.strip().partition(" ")
        if pid.isdigit() and is_app_own_server(args):
            home = _home_of(int(pid))
            if home and Path(home).expanduser().resolve() == codex_home.expanduser().resolve():
                pids.append(int(pid))
    return pids


def sessions_quiet(codex_home: Path, now: Optional[float] = None, quiet: float = QUIET_SECONDS) -> bool:
    """No Codex session file written in the last ``quiet`` seconds: no turn is running."""
    now = time.time() if now is None else now
    sessions = codex_home / "sessions"
    if not sessions.is_dir():
        return True
    newest = 0.0
    for folder, _dirs, files in os.walk(sessions):
        for name in files:
            if name.endswith(".jsonl"):
                try:
                    newest = max(newest, os.path.getmtime(os.path.join(folder, name)))
                except OSError:
                    continue
    return now - newest >= quiet


def restart_app(codex_home: Path) -> None:
    """End the Codex app's app-server once Codex is quiet, so the app starts one on the new config."""
    global _restart_pending
    with _restart_lock:
        if _restart_pending:
            return
        _restart_pending = True

    def run() -> None:
        global _restart_pending
        deadline = time.monotonic() + RESTART_WAIT_SECONDS
        try:
            while time.monotonic() < deadline:
                pids = app_servers(codex_home)
                if not pids:
                    return
                if sessions_quiet(codex_home):
                    for pid in pids:
                        try:
                            os.kill(pid, signal.SIGTERM)  # the app reads exit 0 as expected and restarts it
                        except OSError:
                            pass
                    _log.info("Codex app's app-server ended so it reads the new config: %s", pids)
                    watch_crash(codex_home, time.time())
                    return
                time.sleep(10)
            _log.info("Codex stayed busy; the Codex app takes the new config at its next launch")
        finally:
            with _restart_lock:
                _restart_pending = False

    threading.Thread(target=run, daemon=True, name="codex-app-restart").start()


# The app's error page after its app-server ended (magpie codex_app_crash.go)

#: How long after an end the app's logs are read for its error page.
CRASH_WAIT = 60.0
CRASH_POLL = 5.0
#: How many of a log root's newest day folders are read.
LOG_DAYS = 7
#: How much of the end of a log file is read.
LOG_TAIL = 512 << 10
#: What the app's AppServerConnection logs as it shows the page.
FATAL_LINE = " [AppServerConnection] fatal_error_broadcasted"
APP_BUNDLE_ID = "com.openai.codex"


def log_roots() -> list[Path]:
    """The folders the Codex app writes its logs in here (its file logger:
    macOS ``~/Library/Logs/<bundle id>``, Windows ``%LOCALAPPDATA%\\Codex\\Logs``
    and the Store package's, Linux ``$XDG_STATE_HOME/codex/logs``)."""
    user = Path(os.environ.get("HOME") or Path.home())
    if sys.platform == "darwin":
        return [user / "Library" / "Logs" / APP_BUNDLE_ID]
    if sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA") or user / "AppData" / "Local")
        return [local / "Codex" / "Logs",
                local / "Packages" / "OpenAI.Codex_2p2nqsd0c76g0" / "LocalCache" / "Local" / "Codex" / "Logs"]
    state = Path(os.environ.get("XDG_STATE_HOME") or user / ".local" / "state")
    return [state / "codex" / "logs"]


def _digit_dirs(folder: Path) -> list[Path]:
    try:
        return [Path(e.path) for e in os.scandir(folder) if e.is_dir() and e.name.isdigit()]
    except OSError:
        return []


def _log_files(root: Path, since: float) -> tuple[list[Path], bool]:
    """The ``.log`` files under ``root``'s newest day folders written to at or
    after ``since``; ``False`` when ``root`` can't be read. A file's own time
    counts, never its folder's (Windows stamps a file only as it is closed)."""
    try:
        os.listdir(root)
    except FileNotFoundError:
        return [], True
    except OSError:
        return [], False
    days = sorted(str(d) for y in _digit_dirs(root) for m in _digit_dirs(y) for d in _digit_dirs(m))[-LOG_DAYS:]
    files = []
    for day in days:
        try:
            entries = list(os.scandir(day))
        except OSError:
            continue
        for entry in entries:
            if entry.is_file() and entry.name.endswith(".log"):
                try:
                    if os.lstat(entry.path).st_mtime >= since:
                        files.append(Path(entry.path))
                except OSError:
                    continue
    return files, True


def _stamp(text: str) -> Optional[float]:
    from datetime import datetime
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _fatal_in(path: Path, since: float) -> Optional[float]:
    """The time of the first page logged in ``path`` at or after ``since``, in
    its last :data:`LOG_TAIL` bytes; a line still being written is left out."""
    try:
        with open(path, "rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            offset = max(size - LOG_TAIL, 0)
            handle.seek(offset)
            data = handle.read()
    except OSError:
        return None
    if offset > 0:
        cut = data.find(b"\n")
        if cut < 0:
            return None
        data = data[cut + 1:]
    end = data.rfind(b"\n")
    if end < 0:
        return None
    for line in data[:end + 1].decode("utf-8", "replace").splitlines():
        if FATAL_LINE not in line:
            continue
        at = _stamp(line.split(" ", 1)[0])
        if at is not None and at >= since:
            return at
    return None


def app_crashed(since: float) -> tuple[Optional[float], bool]:
    """When the Codex app first logged its error page at or after ``since``
    (less a second for the clock's grain), and whether its logs could be read
    (magpie ``codexAppCrashed``)."""
    since -= 1
    first: Optional[float] = None
    known = False
    for root in log_roots():
        files, readable = _log_files(root, since)
        known = known or readable
        for path in files:
            at = _fatal_in(path, since)
            if at is not None and (first is None or at < first):
                first = at
    return first, known


def _newest_session(codex_home: Path) -> float:
    newest = 0.0
    for folder, _dirs, files in os.walk(codex_home / "sessions"):
        for name in files:
            if name.endswith(".jsonl"):
                try:
                    newest = max(newest, os.path.getmtime(os.path.join(folder, name)))
                except OSError:
                    continue
    return newest


def relaunch_app() -> str:
    """Quit the Codex app as its own Quit does and open it again (macOS);
    ``""`` when it did, else why not."""
    from types import SimpleNamespace
    from .agents.codex import CODEX_APP
    from .agents.running import restart_app as reopen
    app = SimpleNamespace(id="codex-app", name="The Codex app", app=CODEX_APP)
    return reopen(app, time.time() + 2)


def watch_crash(codex_home: Path, since: float, wait: float = CRASH_WAIT, poll: float = CRASH_POLL) -> bool:
    """Read the app's logs for its error page for ``wait`` seconds after an
    end at ``since``; reopen the app that shows it while no turn ran since.
    Whether it was reopened."""
    if sys.platform != "darwin":
        return False  # OpenProgram reopens the macOS app only
    deadline = time.monotonic() + wait
    while True:
        at, _known = app_crashed(since)
        if at is not None:
            if _newest_session(codex_home) > at:
                _log.info("the Codex app showed its error page after its app-server ended, and a turn ran since")
                return False
            problem = relaunch_app()
            if problem:
                _log.info("the Codex app showed its error page after its app-server ended: %s", problem)
                return False
            _log.info("the Codex app showed its error page after its app-server ended; quit it and opened it again")
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll)
