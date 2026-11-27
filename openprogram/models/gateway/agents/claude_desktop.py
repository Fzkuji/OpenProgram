"""Claude Desktop in its third-party ("3p") gateway mode.

After yetone/magpie ``internal/agent/claudedesktop.go``,
``claudedesktop_gateway.go``, ``claudedesktop_models.go`` and
``internal/desktopdir`` (MIT License, Copyright (c) yetone). Desktop runs on
a third-party gateway in its 3p deployment mode, Anthropic's own deployment
for an inference gateway: no Anthropic sign-in, its Code and Cowork tabs on
the gateway's models. The mode is ``deploymentMode`` in
``claude_desktop_config.json``, in Desktop's own folder and in ``Claude-3p``
beside it; the gateway is a profile in ``Claude-3p/configLibrary``, the one
``_meta.json`` applies::

    Claude/claude_desktop_config.json      {"deploymentMode":"3p",…}
    Claude-3p/claude_desktop_config.json   {"deploymentMode":"3p",…}
    Claude-3p/configLibrary/<id>.json      {"inferenceProvider":"gateway",
      "inferenceGatewayBaseUrl":"http://127.0.0.1:<port>/gateway/anthropic",
      "inferenceGatewayApiKey":"<this agent's own gateway key>",
      "inferenceGatewayAuthScheme":"bearer",
      "disableDeploymentModeChooser":true,
      "claudeAiImport":{…},"toolSearchEnabled":true,"modelPrefer1mContext":true}
    Claude-3p/configLibrary/_meta.json     {"appliedId":"<id>",
      "entries":[…,{"id":"<id>","name":"OpenProgram"}]}

Desktop reads all this at start-up only. The policies after the key are
seeded once: a value the user sets in Desktop's window, or takes away, stays
theirs. The MCP servers and language of Desktop's signed-in profile are
carried into Claude-3p, its userData in 3p mode. With no ``inferenceModels``
Desktop lists the gateway's ``/v1/models``, which the gateway gives it by ids
it keeps (``gateway/desktop.py``); while a model is picked for a tier the
profile lists them, each pick with the Claude model it behaves as.

Desktop's own sign-in is never read or passed on: the profile carries an
OpenProgram gateway key, and the gateway answers with OpenProgram's own
provider keys only.

Not ported: magpie's isolated Desktop (a second app bundle of its own),
Desktop pointed at a LAN https address for SSH remote sessions (the
gateway is on loopback only), the MSIX package's second set of folders on
Windows, and writing the Code tab's Claude Code settings (``claude.go``
``desktopOn``).
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

from .. import edit
from ..edit import ConfigText, ConfigUnreadable, get_path
from .base import Agent, App, Field, Wiring, Write

#: OpenProgram's profile in Desktop's configLibrary: a UUID like the ones
#: Desktop makes, its last part "openpr" in hex.
PROFILE_ID = "00000000-0000-4000-8000-6f70656e7072"
PROFILE_NAME = "OpenProgram"
IMPORT_ON = {"enabled": True, "bannerBehavior": "detect"}
#: Policies written once each, where the profile has none (magpie
#: desktopSeedOnce). magpie also opens Cowork's network egress to every host
#: (``coworkEgressAllowedHosts: ["*"]``); that is a security policy of
#: Desktop's that connecting a gateway has no need to change, so it is not
#: written.
SEEDS = {
    "disableDeploymentModeChooser": True,
    "claudeAiImport": IMPORT_ON,          # Settings → Import of the signed-in sessions
    "toolSearchEnabled": True,            # MCP tools behind ToolSearch
    "modelPrefer1mContext": True,         # a 1M model by its 1M entry
}
_LOCALE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$")
APP = App("com.anthropic.claudefordesktop", ("Claude.app",),
          instance=lambda command: desktop_instance(command), reopen=lambda run: reopen_isolated(run))


def desktop_dirs() -> tuple[Path, Path, Path]:
    """``(data, mode, 3p)``: Desktop's signed-in userData, the Claude folder
    ``deploymentMode`` is written in, and Claude-3p (magpie ``desktopdir.Sets``)."""
    home = Path.home()
    if sys.platform == "darwin":
        root = home / "Library" / "Application Support"
        return root / "Claude", root / "Claude", root / "Claude-3p"
    if sys.platform == "win32":
        def env(name: str, default: Path) -> Path:
            value = os.environ.get(name) or ""
            return Path(value) if value and Path(value).is_absolute() else default
        local = env("LOCALAPPDATA", home / "AppData" / "Local")
        roaming = env("APPDATA", home / "AppData" / "Roaming")
        return roaming / "Claude", local / "Claude", local / "Claude-3p"
    xdg = os.environ.get("XDG_CONFIG_HOME") or ""
    root = Path(xdg) if xdg and Path(xdg).is_absolute() else home / ".config"
    return root / "Claude", root / "Claude", root / "Claude-3p"


class Paths:
    """The files OpenProgram writes for Desktop (magpie ``desktopPaths``);
    ``isolated``: the copy's, whose userData and 3p folder are one
    (magpie ``desktopIsolatedSet``), given the language and MCP servers of
    the user's own Desktop."""

    def __init__(self, isolated: bool = False) -> None:
        data, mode, three = desktop_dirs()
        if isolated:
            mode = three = isolated_dir() or three
        self.isolated = isolated
        self.dir, self.dir3p = mode, three
        self.config = mode / "claude_desktop_config.json"
        self.config3p = three / "claude_desktop_config.json"
        self.library = three / "configLibrary"
        self.meta = self.library / "_meta.json"
        self.prof = self.library / f"{PROFILE_ID}.json"
        self.app = data / "config.json"
        self.app3p = three / "config.json"
        self.mcp_from = data / "claude_desktop_config.json"


