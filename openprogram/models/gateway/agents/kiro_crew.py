"""Kiro Crew (``$KIROCREW_HOME/config.json``, default ``~/.kiro/crew``).

After yetone/magpie ``internal/agent/kirocrew.go`` (MIT License, Copyright
(c) yetone). Kiro Crew (kirodotdev/KiroCrew: the KiroCrew.app desktop app,
the ``kirocrew`` CLI, its web dashboard and the Gateway they share) is a
persistent agent that runs each session on a harness: an ACP agent it starts
and talks to. It has no model provider of its own to point at the gateway;
its models are its harness's. So what OpenProgram sets is which harness it
runs: one already connected to OpenProgram, whose own config then sends its
requests to the gateway.

Its settings are one JSON file in its home, ``$KIROCREW_HOME`` (made
absolute; a root, ``/usr``, ``/System`` or ``/etc`` is refused and the
default used) or ``~/.kiro/crew``, the desktop app's too.
``config.local.json`` beside it is merged over it key by key and wins. The
keys::

    {"agent": {"acp_backend": "codex",          // "" is kiro-cli, the default
               "member_acp_backend": "codex"}}  // crew members', default "kas"

``member_acp_backend`` is set only to a harness that can dispatch crew
members. The Gateway reloads the files when they change; a session already
open keeps its harness.

The harnesses offered are the agents OpenProgram wires itself through the
files the harness reads under Crew too: Codex (codex-acp reads ``~/.codex``),
Claude Code (claude-agent-acp runs the user's claude), OpenCode, Pi and
Goose, as far as the registry has them. Not deepseek: Crew runs it as a
profile of its own. Codex, Claude Code and Pi are reached through an npm
adapter Crew doesn't ship; OpenProgram looks for it where Crew does and says
how to install it when it isn't there, and installs nothing.

Connecting keeps the harness Kiro Crew is set to when it is one of these,
and connects that agent too, on the model picked here, when it isn't yet;
disconnecting takes that agent off again only when this connect is what put
it on. On a harness OpenProgram doesn't wire (KAS, kiro-cli, deepseek) it
picks the first connected one, one whose adapter is here first. A harness
set in ``config.local.json`` is the user's: nothing is written over it.
"""
from __future__ import annotations

import glob
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from .base import Agent, App, Field, Wiring, Write, app_bundles, find_tool

BACKEND = "agent.acp_backend"
MEMBER = "agent.member_acp_backend"

APP = App("com.amazon.kiro.crew", ("KiroCrew.app", "Kiro Crew.app"))


@dataclass(frozen=True)
class Harness:
    """One harness OpenProgram can set: Crew's backend id, the registry agent
    that is it, and the adapter Crew runs it through, with the variable naming
    the adapter's path and the npm package it comes in (magpie ``kiroCrewHarness``)."""

    id: str
    agent: str
    name: str
    adapter: str = ""
    env: str = ""
    npm: str = ""
    dispatch: bool = False   # can run crew members (member_acp_backend)


#: The harnesses offered, in the order one is picked by itself.
HARNESSES = (
    Harness("codex", "codex", "Codex", "codex-acp", "CODEX_ACP_BIN", "@agentclientprotocol/codex-acp", True),
    Harness("claude", "claude_code", "Claude Code", "claude-agent-acp", "CLAUDE_AGENT_ACP_BIN",
            "@agentclientprotocol/claude-agent-acp", True),
    Harness("opencode", "opencode", "OpenCode", dispatch=True),
    Harness("pi", "pi", "Pi", "pi-acp", "PI_ACP_BIN", "pi-acp"),
    Harness("goose", "goose", "Goose", dispatch=True),
)

PINNED = ("Kiro Crew's config.local.json sets its harness (agent.acp_backend), and that wins over "
          "config.json, so OpenProgram leaves it alone: set it there to a harness connected to OpenProgram.")


def harness_of(backend: Any) -> Optional[Harness]:
    return next((h for h in HARNESSES if h.id == backend), None)


def _unsafe(path: Path) -> bool:
    """A home Kiro Crew refuses (``_is_unsafe_home``): a root, or a folder in
    /usr, /System or /etc (/private/etc, as a Mac resolves it)."""
    if path.parent == path:
        return True
    if sys.platform == "win32":
        return False
    text = str(path)
    return any(text == d or text.startswith(d + "/") for d in ("/usr", "/System", "/etc", "/private/etc"))


def home() -> Path:
    """Kiro Crew's home, as its ``config_dir`` finds it."""
    override = os.environ.get("KIROCREW_HOME")
    if override:
        try:
            path = Path(os.path.realpath(os.path.abspath(os.path.expanduser(override))))
            if not _unsafe(path):
                return path
        except (OSError, ValueError):
            pass
    return Path.home() / ".kiro" / "crew"


