"""Codex CLI and the Codex app (``$CODEX_HOME/config.toml``, default ``~/.codex``).

After yetone/magpie ``internal/agent/codex.go``, ``codex_profile.go``,
``codex_async.go`` and ``codex_compact_held.go`` (MIT License, Copyright (c)
yetone), without its join mode: Codex is put on the gateway only as a
provider of its own, never by passing its ChatGPT sign-in through (no
``openai_base_url``, no proxy of Codex's own models). Connecting writes
``model_provider = "openprogram"`` and ``model = "<provider>/<model>"`` at the
top level and a ``[model_providers.openprogram]`` table with the gateway's
``base_url``, ``wire_api = "responses"`` and the gateway key as
``experimental_bearer_token``. Disconnecting puts ``model_provider`` and
``model`` back; the table stays, because Codex cannot reopen a thread started
on a provider whose table is gone.

The other fields, after magpie's:

* ``subagent``: ``[agents] default_subagent_model``, the model a spawned
  subagent starts on when the lead names none (unset, the lead's own).
* ``memories``: ``[memories] extract_model`` and ``consolidation_model``,
  one pick for both, the model Codex writes its memories with (unset,
  Codex takes models of its own whatever its model).
* ``scope`` ("applies to"): all of Codex, or ``profile``: only ``codex -p
  openprogram``. Codex reads ``--profile <name>`` from ``<name>.config.toml``
  beside ``config.toml`` and refuses ``-p`` while ``config.toml`` holds the
  legacy ``[profiles.<name>]``, so the profile wiring writes
  ``openprogram.config.toml`` alone (provider, model, catalog and its own
  provider table) and never touches ``config.toml``; disconnecting removes
  that file. It changes only while Codex is off, so the two wirings never
  meet in one config.

Background compaction (magpie ``codex_async.go``): Codex (0.163 on) starts a
compaction of its own while a turn runs only on a provider whose
``capabilities.remote_compaction`` is ``"v2"``, which it gives OpenAI's own
alone; the gateway answers it at ``/responses/compact``. So where the
feature is on in the config (the user's own ``[features] async_compaction``,
or OpenProgram's when its Background Compaction field is on) the provider table gets that
capability. The feature's table form is read only from
:data:`ASYNC_TABLE_FROM` on: an older Codex stops loading a config holding
it, so the table is written only when every Codex reading the config is
known to be new enough, the bare ``async_compaction = true`` otherwise. A
value the user set is theirs and is never written over.

The Codex app reads the same ``config.toml``: ``model_catalog_json`` names
the gateway's models for its picker (``openprogram-models.json``, rewritten
whenever the catalog changes), and the app's app-server is restarted and its
threads moved when the route changes (``codex_app.py``).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from .base import Agent, App, Field, Wiring, Write, app_bundles, find_tool

PROVIDER_ID = "openprogram"
PROVIDER_TABLE = f"model_providers.{PROVIDER_ID}"
#: The file ``codex -p openprogram`` reads (magpie ``codexProfileFile``).
PROFILE_FILE = f"{PROVIDER_ID}.config.toml"
#: The ``scope`` field's choices; ``""`` is all of Codex.
SCOPES = [("profile", f"codex -p {PROVIDER_ID} only")]
#: The ``async`` field's choices (magpie's Settings switch Background
#: compaction, ``codexAsyncCompact``, and its share of the model's window,
#: ``codexAsyncCompactPercent``): ``"on"`` at Codex's own share (65%), a
#: number at that share; ``""`` leaves it off.
ASYNC_CHOICES = [("on", "On, at Codex's own 65%"), ("50", "On, at 50%"), ("75", "On, at 75%"),
                 ("85", "On, at 85%")]

SUBAGENT_KEY = "agents.default_subagent_model"
MEMORY_KEYS = ("memories.extract_model", "memories.consolidation_model")

ASYNC_TABLE = "features.async_compaction"
ASYNC_PERCENT = "model_async_compact_threshold_percent"
#: The first Codex whose ``[features]`` takes ``async_compaction`` as a table
#: (``FeatureToml<AsyncCompactionConfigToml>``, rust-v0.163.0-alpha.8).
ASYNC_TABLE_FROM = "0.163.0-alpha.8"
CAPABILITIES = {"remote_compaction": "v2"}

#: The config keys Codex reads before the catalog's window and threshold
#: (magpie ``codexCompactKeys``).
COMPACT_KEYS = ("model_context_window", "model_auto_compact_token_limit")

#: The Codex desktop app (magpie ``desktopApps["codex"]``).
CODEX_APP = App("com.openai.codex", ("ChatGPT.app", "Codex.app"))


def home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")).expanduser()


def config_path() -> Path:
    return home() / "config.toml"


def profile_path() -> Path:
    return home() / PROFILE_FILE


def _doc(path: Path) -> dict[str, Any]:
    from ..edit import ConfigText, load
    text = ConfigText.read(path)
    return load("toml", text.text, str(path)) if text.exists else {}


def _table(base: str, key: str) -> dict[str, Any]:
    table = {"name": "OpenProgram", "base_url": base, "wire_api": "responses",
             "experimental_bearer_token": key}
    return {f"{PROVIDER_TABLE}.{k}": v for k, v in table.items()}


def _wire(at: Wiring) -> list[Write]:
    scope = at.values.get("scope", "")
    was = at.previous.get("values") or {} if at.previous else None
    if was is not None and was.get("model") and was.get("scope", "") != scope:
        from .engine import AgentConfigError
        raise AgentConfigError("turn Codex off in OpenProgram first, then change what it applies to")
    if scope == "profile":
        return _wire_profile(at)
    from ..codex_app import catalog_path, catalog_text
    path = config_path()
    doc = _doc(path)
    writes = []
    sets: dict[str, Any] = {"model_provider": PROVIDER_ID, "model": at.model}
    deletes: list[str] = []
    catalog = catalog_text(at.model, list(at.options.get("codex_versions") or []), at.models)
    if catalog is not None:
        # the Codex app lists models from a catalog, not from config.toml
        sets["model_catalog_json"] = str(catalog_path(path.parent))
        writes.append(Write(catalog_path(path.parent), "file", content=catalog, listing=True))
    picks = {SUBAGENT_KEY: at.values.get("subagent", "")}
    picks.update({key: at.values.get("memories", "") for key in MEMORY_KEYS})
    for key, value in picks.items():
        if value:
            sets[key] = value
        else:
            _unpick(at, path, doc, key, sets, deletes)
    _async(at, path, doc, sets, deletes)
    writes.insert(0, Write(path, "toml", sets=sets, deletes=deletes, keep=_table(at.base("openai-responses"), at.key)))
    return writes


def _wire_profile(at: Wiring) -> list[Write]:
    """Codex wired by its profile alone (magpie ``codexProfileIn``)."""
    from ..codex_app import CATALOG_NAME, catalog_path, catalog_text
    folder = home()
    sets: dict[str, Any] = {"model_provider": PROVIDER_ID, "model": at.model,
                            **_table(at.base("openai-responses"), at.key)}
    writes = []
    catalog = catalog_text(at.model, list(at.options.get("codex_versions") or []), at.models)
    if catalog is not None:
        # the catalog as Codex reads it from this file: beside it, so by name
        sets["model_catalog_json"] = CATALOG_NAME
        writes.append(Write(catalog_path(folder), "file", content=catalog, listing=True))
    writes.insert(0, Write(profile_path(), "toml", sets=sets))
    return writes


def _written(at: Wiring, path: Path, key: str) -> tuple[bool, Any, Optional[dict[str, Any]]]:
    """Whether OpenProgram wrote ``key`` of ``path`` last connect, what, and its original."""
    kept = ((at.previous.get("files") or {}).get(str(path)) or {}) if at.previous else {}
    wrote = (kept.get("wrote") or {}).get(key)
    if not wrote or "set" not in wrote:
        return False, None, None
    return True, wrote["set"], (kept.get("originals") or {}).get(key)


def _unpick(at: Wiring, path: Path, doc: dict[str, Any], key: str,
            sets: dict[str, Any], deletes: list[str]) -> None:
    """A field taken back to Default: a value OpenProgram wrote, still as
    written, goes back to what was there before (magpie's ``Set("")``)."""
    from ..edit import get_path
    ours, value, original = _written(at, path, key)
    if not ours or get_path(doc, key) != (True, value):
        return
    if original and original.get("had"):
        sets[key] = original.get("value")
    else:
        deletes.append(key)


def _drop_added(at: Wiring, path: Path, doc: dict[str, Any], tables: list[str], deletes: list[str]) -> None:
    """Take out ``tables`` OpenProgram added (in order, inner first) once
    nothing is left in them but what ``deletes`` takes out."""
    from ..edit import delete_path, get_path
    import copy
    kept = ((at.previous.get("files") or {}).get(str(path)) or {}) if at.previous else {}
    added = set(kept.get("parents") or [])
    after = copy.deepcopy(doc)
    for key in deletes:
        delete_path(after, key)
    for table in tables:
        present, value = get_path(after, table)
        if table in added and present and isinstance(value, dict) and not value:
            deletes.append(table)
            delete_path(after, table)


# Background compaction (magpie codex_async.go)

@dataclass(frozen=True)
class CodexReader:
    """A Codex that reads the config: ``cli``, ``app`` or ``ide``, and its
    version, ``""`` when that isn't known (magpie ``CodexReader``)."""

    kind: str
    version: str = ""


_VERSION = re.compile(r"(\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?)")
_PACKAGE_VERSION = re.compile(r'"version"\s*:\s*"([^"]+)"')


def _cli_version(binary: str) -> str:
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    match = _VERSION.search(out or "")
    return match.group(1) if match else ""


def _package_version(path: Path) -> Optional[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = _PACKAGE_VERSION.search(text)
    return match.group(1).strip() if match else ""


def _extension_readers(user_home: Path) -> list[CodexReader]:
    """The Codexes the editors' OpenAI extension (``openai.chatgpt``) carries,
    one per installed copy in ``~/.<editor>/extensions``; a copy marked
    obsolete is not run any more (magpie ``extensionCodexReaders``)."""
    found = []
    try:
        folders = [entry for entry in os.scandir(user_home) if entry.name.startswith(".")]
    except OSError:
        return []
    for folder in folders:
        root = Path(folder.path) / "extensions"
        try:
            copies = list(os.scandir(root))
        except OSError:
            continue
        try:
            obsolete = json.loads((root / ".obsolete").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            obsolete = {}
        for entry in copies:
            if not entry.is_dir() or not entry.name.lower().startswith("openai.chatgpt-") \
                    or (isinstance(obsolete, dict) and obsolete.get(entry.name)):
                continue
            version = ""
            for package in [*sorted(Path(entry.path).glob("bin/*/codex-package.json")),
                            Path(entry.path) / "bin" / "codex-package.json"]:
                read = _package_version(package)
                if read:
                    version = read
                    break
            found.append(CodexReader("ide", version))
    return found


def _readers() -> list[CodexReader]:
    """The Codexes that read the config: the CLI, the desktop app's bundled
    one, and the editor extensions' (magpie ``codexReaders``)."""
    found = []
    binary = find_tool("codex")
    if binary:
        found.append(CodexReader("cli", _cli_version(os.path.realpath(binary))))
    if sys.platform == "darwin":
        for bundle in app_bundles(CODEX_APP):
            version = _package_version(bundle / "Contents" / "Resources" / "codex-cli" / "codex-package.json")
            found.append(CodexReader("app", version or ""))
    return found + _extension_readers(Path.home())


#: The Codexes reading this machine's config; a module value so tests can name them.
codex_readers: Callable[[], list[CodexReader]] = _readers


def _version_key(version: str) -> tuple:
    core, _, pre = version.partition("-")
    numbers = tuple(int(part) if part.isdigit() else 0 for part in core.split("."))
    if not pre:
        return numbers, (1,)
    return numbers, (0, *[(0, int(part), "") if part.isdigit() else (1, 0, part) for part in pre.split(".")])


def older(version: str, than: str) -> bool:
    """``version`` comes before ``than`` (semver with pre-releases)."""
    return _version_key(version) < _version_key(than)


def async_blocked(readers: list[CodexReader]) -> list[CodexReader]:
    """The readers that keep the feature's table out: one older than
    :data:`ASYNC_TABLE_FROM`, or of no known version. None at all is unknown
    as well: a Codex OpenProgram can't see may read the config
    (magpie ``codexAsyncBlocked``)."""
    if not readers:
        return [CodexReader("cli")]
    return [r for r in readers if not r.version or older(r.version, ASYNC_TABLE_FROM)]


def _async(at: Wiring, path: Path, doc: dict[str, Any], sets: dict[str, Any], deletes: list[str]) -> None:
    """The background compaction's writes (magpie ``codexAsyncSync``):
    the ``async`` field turns it on, ``"on"`` at Codex's own threshold (65%)
    or a number at that share of the window."""
    from ..edit import get_path
    picked = str(at.values.get("async") or "")
    want = bool(picked)
    table_ok = want and not async_blocked(codex_readers())
    percent = min(int(picked), 100) if table_ok and picked.isdigit() else 0
    present, feature = get_path(doc, ASYNC_TABLE)
    ours_bare = _written(at, path, ASYNC_TABLE)[0]
    ours_enabled = _written(at, path, f"{ASYNC_TABLE}.enabled")[0]
    ours_percent = _written(at, path, f"{ASYNC_TABLE}.{ASYNC_PERCENT}")[0]
    if not present:
        if table_ok:
            sets[f"{ASYNC_TABLE}.enabled"] = True
            if percent:
                sets[f"{ASYNC_TABLE}.{ASYNC_PERCENT}"] = percent
            feature = {"enabled": True}
        elif want:
            sets[ASYNC_TABLE] = True
            feature = True
    elif feature is True and ours_bare and (not want or table_ok):
        # the bare key OpenProgram wrote, where the table is to go instead or nothing is
        _unpick(at, path, doc, ASYNC_TABLE, sets, deletes)
        feature = None
        if table_ok:
            sets[f"{ASYNC_TABLE}.enabled"] = True
            if percent:
                sets[f"{ASYNC_TABLE}.{ASYNC_PERCENT}"] = percent
            feature = {"enabled": True}
    elif isinstance(feature, dict):
        if not table_ok and ours_enabled:
            # off, or a Codex here too old for the table: the table OpenProgram wrote goes
            _unpick(at, path, doc, f"{ASYNC_TABLE}.enabled", sets, deletes)
            if ours_percent:
                _unpick(at, path, doc, f"{ASYNC_TABLE}.{ASYNC_PERCENT}", sets, deletes)
            feature = {k: v for k, v in feature.items() if k not in ("enabled", ASYNC_PERCENT)} or None
            if feature is None:
                # a table left with nothing of the user's goes whole, so an old Codex loads its config again
                _drop_added(at, path, doc, [ASYNC_TABLE, "features"], deletes)
        elif table_ok:
            if "enabled" not in feature:
                sets[f"{ASYNC_TABLE}.enabled"] = True
            if percent and (ASYNC_PERCENT not in feature or ours_percent):
                sets[f"{ASYNC_TABLE}.{ASYNC_PERCENT}"] = percent
            elif not percent and ours_percent:
                _unpick(at, path, doc, f"{ASYNC_TABLE}.{ASYNC_PERCENT}", sets, deletes)
            feature = {**feature, "enabled": feature.get("enabled", True)}
    # the capability on OpenProgram's provider table, for a feature that is on
    # (the user's own too: on this provider it would never run without it);
    # one the user gave the table, whatever it says, stays as it is
    on = feature is True or (isinstance(feature, dict) and feature.get("enabled") is True)
    key = f"{PROVIDER_TABLE}.capabilities"
    has, caps = get_path(doc, key)
    if on and (not has or _written(at, path, key)[0]):
        sets[key] = dict(CAPABILITIES)
    elif not on:
        _unpick(at, path, doc, key, sets, deletes)


# Keys that come before the catalog's window (magpie codex_compact_held.go)

@dataclass(frozen=True)
class CompactHold:
    """One key Codex reads before the window the catalog gives each model:
    at the top level, or in the profile the config picks."""

    path: str
    key: str
    profile: str
    value: str


def _compact_value(value: Any) -> str:
    text = str(value).strip()
    digits = text.replace("_", "")
    return str(int(digits)) if digits.isdigit() else text


def compact_held(codex_home: Optional[Path] = None) -> list[CompactHold]:
    """The keys this Codex config has that come before the catalog's window:
    the first becomes every model's window (Codex compacts at 90% of it), the
    second is where it compacts (magpie ``CodexCompactHeld``). The values are
    the user's, never taken out unasked."""
    path = (codex_home or home()) / "config.toml"
    try:
        doc = _doc(path)
    except Exception:  # noqa: BLE001 - a config that does not parse holds nothing readable
        return []
    found = [CompactHold(str(path), key, "", _compact_value(doc[key])) for key in COMPACT_KEYS if key in doc]
    profile = doc.get("profile")
    if isinstance(profile, str) and profile:
        table = (doc.get("profiles") or {}).get(profile) or {}
        found += [CompactHold(str(path), key, profile, _compact_value(table[key])) for key in COMPACT_KEYS if key in table]
    return found


def drop_compact_holds(codex_home: Optional[Path] = None) -> None:
    """Take those keys out, so Codex compacts where the catalog says
    (magpie ``DropCodexCompactHolds``); asked for by the user only."""
    from ..edit import ConfigText, edit
    for hold in compact_held(codex_home):
        text = ConfigText.read(Path(hold.path))
        dotted = hold.key if not hold.profile else f"profiles.{hold.profile.replace('.', chr(92) + '.')}.{hold.key}"
        text.write(edit("toml", text.text, deletes=[dotted], where=hold.path))


def codex_model(codex_home: Optional[Path] = None) -> str:
    """The OpenProgram model this Codex config runs: the profile's the config
    picks, else the top level's; ``""`` on a provider of its own
    (magpie ``CodexModel``)."""
    try:
        doc = _doc((codex_home or home()) / "config.toml")
    except Exception:  # noqa: BLE001
        return ""
    model, provider = doc.get("model", ""), doc.get("model_provider", "")
    profile = doc.get("profile")
    if isinstance(profile, str) and profile:
        table = (doc.get("profiles") or {}).get(profile) or {}
        model, provider = table.get("model") or model, table.get("model_provider") or provider
    return model if provider == PROVIDER_ID else ""


# The route and what to say

def original_provider(record: dict[str, Any]) -> str:
    """The provider Codex used before the gateway: config.toml's, else ``openai``."""
    for kept in (record.get("files") or {}).values():
        original = (kept.get("originals") or {}).get("model_provider") or {}
        value = original.get("value") if original.get("had") else None
        if isinstance(value, str) and value and value != PROVIDER_ID:
            return value
    return "openai"


def _profile_only(record: dict[str, Any]) -> bool:
    return (record.get("values") or {}).get("scope") == "profile"


def _on_route(codex_home: Path, connected: bool, record: dict[str, Any]) -> None:
    from ..codex_app import move_history, restart_app
    if _profile_only(record):
        return  # plain codex and the app keep their own provider
    if connected:
        move_history(codex_home, PROVIDER_ID, original_provider(record))
    else:
        move_history(codex_home, original_provider(record), PROVIDER_ID)
    restart_app(codex_home)


def _notice() -> str:
    from .. import stash
    from .running import any_running
    try:
        record = stash.entry("codex") or {}
    except stash.StashUnreadable:
        record = {}
    if _profile_only(record):
        return f"Start Codex with codex -p {PROVIDER_ID}; plain codex keeps its own provider."
    notes = []
    if any_running(r"(^|/)codex( |$)"):
        notes.append("Codex builds its model list at start-up — restart open codex sessions to see this.")
    held = compact_held()
    if held:
        keys = ", ".join(dict.fromkeys(hold.key for hold in held))
        notes.append(f"Codex's config.toml sets {keys}, which comes before each model's own window; "
                     "take it out for Codex to compact where the model's window says.")
    return " ".join(notes)


def _held() -> list[Path]:
    return [home() / "packages" / "standalone", Path("/Applications/Codex.app"), Path("/Applications/ChatGPT.app")]


def agent() -> Agent:
    return Agent(
        id="codex", name="Codex", icon="openai", aliases=("codex-cli",), kind="cli",
        protocol="openai-responses", bin="codex", dir=home, path=config_path,
        fields=[Field("model", "Model"),
                # Codex lists only five models in its spawn_agent tool; this
                # is the model a spawn naming none starts on
                Field("subagent", "Subagents", quiet=True, follows="model"),
                Field("memories", "Memories", quiet=True),
                Field("scope", "Applies To", quiet=True, options=lambda: list(SCOPES)),
                # magpie's Settings → Background compaction and its share
                Field("async", "Background Compaction", quiet=True, options=lambda: list(ASYNC_CHOICES))],
        ua=("codex", "acp-extension-codex"),
        harness=True, wire=_wire, on_route=_on_route, notice=_notice, held_at=_held,
    )
