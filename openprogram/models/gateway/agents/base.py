"""What an outside agent is to OpenProgram: a description, not a process driver.

After yetone/magpie ``internal/agent/agent.go`` (MIT License, Copyright (c)
yetone). An :class:`Agent` says where the agent's config lives, how it is
found on this computer, which fields OpenProgram sets (its model, its tiers),
what it writes into the agent's own files to put it on the gateway
(:attr:`Agent.wire`), which of those writes are the model list kept in step
with the catalog (``Write.listing``), how its requests are told apart at the
gateway (:attr:`Agent.ua`, a key of its own, a path of its own) and what is
worth saying after a change (:attr:`Agent.notice`, a running app to restart).
The engine (``engine.py``) does every write, the stash of what was there
before, drift and putting it all back; an agent describes, it never writes.
"""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class Field:
    """One setting OpenProgram sets in an agent (magpie ``Field``).

    ``quiet`` fields are left out while empty: they follow ``follows`` until
    set (Claude Code's per-tier models follow its model). A field with
    ``options`` (``() -> [(value, label)]``) is a choice, not a model: it is
    picked from those, and a model leaving the catalog never moves it
    (magpie ``Field.Options`` of a non-model field: Codex's "applies to",
    Kiro Crew's harness)."""

    key: str
    label: str
    quiet: bool = False
    follows: str = ""
    options: Optional[Callable[[], list[tuple[str, str]]]] = None
    kept: bool = False
    """A choice that is the user's own setting, not wiring (magpie
    ``Field.Own``: agy's effort and fast tools): kept by agent outside the
    stash (``kept_picks.py``), so it outlives disconnecting and comes back
    with the next connect. It is set connected or not (:func:`engine.set_kept`)."""
    multi: bool = False
    """A choice of several of its options at once, one per line (magpie's
    multi-pick: Kimi Code's subagent pool); the Models card shows it as a
    menu of checks."""
    with_catalog: bool = False
    """The model field's ``options`` are the agent's own models offered beside
    OpenProgram's, not in their place (magpie's ``Direct`` options: DeepSeek
    Harness's DeepSeek models): its pick is either, and a reseat moves only a
    pick of OpenProgram's."""


#: The ``Write.members`` key of a file that is itself one JSON list.
ROOT = "$"


@dataclass(frozen=True)
class Members:
    """The entries OpenProgram owns in a list it shares with the user's own
    entries (magpie's ``qwenCustoms`` ``ours``, ``copilotFile``, Droid's
    ``customModels``): an entry is OpenProgram's when every field of
    ``match`` holds (a value, or ``{"prefix": p}`` for a string beginning
    with ``p``; field ``""`` is the entry itself). Connecting writes the
    user's entries as they are, in their order, and ``items`` after them
    (before them with ``first``, or where OpenProgram's were with
    ``keep_place``, as magpie's Copilot and WorkBuddy lists do); disconnecting
    takes out the entries that match and leaves the user's, the key going
    with the last of them. ``{"in": [...]}`` matches any of the values (the
    ids of OpenProgram's models in Copilot for Obsidian's enabled lists).

    ``loose`` are fields of an entry the agent's own settings change (a
    timeout, an on/off, a key it encrypts): they are written, but a changed
    one is neither drift nor a sign another OpenProgram took the file over.
    A list that is not ``checked`` is never drift (one the user curates).

    ``kept`` are fields the user's value of wins (magpie ``droidKept``): an
    entry is paired with OpenProgram's earlier one by ``key``, and a kept
    field whose value in the file is not what OpenProgram last wrote there
    (recorded in the stash) is the user's and stays, taken out included;
    fields OpenProgram doesn't write stay too, except ``drop``. ``derive``
    then works out fields that follow a kept one (Droid's ``baseUrl`` from
    its ``provider``). Kept fields are loose."""

    match: dict[str, Any]
    items: list[Any]
    first: bool = False
    keep_place: bool = False
    loose: tuple[str, ...] = ()
    checked: bool = True
    key: str = ""
    kept: tuple[str, ...] = ()
    drop: tuple[str, ...] = ()
    derive: Optional[Callable[[dict[str, Any]], dict[str, Any]]] = None