def config_path() -> Path:
    return home() / "config.json"


def local_path() -> Path:
    return home() / "config.local.json"


def _read(path: Path) -> dict[str, Any]:
    from ..edit import ConfigText, load
    text = ConfigText.read(path)
    return load("json", text.text, str(path)) if text.exists else {}


def _get(path: Path, key: str) -> tuple[bool, Any]:
    from ..edit import ConfigUnreadable, get_path
    try:
        return get_path(_read(path), key)
    except ConfigUnreadable:
        return False, None


def pinned() -> bool:
    return _get(local_path(), BACKEND)[0]


def backend() -> Any:
    """The harness it runs: config.local.json's, else config.json's."""
    present, value = _get(local_path(), BACKEND)
    return value if present else _get(config_path(), BACKEND)[1]


def _wired_check(agent_id: str) -> bool:
    from .. import stash
    from .engine import owned
    try:
        return stash.entry(agent_id) is not None and owned(agent_id)
    except Exception:  # noqa: BLE001 - an agent the registry lacks, or an unreadable stash
        return False


#: Whether a registry agent is connected to this OpenProgram; a module value for tests.
wired: Callable[[str], bool] = _wired_check


def connected() -> list[Harness]:
    return [h for h in HARNESSES if wired(h.agent)]


def finds(harness: Harness, user_home: Optional[Path] = None, getenv: Callable[[str], Optional[str]] = os.environ.get,
          windows: bool = sys.platform == "win32") -> bool:
    """Whether Crew would find the harness's adapter where it looks for it
    (``acp/launch.py``, ``env.py augmented_path``): the path its variable
    names, PATH, the folders Crew adds to its own, and every node version
    mise, asdf, nvm and fnm keep. A harness with no adapter is its own binary."""
    user_home = user_home or Path.home()
    binary = harness.adapter or harness.agent
    if harness.env:
        named = getenv(harness.env)
        if named and os.path.isfile(named):
            return True
    if find_tool(binary):
        return True
    mise = getenv("MISE_DATA_DIR") or (os.path.join(getenv("XDG_DATA_HOME"), "mise") if getenv("XDG_DATA_HOME")
                                       else str(user_home / ".local" / "share" / "mise"))
    dirs = [str(user_home / ".local" / "bin"), str(user_home / ".npm-packages" / "bin"),
            str(user_home / ".npm-global" / "bin"), os.path.join(mise, "shims"),
            str(user_home / ".volta" / "bin"), str(user_home / ".bun" / "bin")]
    bun = getenv("BUN_INSTALL")
    if bun and os.path.isabs(bun):
        dirs.append(os.path.join(bun, "bin"))
    if windows:
        dirs.append(os.path.join(getenv("APPDATA") or str(user_home / "AppData" / "Roaming"), "npm"))
    else:
        dirs += ["/opt/homebrew/bin", "/usr/local/bin"]
    for pattern in (os.path.join(mise, "installs", "node", "*", "bin"),
                    str(user_home / ".asdf" / "installs" / "nodejs" / "*" / "bin"),
                    os.path.join(getenv("NVM_DIR") or str(user_home / ".nvm"), "versions", "node", "*", "bin"),
                    os.path.join(getenv("FNM_DIR") or str(user_home / ".local" / "share" / "fnm"),
                                 "node-versions", "*", "installation", "bin"),
                    str(user_home / "Library" / "Application Support" / "fnm" / "node-versions" / "*" / "installation" / "bin")):
        dirs += glob.glob(pattern)
    # on Windows npm's shim is the name with .cmd, as Crew's shutil.which finds it
    names = [binary + ".cmd", binary + ".exe", binary + ".bat", binary] if windows else [binary]
    return any(os.path.isfile(os.path.join(folder, name)) for folder in dirs for name in names)


#: Whether a harness's adapter is here; a module value for tests.
has_adapter: Callable[[Harness], bool] = finds


def _join(agent_id: str, model: str) -> bool:
    """Connect a registry agent on ``model`` for Kiro Crew; False for one not
    here (magpie ``kiroCrewJoin``)."""
    from . import find
    from .engine import connect
    from .lifecycle import options_for
    agent = find(agent_id)
    if agent is None or not agent.installed():
        return False
    connect(agent.id, {"model": model}, options=options_for(agent.id))
    _harness_setting(agent, model)
    return True


def _harness_setting(agent: Agent, model: str) -> None:
    """A harness agent's OpenProgram settings follow its config (on, or off for ``""``)."""
    if not agent.harness:
        return
    from openprogram.state.settings import set_setting
    if model:
        set_setting(f"harnesses.{agent.id}.model", model)
    set_setting(f"harnesses.{agent.id}.terminal", bool(model))


