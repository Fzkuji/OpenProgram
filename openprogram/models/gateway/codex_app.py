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

from openprogram.host._platform.filesystem import replace_file

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


def write_catalog(codex_home: Path, chosen: str, versions: list[str]) -> Optional[Path]:
    """The enabled models as Codex's catalog, ``chosen`` first; ``None`` without instructions."""
    from .conversion import enabled_model_refs
    instructions = base_instructions(versions)
    if not instructions:
        return None
    rows = enabled_model_refs()
    rows.sort(key=lambda row: row["id"] != chosen)
    if chosen not in {row["id"] for row in rows}:
        rows.insert(0, {"id": chosen, "name": chosen, "context_window": None})
    models = [_entry(row["id"], str(row.get("name") or row["id"]), row.get("context_window"), index, instructions,
                     row.get("thinking_levels"), row.get("default_thinking_level"))
              for index, row in enumerate(rows)]
    path = catalog_path(codex_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps({"models": models}, indent=1), encoding="utf-8")
    replace_file(temporary, path)
    return path


def remove_catalog(codex_home: Path) -> None:
    try:
        catalog_path(codex_home).unlink()
    except OSError:
        pass


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
                    return
                time.sleep(10)
            _log.info("Codex stayed busy; the Codex app takes the new config at its next launch")
        finally:
            with _restart_lock:
                _restart_pending = False

    threading.Thread(target=run, daemon=True, name="codex-app-restart").start()