# The isolated Desktop (magpie claudedesktop_isolated.go and
# internal/desktopiso): on a Mac, Desktop's 3p mode runs in a folder of its
# own, ~/Library/Application Support/Claude-OpenProgram-3p, started by
# ~/Applications/Claude OpenProgram.app, whose executable is a script that
# runs the user's Claude.app with --user-data-dir set to that folder.
# Desktop's 3p folder is its userData with "-3p" added unless it ends so
# already, so the folder holds the whole 3p mode, and the user's own Desktop,
# signed in with Anthropic, runs beside it untouched. Off by default: the
# "Runs In" field turns it on, while Desktop is off the gateway in
# OpenProgram. Turning it off takes the app away and leaves the folder, with
# the chats in it.

ISOLATED_NAME = "Claude-OpenProgram-3p"
LAUNCHER_NAME = "Claude OpenProgram"
#: Its CFBundleIdentifier, which says a bundle by that name is OpenProgram's:
#: one without it is the user's and is never written over.
LAUNCHER_ID = "org.openprogram.claude-desktop"
LAUNCHER_EXE = "claude-openprogram"
#: The ``runs_in`` field's choice; ``""`` is Desktop's own folders.
RUNS_IN = [("isolated", f"{LAUNCHER_NAME}, a copy of its own")]


def isolated_dir() -> Optional[Path]:
    """The isolated Desktop's folder, ``None`` where Desktop can't run so (only macOS)."""
    if sys.platform != "darwin":
        return None
    return Path.home() / "Library" / "Application Support" / ISOLATED_NAME


def _record_values() -> dict[str, Any]:
    from .. import stash
    try:
        record = stash.entry("claude_desktop") or {}
    except stash.StashUnreadable:
        return {}
    return dict(record.get("values") or {})


def isolated_on(values: Optional[dict[str, Any]] = None) -> bool:
    values = _record_values() if values is None else values
    return values.get("runs_in") == "isolated" and isolated_dir() is not None


def current_paths() -> Paths:
    """The files of the Desktop OpenProgram is connected to now."""
    return Paths(isolated_on())


def launcher_path() -> Path:
    return Path.home() / "Applications" / f"{LAUNCHER_NAME}.app"


def launcher_mine(bundle: Path) -> bool:
    try:
        info = (bundle / "Contents" / "Info.plist").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return f"<string>{LAUNCHER_ID}</string>" in info