@dataclass(frozen=True)
class Pick:
    """Keys of a pick the agent's own picker rewrites as one (Pi's
    ``defaultProvider`` and ``defaultModel`` from its /model, Aside's
    ``defaultModel`` object, an omp role): written, but a change there is
    neither drift nor a sign another OpenProgram took the file over.
    Disconnecting puts back every one of them while the key ``on`` still
    says OpenProgram's (``want``: a value, or ``{"prefix": p}`` /
    ``{"in": [...]}`` as in :class:`Members`), and leaves them all once it
    names the user's own, so a pair never comes back half (magpie's
    Disconnect resetting a field still on magpie, as a pair)."""

    keys: tuple[str, ...]
    on: str
    want: Any


@dataclass
class Write:
    """One file's part of an agent's wiring.

    ``format`` is one of ``edit.FORMATS``, or ``"file"`` for a file
    OpenProgram writes whole (``content``) and removes again on disconnect
    (Codex's model catalog). ``sets`` and ``deletes`` are dotted key paths;
    their originals are stashed and put back. ``keep`` are dotted keys
    written but never restored (Codex's provider table, which threads started
    on it still name). ``listing`` marks the agent's copy of the model list,
    rewritten alone when the catalog changes (magpie ``Sync``). ``members``
    are lists, by dotted key (:data:`ROOT` for a file that is one JSON list),
    holding OpenProgram's entries beside the user's (:class:`Members`).

    ``owns`` are keys of ``sets`` holding an object that is OpenProgram's
    whole (its provider entry, magpie ``DelJSON`` of its own entry): what the
    user added inside it is kept while connected, a field the user changed is
    drift, and disconnecting takes the whole object out (or puts back the
    user's own of that name), edited or not. ``loose`` are dotted keys (in
    ``sets``, ``keep`` or inside an owned object) written but not checked:
    the agent rewrites them itself (QwenPaw encrypts its key). A file with
    :data:`ROOT` in ``owns`` is OpenProgram's whole: it goes on disconnect
    (or its copy from before comes back) even when edited since (Snow's
    profile of OpenProgram's, QwenPaw's provider file)."""

    path: Path
    format: str
    sets: dict[str, Any] = field(default_factory=dict)
    deletes: list[str] = field(default_factory=list)
    keep: dict[str, Any] = field(default_factory=dict)
    content: Optional[str] = None
    listing: bool = False
    members: dict[str, Members] = field(default_factory=dict)
    owns: tuple[str, ...] = ()
    loose: tuple[str, ...] = ()
    picks: tuple[Pick, ...] = ()
    base: Optional[str] = None
    """The text a file that isn't there is taken to hold, as the agent itself
    would have made it (omp's models.yml from an older models.json): not
    OpenProgram's, so nothing of it is recorded as written or taken out
    again."""
    shared: bool = False
    """A file several agents write alike (the provider file every QwenPaw
    agent names): the first to connect stashes what was there before, the
    others take that record, and it is put back only when the last agent
    whose stash names the file disconnects."""