#: The registry agent connected along with Kiro Crew; module values for tests.
join: Callable[[str, str], bool] = _join


def _leave(agent_id: str) -> None:
    """Take the agent connected along with Kiro Crew off again (magpie ``kiroCrewLeave``)."""
    from . import find
    from .engine import disconnect
    agent = find(agent_id)
    if agent is None or not wired(agent.id):
        return
    disconnect(agent.id)
    _harness_setting(agent, "")


leave: Callable[[str], None] = _leave


def _pick(at: Wiring) -> tuple[Harness, bool]:
    """The harness to run: the one asked for; else the one Kiro Crew is set
    to when it is one of OpenProgram's, connected now if it isn't yet
    (joined); else the first connected, one whose adapter is here first."""
    from .engine import AgentConfigError
    asked = at.values.get("harness", "")
    if asked:
        harness = harness_of(asked)
        if harness is None or not wired(harness.agent):
            raise AgentConfigError("that harness isn't connected to OpenProgram: connect it first")
        return harness, False
    current = harness_of(backend())
    if current is not None:
        if wired(current.agent):
            return current, False
        if join(current.agent, at.model):
            return current, True
    ready = connected()
    if not ready:
        raise AgentConfigError("Kiro Crew runs on a harness: connect Codex, Claude Code, OpenCode, Pi or Goose "
                               "to OpenProgram first, then connect Kiro Crew")
    return next((h for h in ready if has_adapter(h)), ready[0]), False


def _wire(at: Wiring) -> list[Write]:
    from .engine import AgentConfigError
    if pinned():
        raise AgentConfigError("Kiro Crew's config.local.json sets its harness, and that wins over config.json: "
                               "OpenProgram leaves it alone. Change agent.acp_backend there, or take it out, "
                               "then connect again")
    harness, joined = _pick(at)
    if joined:
        at.record["joined"] = harness.agent
    sets: dict[str, Any] = {BACKEND: harness.id}
    if harness.dispatch:
        sets[MEMBER] = harness.id
    return [Write(config_path(), "json", sets=sets)]


def _on_route(_folder: Path, connected_now: bool, record: dict[str, Any]) -> None:
    joined = record.get("joined")
    if not connected_now and isinstance(joined, str) and joined:
        leave(joined)


def _on() -> Optional[Harness]:
    """The harness it runs while OpenProgram set it and that harness is connected."""
    from .. import stash
    try:
        if stash.entry("kiro_crew") is None:
            return None
    except stash.StashUnreadable:
        return None
    harness = harness_of(backend())
    return harness if harness is not None and wired(harness.agent) else None


def _notice() -> str:
    from .. import stash
    harness = _on()
    if harness is None:
        return PINNED if pinned() else ""
    notes = ["Kiro Crew takes the new harness for new sessions; sessions already open keep theirs."]
    try:
        joined = (stash.entry("kiro_crew") or {}).get("joined")
    except stash.StashUnreadable:
        joined = None
    if joined == harness.agent:
        notes.append(f"Kiro Crew runs on {harness.name}, so OpenProgram connected {harness.name} too; "
                     "turning Kiro Crew off disconnects it again.")
    if harness.adapter and not has_adapter(harness):
        notes.append(f"Kiro Crew runs {harness.name} through {harness.adapter}, which OpenProgram didn't find here: "
                     f"install it with npm i -g {harness.npm}, then start a new session.")
    return " ".join(notes)


def _detect() -> bool:
    """Its app, or its command where its installer puts it; never ~/.kiro
    alone, which is Kiro's and kiro-cli's."""
    user = Path.home()
    venv = user / ".kirocrew-app" / ".venv"
    return bool(app_bundles(APP)) or (venv / "bin" / "kirocrew").is_file() or (
        sys.platform == "win32" and (venv / "Scripts" / "kirocrew.exe").is_file())


def _harness_options() -> list[tuple[str, str]]:
    return [(h.id, h.name if not h.adapter or has_adapter(h) else f"{h.name} (npm i -g {h.npm})")
            for h in connected()]


def agent() -> Agent:
    return Agent(
        id="kiro_crew", name="Kiro Crew", icon="kirocrew", aliases=("kirocrew", "kiro-crew", "crew"),
        kind="app", protocol="openai-responses", bin="kirocrew", path=config_path,
        fields=[Field("model", "Model"),
                Field("harness", "Harness", quiet=True, options=_harness_options)],
        wire=_wire, on_route=_on_route, notice=_notice, detect=_detect, before_copy=True,
    )