def claude_app() -> Optional[Path]:
    """The user's Claude.app, ``None`` when none is installed."""
    from .base import app_bundles
    bundles = app_bundles(APP)
    return bundles[0] if bundles else None


def _plist_string(path: Path, key: str) -> str:
    import plistlib
    try:
        with open(path, "rb") as handle:
            value = plistlib.load(handle).get(key)
    except (OSError, ValueError, plistlib.InvalidFileException):
        return ""
    return value.strip() if isinstance(value, str) else ""


def launcher_script(app: Path, exe: str, folder: Path) -> str:
    """The launcher's executable (magpie ``desktopiso.Script``): Desktop's own
    executable ``exe`` in ``app`` (else in /Applications or ~/Applications,
    where it may have moved since) run on ``folder``. A Desktop already
    running on that folder is not started a second time: two copies on one
    userData share no lock."""
    def q(value: str) -> str:
        return "'" + value.replace("'", "'\\''") + "'"
    return ("#!/bin/sh\n"
            f"# Claude Desktop on OpenProgram, in a folder of its own; made by OpenProgram.\n"
            "# Turning Runs In back off in OpenProgram takes it away again.\n"
            f"dir={q(str(folder))}\n"
            f"exe={q(exe)}\n"
            "if /usr/bin/pgrep -f -- \"--user-data-dir=$dir( |\\$)\" >/dev/null 2>&1; then exit 0; fi\n"
            f"for app in {q(str(app))} /Applications/Claude.app \"$HOME/Applications/Claude.app\"; do\n"
            "\tif [ -x \"$app/Contents/MacOS/$exe\" ]; then\n"
            "\t\texec \"$app/Contents/MacOS/$exe\" --user-data-dir=\"$dir\" \"$@\"\n"
            "\tfi\n"
            "done\n"
            "echo 'Claude Desktop (Claude.app) is not installed' >&2\n"
            "exit 1\n")


def _launcher_plist(app: Path) -> str:
    from xml.sax.saxutils import escape
    version = _plist_string(app / "Contents" / "Info.plist", "CFBundleShortVersionString") or "1.0"
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0">\n<dict>\n'
            f"\t<key>CFBundleExecutable</key>\n\t<string>{LAUNCHER_EXE}</string>\n"
            f"\t<key>CFBundleIdentifier</key>\n\t<string>{LAUNCHER_ID}</string>\n"
            f"\t<key>CFBundleName</key>\n\t<string>{LAUNCHER_NAME}</string>\n"
            f"\t<key>CFBundleDisplayName</key>\n\t<string>{LAUNCHER_NAME}</string>\n"
            f"\t<key>CFBundleIconFile</key>\n\t<string>{LAUNCHER_EXE}</string>\n"
            "\t<key>CFBundlePackageType</key>\n\t<string>APPL</string>\n"
            f"\t<key>CFBundleShortVersionString</key>\n\t<string>{escape(version)}</string>\n"
            "\t<key>LSMinimumSystemVersion</key>\n\t<string>11.0</string>\n"
            "\t<key>NSHighResolutionCapable</key>\n\t<true/>\n"
            "</dict>\n</plist>\n")