@dataclass
class Wiring:
    """What :attr:`Agent.wire` is given: the picks and where the gateway is."""

    agent: "Agent"
    values: dict[str, str]
    worker: str                      # http://127.0.0.1:<port>
    key: str                         # the key this agent sends
    models: list[dict[str, Any]]     # the enabled models (conversion.enabled_model_refs)
    options: dict[str, Any] = field(default_factory=dict)
    previous: dict[str, Any] = field(default_factory=dict)
    """The agent's stash record before this connect, ``{}`` when it was off."""
    syncing: bool = False
    """A sync of the model list (magpie ``Sync``), not a connect."""
    record: dict[str, Any] = field(default_factory=dict)
    """Facts of the agent's own :attr:`Agent.wire` keeps in its stash record
    after a connect (Kiro Crew's harness it connected along with it), and
    after a sync of its model list (what Pi's ``enabledModels`` lost to a
    model no longer given, read back from :attr:`previous`)."""

    def base(self, protocol: str) -> str:
        """The gateway's base URL for ``protocol`` as this agent is given it."""
        from ..address import PROTOCOL_PATHS
        root = self.worker + "/gateway"
        if self.agent.path_keyed:
            # the agent's own key in the path: an agent that sends a bearer
            # of its own it can't be told to change (Claude Science's
            # Claude sign-in) is let in by its address (magpie's WSL relay
            # carries its key the same way, claudescience_wsl.go)
            from ..keys import path_key
            root += f"/agent/{path_key(self.key)}"
        elif self.agent.path_named:
            root += f"/agent/{self.agent.id}"
        return root + PROTOCOL_PATHS[protocol]

    @property
    def model(self) -> str:
        return self.values.get("model", "")

    @property
    def same_pick(self) -> bool:
        """A connect again on the model the agent is connected with already
        (``lifecycle.rewire`` after a moved port or a new key): an app that
        keeps its own pick is given only the address and key, and a model
        picked in the app since stays."""
        return bool(self.previous) and bool(self.model) and (
            (self.previous.get("values") or {}).get("model") == self.model)


@dataclass(frozen=True)
class App:
    """A desktop app of the agent's that reads its config only as it starts
    (magpie ``restartable``): its macOS bundle id and the names it is
    installed under. Where one app runs as two (Claude Desktop beside its
    copy on a folder of its own), ``instance`` says which running command
    line is the one OpenProgram wired, and ``reopen``, given that run,
    restarts it its own way (``None``: the usual Quit and open)."""

    bundle_id: str
    names: tuple[str, ...]
    instance: Optional[Callable[[str], bool]] = None
    reopen: Optional[Callable[[Any], Optional[str]]] = None


class AppGone(Exception):
    """Raised by :attr:`Live.disconnect` when the app is uninstalled and so
    can't be reached: its data is kept, which only the app itself changes.
    The engine turns the agent off and keeps ``kept`` in the stash, and once
    the app is installed and answers again the hook runs again to take
    OpenProgram out (magpie's ``alma.takeout`` and ``shelley.takeout``)."""


@dataclass
class Live:
    """The wiring of an agent whose settings OpenProgram cannot write as a
    file edit: a running app's own local API (Alma, Kun, Shelley …), a
    database its daemon owns (Dim), the user's environment (Cursor Private
    Inference) or a credential store (Zed's key). After magpie's per-field
    ``Set``/``Get`` with ``Unwire``: each hook is given what it kept the
    last time (``kept``, ``{}`` before the first connect) and returns what
    to keep now, which the engine stores in the agent's stash record and
    hands back to :attr:`disconnect`. ``kept`` is OpenProgram's only memory
    of what the agent had before, so a hook keeps there whatever it must put
    back. A hook that cannot do its part raises ``engine.AgentConfigError``;
    the engine then writes no file and records nothing new.

    ``connect`` runs before the agent's file writes (if any) on every
    connect and re-apply (a moved port, a reseat), so it must leave alone
    what it already holds: a non-empty ``kept`` means the agent is already
    OpenProgram's. ``sync`` brings a list of models the agent keeps up to the
    catalog (magpie ``Sync``); ``check`` names what of the wiring is gone
    (magpie ``Check``), shown as drift."""

    connect: Callable[["Wiring", dict[str, Any]], dict[str, Any]]
    disconnect: Callable[[dict[str, Any]], None]
    sync: Optional[Callable[["Wiring", dict[str, Any]], dict[str, Any]]] = None
    check: Optional[Callable[["Wiring", dict[str, Any]], list[str]]] = None
    start: Optional[Callable[["Wiring", dict[str, Any]], None]] = None
    """Run as the Worker starts while the agent is connected: what the
    system forgets at a restart is set again (launchd's variables, magpie
    ``KeepCursorLocalEnv``)."""


@dataclass
class Agent:
    """One outside agent (magpie ``Agent``)."""

    id: str
    name: str
    icon: str
    path: Callable[[], Path]                     # the config file OpenProgram edits
    wire: Callable[[Wiring], list[Write]]        # what connecting with ``values`` writes
    fields: list[Field] = field(default_factory=lambda: [Field("model", "Model")])
    kind: str = "cli"                            # cli | app | ide | bot
    protocol: str = "openai-chat"                # what it speaks to the gateway
    aliases: tuple[str, ...] = ()
    bin: str = ""                                # its command, for detection
    dir: Optional[Callable[[], Path]] = None     # its config folder, for detection
    ua: tuple[str, ...] = ()                     # User-Agent prefixes, lower case
    harness: bool = False                        # OpenProgram also runs it in its own chats
    named_key: bool = True                       # given token_for(id); every agent is (engine.agent_key)
    key_of: str = ""
    """The agent whose key this one is given, where both write one provider
    in one shared file (OpenChamber names OpenCode's): one key there."""
    path_named: bool = False                     # given /gateway/agent/<id>/… (magpie AgentPath)
    path_keyed: bool = False                     # given /gateway/agent/<id>.<mac>/…: its key in the path
    before_copy: bool = False                    # copy its files whole before the first write
    notice: Optional[Callable[[], str]] = None   # advice after a change
    app: Optional[App] = None                    # a desktop app to offer a restart of
    held_at: Callable[[], list[Path]] = lambda: []  # where its vendor installs it off PATH
    detect: Optional[Callable[[], bool]] = None  # its own sign it is here, beside the others (magpie ``detect``)
    loose: tuple[str, ...] = ()
    """Dotted keys the agent's app itself rewrites or drops as it runs (Claude
    Desktop drops Claude-3p's ``deploymentMode`` at start): written and put
    back like any other, but not drift, and not a sign another instance owns
    the files. :attr:`check` says when they matter."""
    check: Optional[Callable[[], str]] = None
    """What keeps a connected agent off the gateway, as its app reads its
    files, ``""`` for nothing (magpie ``Agent.Check``)."""
    keep: Optional[Callable[[], None]] = None    # run every few seconds while connected (magpie's keepers)
    unwire: Optional[Callable[[dict[str, Any]], None]] = None
    """Run first on disconnect with the stash record: takes the agent's own
    entries out of a list its app may have rewritten since (magpie
    ``Unwire``: NewMax's provider, Paseo's models), before the stashed values
    go back where they still read as written. Raises ``ConfigUnreadable``
    for a file it cannot change."""
    detect_only: bool = False
    """:attr:`detect` is the whole answer, in place of its file, folder,
    command and app (magpie's ``detect`` does replace them): a folder shared
    with another program (every Copilot's, Qoder's plugin and app), or an
    agent that is only a plugin."""
    sign: Optional[Callable[[], bool]] = None
    """Something besides its command that says it is installed (magpie
    ``sign``: Qoder's app status, CodeBuddy's IDE); it is then never "CLI gone"."""
    bin_not: Optional[Callable[[str], bool]] = None
    """Whether a command found for ``bin`` is another program's (magpie
    ``binNot``: VS Code's ``copilot`` launcher, another ``ocr``)."""
    shared_used: Optional[Callable[[Path], bool]] = None
    """Whether one of the agent's own still uses a shared file (``Write.shared``)
    that no connected agent's stash names any more (magpie
    ``qwenpawNamesMagpie``: a QwenPaw agent switched to OpenProgram's provider
    in QwenPaw itself): the last disconnect then leaves the file."""
    unwired: Optional[Callable[[dict[str, Any]], None]] = None
    """Run on disconnect once the stashed values are back: what the agent's own
    lists still name of OpenProgram's goes (magpie ``piScopeWithout``: Pi's
    ``enabledModels`` entries on a provider no longer there)."""
    on_route: Optional[Callable[[Path, bool, dict[str, Any]], None]] = None
    """Run after the agent is connected (True) or disconnected (False): the
    Codex app's thread move and app-server restart."""
    after: Optional[Callable[[bool, dict[str, Any]], None]] = None
    """Run after every change to its files: written (True, by a connect or a
    sync that changed them, or a connect that changed its picks) or put back
    (False), with its stash record: a running app given the change live
    (Aside's ``aside repl``), agy's fast-tools plugin."""
    apply_kept: Optional[Callable[[dict[str, str]], None]] = None
    """Carry out the agent's kept choices (``Field.kept``), as they are now,
    whether it is connected or not (magpie ``Field.Own``'s ``Set``: agy's
    fast-tools plugin written or taken out)."""
    launch: Optional[Callable[["Wiring"], str]] = None
    """The command that starts it on the gateway, for an agent that takes
    the gateway only from how it is started (magpie ``Agent.Launch``: agy)."""
    root_paths: tuple[str, ...] = ()
    """Paths it asks for at the root of its endpoint's host, where the key in
    its address can't reach (Muse Code's ``/muse-code/models``, magpie
    ``museModels``): served as its own path, and let in with no key only
    from this computer, from no web page, while it is connected
    (owner_auth ``_rooted_agent``)."""

    @property
    def gateway(self) -> bool:
        """False for an agent with only models of its own (magpie's own-models
        rows: Cursor CLI, Devin): its model field is a choice among them, its
        picks never the gateway's, and nothing keeps them in step with
        OpenProgram's models."""
        model = self.field("model")
        return model is None or model.options is None or model.with_catalog
    live: Optional[Live] = None                  # wiring that is not a file edit (above)
    link: Optional[Callable[["Wiring"], str]] = None
    """For an app that takes the gateway only through an import link of its
    own, which the user confirms in the app (Cindy, AionUi, Cherry Studio,
    Lody): that link. Such an agent has no fields; its row offers the link
    (magpie ``Import``)."""
    added: Optional[Callable[["Wiring"], tuple[bool, bool]]] = None
    """Whether the app has the gateway already, read and never written:
    ``(added, known)``, ``known`` False when that could not be read (magpie
    ``Added``). ``None`` for an app that shows no way to tell."""
    open_link: Optional[Callable[[str, Callable[[str], None]], None]] = None
    """How the link is handed over when the system's opener is not the way
    (Lody on Linux, run with the link as its desktop entry says): given the
    link and the checked opener (``applink.open_app_link``) to fall back on."""

    def config_dir(self) -> Optional[Path]:
        return self.dir() if self.dir else None

    @property
    def switch(self) -> bool:
        """It is turned on and off, with no model of its own to pick: no
        ``model`` field (Cursor Private Inference lists the gateway's models
        itself), and no import link."""
        return self.link is None and self.field("model") is None

    def field(self, key: str) -> Optional[Field]:
        lowered = key.lower()
        return next((f for f in self.fields if f.key.lower() == lowered or f.label.lower() == lowered), None)

    # Detection (magpie Detected, agentDir, binAt; gone.go)

    def bin_path(self) -> str:
        found = find_tool(self.bin) if self.bin else ""
        return "" if found and self.bin_not is not None and self.bin_not(found) else found

    def installed(self) -> bool:
        """The agent seems to be here: its config file, its folder, or its command."""
        if self.detect is not None and self.detect_only:
            return self.detect()
        if self.sign is not None and self.sign():
            return True
        folder = self.config_dir()
        if folder is not None and taken(folder):
            return False
        try:
            if self.path().is_file():
                return True
        except OSError:
            pass
        if folder is not None and agent_dir(folder):
            return True
        if self.bin and self.bin_path():
            return True
        if self.detect is not None and self.detect():
            return True
        return self.app is not None and bool(app_bundles(self.app))

    def cli_gone(self) -> bool:
        """Its folder is left but nothing here runs it any more (magpie ``cliHeld``)."""
        if not self.bin or not self.installed():
            return False
        if self.bin_path():
            return False
        if self.app is not None and app_bundles(self.app):
            return False
        if self.sign is not None and self.sign():
            return False
        return not any(path.exists() for path in self.held_at())