def _register(bundle: Path) -> None:
    """Have macOS read the app again (its icon among it); tests replace it."""
    import subprocess
    tool = ("/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/"
            "Support/lsregister")
    try:
        subprocess.run([tool, "-f", str(bundle)], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass


def add_launcher(app: Path, folder: Path) -> Path:
    """Make the launcher starting the Desktop at ``app`` on ``folder``
    (magpie ``desktopiso.Add``). One of OpenProgram's there already is made
    again; one that isn't stays, and this fails. Its icon is Claude.app's
    own (magpie badges it with its bird; OpenProgram keeps no image library
    for that)."""
    import shutil
    import tempfile
    from .engine import AgentConfigError
    target = launcher_path()
    if (target.exists() or target.is_symlink()) and not launcher_mine(target):
        raise AgentConfigError(f"{target} is there already and isn't OpenProgram's")
    exe = _plist_string(app / "Contents" / "Info.plist", "CFBundleExecutable") or "Claude"
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{LAUNCHER_NAME}.app-", dir=target.parent))
    try:
        (tmp / "Contents" / "MacOS").mkdir(parents=True)
        (tmp / "Contents" / "Resources").mkdir(parents=True)
        (tmp / "Contents" / "Info.plist").write_text(_launcher_plist(app), encoding="utf-8")
        script = tmp / "Contents" / "MacOS" / LAUNCHER_EXE
        script.write_text(launcher_script(app, exe, folder), encoding="utf-8")
        script.chmod(0o755)
        icon = _plist_string(app / "Contents" / "Info.plist", "CFBundleIconFile") or "electron.icns"
        icon = Path(icon).name if Path(icon).suffix else f"{Path(icon).name}.icns"
        try:
            shutil.copyfile(app / "Contents" / "Resources" / icon,
                            tmp / "Contents" / "Resources" / f"{LAUNCHER_EXE}.icns")
        except OSError:
            pass  # no icon: the app still starts Desktop
        if target.exists():
            shutil.rmtree(target)
        tmp.rename(target)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    _register(target)
    return target


def remove_launcher() -> None:
    """Take the launcher away when OpenProgram made it."""
    import shutil
    target = launcher_path()
    if launcher_mine(target):
        shutil.rmtree(target, ignore_errors=True)


def desktop_instance(command: str) -> bool:
    """A Claude.app process's command line is the Desktop OpenProgram wired
    (magpie ``desktopInstance``): the copy's (``--user-data-dir`` on its
    folder) while it is on, else one on Desktop's own folders (no
    ``--user-data-dir`` at all). The other runs too, and isn't OpenProgram's
    to restart."""
    folder = isolated_dir()
    if folder is not None and isolated_on():
        flag = f" --user-data-dir={folder}"
        at = command.find(flag)
        return at >= 0 and command[at + len(flag):][:1] in ("", " ")
    return " --user-data-dir=" not in command


def reopen_isolated(run: Any) -> Optional[str]:
    """Quit the copy and open its launcher again (magpie ``reopenIsolated``):
    macOS's Quit by bundle can't tell the two Desktops apart, both being
    Claude.app, so the copy's process is sent SIGTERM, which Electron takes
    as a Quit. ``None`` for Desktop on its own folders: the usual Quit."""
    import subprocess
    import time
    from .running import POLL, QUIT_WAIT, find_app
    folder = isolated_dir()
    if folder is None or not isolated_on():
        return None
    launcher = launcher_path()
    if not launcher_mine(launcher):
        return f"{LAUNCHER_NAME}.app isn't in ~/Applications any more. Turn Runs In off and on again."
    pattern = "^" + re.escape(str(run.exe)) + " .*--user-data-dir=" + re.escape(str(folder)) + "( |$)"
    try:
        subprocess.run(["/usr/bin/pkill", "-TERM", "-f", pattern], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return f"OpenProgram couldn't ask {LAUNCHER_NAME} to quit. Quit it yourself, then open it again."
    deadline = time.monotonic() + QUIT_WAIT
    while find_app(APP) is not None:
        if time.monotonic() > deadline:
            return (f"{LAUNCHER_NAME} is still open; it may be asking something in its window. "
                    "Answer it there, or quit it, then open it again.")
        time.sleep(POLL)
    try:
        subprocess.run(["open", str(launcher)], capture_output=True, timeout=30, check=True)
    except (OSError, subprocess.SubprocessError):
        return f"{LAUNCHER_NAME} quit, but didn't open again. Open it yourself."
    return ""


# Where Desktop reaches the gateway (magpie claudedesktop_gateway.go)
#
# Desktop's built-in SSH remote sessions run its Code tab on another
# machine, which is handed the profile's gateway address and key. They are
# refused when the address is this computer's own (that endpoint is this
# computer, which a remote host cannot reach), and a non-loopback address
# must be https. So Desktop can be given, instead of 127.0.0.1, this
# computer's https address: OpenProgram's own while it is reachable from
# Anywhere (Settings → Devices; Tailscale Funnel), with the agent's own key,
# which the gateway takes from anywhere. Loopback stays the default.
# Desktop's ``sshHostAllowlist``, empty by default in 3p mode, says which
# hosts may be used at all; magpie seeds it with every host, OpenProgram
# leaves that security policy to the user in Desktop's window.


def remote_urls() -> list[str]:
    """The https addresses Desktop may be pointed at beyond loopback."""
    try:
        from openprogram.host.security.remote_access import anywhere
        address = anywhere.address()
    except Exception:  # noqa: BLE001 - no Worker here: none
        return []
    return [address.rstrip("/")] if isinstance(address, str) and address.startswith("https://") else []


def _gateway_choices() -> list[tuple[str, str]]:
    return [(url, url) for url in remote_urls()]


def desktop_gateway(pick: Any) -> str:
    """The address Desktop's profile gets for ``pick`` (magpie
    ``desktopGateway``): the one picked while it is still this computer's
    https address (the current one, where it changed since), else ``""``,
    the gateway on loopback."""
    if not isinstance(pick, str) or not pick:
        return ""
    urls = remote_urls()
    if pick.rstrip("/") in urls:
        return pick.rstrip("/")
    return urls[0] if urls else ""


def config_path() -> Path:
    return current_paths().config


def _doc(path: Path) -> dict[str, Any]:
    """A JSON object file's content, ``{}`` when missing, empty or not one."""
    try:
        text = ConfigText.read(path)
        doc = edit.load("json", text.text, str(path)) if text.exists and text.text.strip() else {}
    except (OSError, ConfigUnreadable):
        return {}
    return doc if isinstance(doc, dict) else {}


def _entries(meta: dict[str, Any]) -> list[Any]:
    entries = meta.get("entries")
    return list(entries) if isinstance(entries, list) else []


def _ours(entry: Any) -> bool:
    return isinstance(entry, dict) and entry.get("id") == PROFILE_ID


def wired(paths: Optional[Paths] = None) -> bool:
    """``_meta.json`` lists OpenProgram's profile."""
    return any(_ours(entry) for entry in _entries(_doc((paths or current_paths()).meta)))


def _our_gateway(url: Any) -> bool:
    """``url`` is this instance's gateway on loopback, or at the https address
    other machines reach it at (magpie ``desktopIsOurGateway``,
    ``desktopOurAddress``)."""
    from urllib.parse import urlparse
    from ..address import worker_base_url
    if not isinstance(url, str):
        return False
    try:
        given, ours = urlparse(url.strip()), urlparse(worker_base_url(stable=True))
        for remote in remote_urls():
            there = urlparse(remote)
            if given.scheme == "https" and given.hostname == there.hostname and \
                    (given.port or 443) == (there.port or 443):
                return True
        if not given.port or given.port != ours.port:
            return False
    except ValueError:
        return False
    host = given.hostname or ""
    return host in ("127.0.0.1", "localhost", "::1") or host == ours.hostname


def _profile_id_ok(profile_id: Any) -> bool:
    return isinstance(profile_id, str) and bool(profile_id) and profile_id != PROFILE_ID \
        and Path(profile_id).name == profile_id and not profile_id.startswith(".")


def _gateway_profile(paths: Paths, profile_id: Any) -> Optional[Path]:
    """The configLibrary profile ``profile_id`` when it is one for this gateway."""
    if not _profile_id_ok(profile_id):
        return None
    path = paths.library / f"{profile_id}.json"
    doc = _doc(path)
    if doc.get("inferenceProvider") != "gateway" or not _our_gateway(doc.get("inferenceGatewayBaseUrl")):
        return None
    return path


def applied_gateway(paths: Paths) -> Optional[Path]:
    """The profile Desktop applies when it is not OpenProgram's own but one
    for its gateway (one imported from another computer by Desktop's
    "Import configuration"): it stays applied (magpie ``desktopAppliedGateway``)."""
    return _gateway_profile(paths, _doc(paths.meta).get("appliedId"))


def three_p(paths: Paths, profile: Path) -> bool:
    """Desktop starts in its 3p mode with ``profile``, as Desktop decides it
    (magpie ``desktop3p``): the profile's disabled chooser, else unless
    Claude-3p's file says "1p"."""
    if _doc(profile).get("disableDeploymentModeChooser") is True:
        return True
    return _doc(paths.config3p).get("deploymentMode") != "1p"


def _key_taken(value: Any) -> bool:
    from ..keys import verify_gateway_key
    from .engine import SEALED
    if not isinstance(value, str):
        return False
    return value.lstrip().startswith(SEALED) or verify_gateway_key(value)


def _escape(name: str) -> str:
    return name.replace(".", "\\.")


def _previous(at: Wiring, path: Path) -> dict[str, Any]:
    return ((at.previous.get("files") or {}).get(str(path)) or {})


def _wire(at: Wiring) -> list[Write]:
    from .. import desktop
    from .engine import AgentConfigError
    runs_in = at.values.get("runs_in", "")
    was = at.previous.get("values") or {} if at.previous else None
    if was is not None and was.get("model") and was.get("runs_in", "") != runs_in:
        raise AgentConfigError("turn Claude Desktop off in OpenProgram first, then change where it runs")
    if runs_in and isolated_dir() is None:
        raise AgentConfigError("Claude Desktop runs in a folder of its own on macOS only")
    if runs_in and claude_app() is None:
        raise AgentConfigError("Claude Desktop (Claude.app) isn't installed")
    paths = Paths(bool(runs_in))
    meta = _doc(paths.meta)
    entries = _entries(meta)
    already = any(_ours(entry) for entry in entries)
    writes: list[Write] = []

    # the profile: the gateway's address and key; Desktop's own settings in
    # it kept; the policies seeded once
    prof = _doc(paths.prof)
    kept = _previous(at, paths.prof)
    sets: dict[str, Any] = {
        "inferenceProvider": "gateway",
        "inferenceGatewayBaseUrl": (desktop_gateway(at.values.get("gateway")) + "/gateway/anthropic"
                                    if desktop_gateway(at.values.get("gateway")) else at.base("anthropic")),
        "inferenceGatewayApiKey": at.key,
        "inferenceGatewayAuthScheme": "bearer",
    }
    saved = _saved(paths, meta.get("appliedId"))
    for key, value in SEEDS.items():
        if key not in prof and key not in saved and key not in (kept.get("originals") or {}):
            sets[key] = value
    deletes: list[str] = []
    rows = desktop.ordered(at.models, at.values)
    want = desktop.inference_models(rows, at.values)
    wrote = kept.get("wrote") or {}

    def ours(key: str) -> bool:
        present, current = get_path(prof, key)
        return present and (wrote.get(key) or {}).get("set", object()) == current

    # inferenceModels: OpenProgram's only while it is what it wrote; the
    # user's own list is never written over (magpie desktopSyncModels), and
    # modelDiscoveryEnabled goes with OpenProgram's list (desktopSyncDiscovery)
    listed = False
    if want is not None and ("inferenceModels" not in prof or ours("inferenceModels")):
        sets["inferenceModels"], listed = want, True
    elif want is None and ours("inferenceModels"):
        deletes.append("inferenceModels")
    if listed and desktop.discovers(at.values):
        if "modelDiscoveryEnabled" not in prof or ours("modelDiscoveryEnabled"):
            sets["modelDiscoveryEnabled"] = True
    elif ours("modelDiscoveryEnabled"):
        deletes.append("modelDiscoveryEnabled")
    writes.append(Write(paths.prof, "json", sets=sets, deletes=deletes, listing=True))

    # a profile for this gateway the user applied since stays applied, given
    # a key the gateway takes (magpie desktopKeyApplied)
    theirs = applied_gateway(paths) if already else None
    if theirs is not None:
        doc = _doc(theirs)
        if not _key_taken(doc.get("inferenceGatewayApiKey")):
            fix: dict[str, Any] = {"inferenceGatewayApiKey": at.key}
            if not doc.get("inferenceGatewayAuthScheme"):
                fix["inferenceGatewayAuthScheme"] = "bearer"
            writes.append(Write(theirs, "json", sets=fix))

    # _meta.json: OpenProgram's entry, applied
    meta_sets: dict[str, Any] = {}
    if not already:
        meta_sets["entries"] = [*entries, {"id": PROFILE_ID, "name": PROFILE_NAME}]
    if theirs is None:
        meta_sets["appliedId"] = PROFILE_ID
    if meta_sets:
        writes.append(Write(paths.meta, "json", sets=meta_sets))

    # the language picked signed in with Anthropic (magpie desktopCarryLocale)
    if paths.app != paths.app3p:
        locale = _doc(paths.app).get("locale")
        if isinstance(locale, str) and _LOCALE.match(locale) and "locale" not in _doc(paths.app3p):
            writes.append(Write(paths.app3p, "json", sets={"locale": locale}))

    # the mode last: until here Desktop still starts as it did; Claude-3p's
    # file is given the MCP servers added signed in (magpie desktopCarryMCP)
    three_sets: dict[str, Any] = {"deploymentMode": "3p"}
    own = _doc(paths.mcp_from).get("mcpServers")
    have = _doc(paths.config3p).get("mcpServers")
    if paths.mcp_from != paths.config3p and isinstance(own, dict) and (have is None or isinstance(have, dict)):
        for name, server in own.items():
            if name not in (have or {}):
                three_sets[f"mcpServers.{_escape(name)}"] = server
    writes.append(Write(paths.config3p, "json", sets=three_sets))
    if paths.config != paths.config3p:  # the copy's mode and 3p file are one
        writes.append(Write(paths.config, "json", sets={"deploymentMode": "3p"}))
    return writes


def _keep_path(paths: Paths) -> Path:
    """Where OpenProgram's profile is kept while Desktop (or its copy) is off
    the gateway, the settings the user made in it from Desktop's window among them."""
    from ..keys import gateway_dir
    return gateway_dir() / ("claude-desktop-isolated-profile.json" if paths.isolated
                            else "claude-desktop-profile.json")


def _saved(paths: Paths, applied: Any) -> dict[str, Any]:
    """The settings OpenProgram's profile starts from: the profile as it was
    turned off, else the user's own profile for this gateway applied before
    (magpie desktopOwnGatewayProfile), its address and key left out."""
    saved = _doc(_keep_path(paths))
    if not saved:
        own = _gateway_profile(paths, applied)
        saved = _doc(own) if own is not None else {}
    return {key: value for key, value in saved.items() if not key.startswith("inferenceGateway")}


def _merge_absent(path: Path, extra: dict[str, Any]) -> None:
    doc = _doc(path)
    sets = {_escape(key): value for key, value in extra.items() if key not in doc}
    if not sets:
        return
    text = ConfigText.read(path)
    text.write(edit.edit("json", text.text or "{}\n", sets=sets, where=str(path)))


def _on_route(_home: Path, connected: bool, record: dict[str, Any]) -> None:
    from .engine import AgentConfigError, remove_made
    paths = Paths(isolated_on(dict(record.get("values") or {})))
    if paths.isolated:
        if connected:
            app = claude_app()
            if app is None:
                raise AgentConfigError("Claude Desktop (Claude.app) isn't installed")
            paths.dir3p.mkdir(parents=True, exist_ok=True)
            add_launcher(app, paths.dir3p)
        else:
            remove_launcher()
    if connected:
        # the profile as it was turned off, else the user's own profile for
        # this gateway applied before: its settings start OpenProgram's
        original = ((record.get("files") or {}).get(str(paths.meta)) or {}).get("originals") or {}
        saved = _saved(paths, (original.get("appliedId") or {}).get("value"))
        if saved:
            _merge_absent(paths.prof, saved)
        keep = _keep_path(paths)
        keep.unlink(missing_ok=True)
        try:
            os.chmod(paths.prof, 0o600)
        except OSError:
            pass
        return
    # off: OpenProgram's entry out of _meta.json where the list changed since
    meta = _doc(paths.meta)
    entries = _entries(meta)
    sets: dict[str, Any] = {}
    deletes: list[str] = []
    if any(_ours(entry) for entry in entries):
        entries = [entry for entry in entries if not _ours(entry)]
        sets["entries"] = entries
    if meta.get("appliedId") == PROFILE_ID:
        if entries and isinstance(entries[0], dict) and entries[0].get("id"):
            sets["appliedId"] = entries[0]["id"]
        else:
            deletes.append("appliedId")
    if sets or deletes:
        text = ConfigText.read(paths.meta)
        text.write(edit.edit("json", text.text, sets=sets, deletes=deletes, where=str(paths.meta)))
    # the profile goes; what is left in it is kept for the next on
    doc = _doc(paths.prof)
    if doc:
        # it holds the agent's key: only its owner may read it
        edit.write_atomic(_keep_path(paths), json.dumps(doc, indent=2) + "\n", keep_mode=False)
    if paths.prof.exists():
        paths.prof.unlink(missing_ok=True)
        remove_made(((record.get("files") or {}).get(str(paths.prof))) or {})


def _check() -> str:
    """What keeps Desktop off the gateway, ``""`` for nothing (magpie ``desktopCheck``)."""
    paths = current_paths()
    if not wired(paths):
        return ""
    profile = paths.prof
    if _doc(paths.meta).get("appliedId") != PROFILE_ID:
        profile = applied_gateway(paths)
        if profile is None:
            return ("Claude Desktop uses another gateway configuration now "
                    "(appliedId in configLibrary/_meta.json), not OpenProgram's")
    if not three_p(paths, profile):
        return ("Claude Desktop's deploymentMode (Claude-3p/claude_desktop_config.json) is 1p now, "
                "so it signs in with Anthropic rather than using OpenProgram")
    return ""


def _notice() -> str:
    if isolated_on():
        return (f"{LAUNCHER_NAME} in ~/Applications starts a copy of Claude Desktop on OpenProgram, in a "
                "folder of its own; your own Claude stays signed in with Anthropic and runs beside it. "
                f"Open {LAUNCHER_NAME} (quit and reopen it when it is open).")
    return ("Claude Desktop reads this at start-up — quit and reopen it to run on OpenProgram "
            "(Code and Cowork, no Anthropic sign-in). Chats and projects from your Anthropic sign-in stay "
            "in Desktop's own profile, not shown here; nothing is deleted, and turning this off brings "
            "them back. To go on with them, open Settings → Import in Desktop: it copies in your Claude.ai "
            "chats and projects and this computer's earlier sessions, and leaves the originals as they are.")


def _efforts() -> list[tuple[str, str]]:
    from ..desktop import EFFORTS
    return [(level, level) for level in EFFORTS]


def agent() -> Agent:
    from ..desktop import TIER_NAMES
    return Agent(
        id="claude_desktop", name="Claude Desktop", icon="anthropic", kind="app",
        aliases=("claude-desktop", "claude-app"), protocol="anthropic",
        dir=lambda: desktop_dirs()[1], path=config_path,
        fields=[Field("model", "Model"),
                *(Field(tier, tier.capitalize(), quiet=True) for tier in TIER_NAMES),
                Field("subagent_model", "Subagent model", quiet=True),
                Field("subagent_effort", "Subagent effort", quiet=True, options=_efforts),
                # this computer, or its https address for SSH remote sessions
                Field("gateway", "Gateway", quiet=True, options=_gateway_choices),
                *([Field("runs_in", "Runs In", quiet=True, options=lambda: list(RUNS_IN))]
                  if sys.platform == "darwin" else [])],
        named_key=True, before_copy=True, wire=_wire, on_route=_on_route, notice=_notice,
        app=APP, check=_check,
        # Desktop drops Claude-3p's deploymentMode itself at start, never
        # reads the Claude folder's, and applies a profile the user imports
        # for this gateway: _check says what keeps it off. The policies
        # seeded and a model list the user changes are theirs.
        loose=("deploymentMode", "appliedId", "entries", "inferenceModels", "modelDiscoveryEnabled", *SEEDS),
    )