# Detection helpers

#: Folders an agent writes as it runs, not as it is set up: a folder holding
#: only these (and skills) is no sign it is here (magpie ``leftover``).
LEFTOVER = frozenset({"logs", "log", "debug", "diagnostics", "cache"})


def agent_dir(path: Path, *own: str) -> bool:
    """Whether ``path`` is a folder an agent itself made (magpie ``agentDir``)."""
    try:
        entries = list(os.scandir(path))
    except OSError:
        return path.is_dir()
    if not entries:
        return True
    for entry in entries:
        name = entry.name
        if name in ("skills", ".DS_Store"):
            continue
        if entry.is_dir() and name.lower() in LEFTOVER:
            continue
        if not entry.is_dir() and name in own:
            continue
        return True
    return False


def taken(path: Path) -> bool:
    """Something that isn't a folder is where ``path``, or a folder it is in, would be."""
    current = Path(path)
    while True:
        if current.exists():
            return not current.is_dir()
        if current.parent == current:
            return False
        current = current.parent


def _tool_dirs() -> list[Path]:
    home = Path.home()
    dirs = [home / ".local" / "bin", home / ".npm-global" / "bin", home / ".bun" / "bin",
            home / ".volta" / "bin", home / "Library" / "pnpm", home / ".cargo" / "bin",
            Path("/opt/homebrew/bin"), Path("/usr/local/bin")]
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            dirs.append(Path(appdata) / "npm")
    return dirs


def picked(agent_id: str) -> dict[str, str]:
    """A connected agent's picks, ``{}`` while it is off: what a choice's
    options that depend on them read (Devin's effort, on its model)."""
    from .. import stash
    try:
        return dict((stash.entry(agent_id) or {}).get("values") or {})
    except stash.StashUnreadable:
        return {}


def find_tool(name: str) -> str:
    """Where command ``name`` is: on PATH, else where users' tools are installed
    (magpie ``binAt`` / ``proc.FindTool``) — a Worker started from the desktop
    app has no login shell's PATH."""
    found = shutil.which(name)
    if found:
        return found
    for folder in _tool_dirs():
        candidate = folder / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return ""


def app_bundles(app: App) -> list[Path]:
    """The installed bundles of a macOS app whose Info.plist names its id."""
    if sys.platform != "darwin":
        return []
    import plistlib
    found = []
    for root in (Path("/Applications"), Path.home() / "Applications"):
        for name in app.names:
            bundle = root / name
            try:
                with open(bundle / "Contents" / "Info.plist", "rb") as handle:
                    info = plistlib.load(handle)
            except (OSError, ValueError, plistlib.InvalidFileException):
                continue
            if info.get("CFBundleIdentifier") == app.bundle_id and info.get("CFBundleExecutable"):
                found.append(bundle)
    return found


def mac_app_removed(*apps: App) -> bool:
    """The desktop app is gone from this Mac: no bundle of any of its builds
    (magpie ``macAppRemoved``). An app found by its data folder alone leaves
    the folder behind when it is uninstalled. Never said elsewhere: where
    such apps install on Windows and Linux is not checked."""
    return sys.platform == "darwin" and not any(app_bundles(app) for app in apps)


def user_config_dir() -> Path:
    """The OS's folder for apps' settings (Go's ``os.UserConfigDir``):
    ~/Library/Application Support, %APPDATA%, $XDG_CONFIG_HOME or ~/.config."""
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return Path(xdg) if xdg and os.path.isabs(xdg) else home / ".config"
