"""Hermes Agent (Nous Research) and each of its profiles (``$HERMES_HOME/config.yaml``).

After yetone/magpie ``internal/agent/hermes.go`` and ``internal/appdir/hermes.go``
(MIT License, Copyright (c) yetone). Hermes keeps the model under
``model.default``, served by ``model.provider``, which may name an entry of
``providers``. OpenProgram adds itself there as the provider ``openprogram``
(the gateway, spoken to as chat completions, with the catalog as its models)
and points ``model.provider`` at it; ``model.default`` holds
``<provider>/<model>``. ``hermes model`` rewrites the two as one pick: the
provider and model the user had go back on disconnect while the provider
is still one of OpenProgram's, and a sync of the model list never writes
them. OpenProgram's provider entries are its own: keys Hermes or the user
adds to them stay while connected, and they go whole on disconnect.

Each of Hermes's bots is a profile with its own ``config.yaml``, which
doesn't read the main one's: the provider entry goes into every profile's
too, so OpenProgram is offered in every bot's picker, and disconnecting
takes it out of each. A profile's own model is left as its user set it;
each other profile also has a model field of its own on Hermes's row, to
put that bot on a model of the gateway's. The config connected is the profile ``hermes``
starts in (``active_profile``).

Models whose provider serves them on Anthropic's Messages go under a second
entry, ``openprogram-anthropic``, spoken to as Messages, so Hermes marks
their prompts for caching itself. A bot nobody picked a model for follows
the connected one while it is still on the model OpenProgram had set, and
disconnecting moves each bot on OpenProgram's entries back to its own
model (or the user's from the connected config), to be put back on
OpenProgram's when connecting again. A local proxy's prefix in front of the
gateway's address stays (:func:`through`). The effort field is
``agent.reasoning_effort``.

Not ported: magpie moving a billion-context proxy's prefix when its port
changes (``biliToMagpie``): OpenProgram has no such proxy of its own.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

from .base import Agent, Field, Pick, Wiring, Write
from .model_rows import PROVIDER_ID, dotted, fixed, read_doc, write_now

#: Hermes's own User-Agent comes only from a recent release on, so the entry's
#: header names it for the gateway's usage view.
USER_AGENT = "hermes-agent"
NOTICE = "Hermes reads its settings at start-up — restart open Hermes sessions to use this."

#: The names a Hermes profile may have (hermes_constants.PROFILE_ID_RE).
PROFILE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

#: The spellings of an api_mode Hermes takes for its canonical ones
#: (hermes_cli/config_providers.py _API_MODE_ALIASES).
MODE_ALIASES = {
    "openai": "chat_completions", "openai_chat": "chat_completions", "openai-chat": "chat_completions",
    "chat-completions": "chat_completions", "chatcompletions": "chat_completions",
    "responses": "codex_responses", "openai_responses": "codex_responses", "openai-responses": "codex_responses",
    "anthropic": "anthropic_messages", "anthropic-messages": "anthropic_messages", "messages": "anthropic_messages",
    "bedrock": "bedrock_converse", "bedrock-converse": "bedrock_converse",
}
#: A reference Hermes expands as it loads its config: ``${VAR}`` or ``${env:VAR}``.
ENV_REF = re.compile(r"\$\{([^}]+)\}")
SECRET_SOURCE = re.compile(r"^[a-z][a-z0-9_-]*:")


def default_home() -> Path:
    """Hermes's default folder, whatever HERMES_HOME says: the root of its
    profiles (``%LOCALAPPDATA%\\hermes`` on Windows, ``~/.hermes`` elsewhere)."""
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(local) / "hermes"
    return Path.home() / ".hermes"


def home() -> Path:
    env = os.environ.get("HERMES_HOME")
    return Path(env).expanduser() if env else default_home()


def root() -> Path:
    """The folder Hermes's profiles are under (get_default_hermes_root): its
    default folder, which a HERMES_HOME under it is a profile of; a
    HERMES_HOME elsewhere is the root itself, or, as ``<root>/profiles/<name>``,
    a profile of ``<root>``."""
    base = default_home()
    env = os.environ.get("HERMES_HOME")
    if env:
        env_path = Path(env).expanduser()
        try:
            env_path.resolve().relative_to(base.resolve())
        except ValueError:
            base = env_path
            if env_path.parent.name == "profiles":
                base = env_path.parent.parent
    return base


def live(base: Path, name: str) -> bool:
    """A profile of ``base`` Hermes runs: a name it allows, not ``default`` (the
    root's own), not deleted (a tombstone at ``profiles/.deleted/<name>``)."""
    return name != "default" and bool(PROFILE_ID.match(name)) and not (base / "profiles" / ".deleted" / name).exists()


def active() -> Path:
    """The folder of the profile ``hermes`` starts in: HERMES_HOME when it names
    a profile; else the one ``hermes profile use`` made sticky in
    ``<root>/active_profile``; else HERMES_HOME or the default folder
    (hermes_cli/main.py _apply_profile_override)."""
    folder = home()
    if os.environ.get("HERMES_HOME") and folder.parent.name == "profiles":
        return folder
    base = root()
    try:
        name = (base / "active_profile").read_text(encoding="utf-8").lstrip("﻿").strip()
    except OSError:
        return folder
    candidate = base / "profiles" / name
    if not live(base, name) or not candidate.is_dir():
        return folder
    return candidate


def config_path() -> Path:
    return active() / "config.yaml"


def profiles() -> list[Path]:
    """The config.yaml of each of Hermes's profiles (hermes_cli/profiles.py):
    the root's own and ``profiles/<name>``'s for each live name that has one.
    A profile with no config.yaml yet is left out: OpenProgram makes none."""
    base = root()
    paths = [base / "config.yaml"]
    folder = base / "profiles"
    try:
        names = sorted(entry.name for entry in os.scandir(folder) if entry.is_dir())
    except OSError:
        names = []
    for name in names:
        candidate = folder / name / "config.yaml"
        if live(base, name) and candidate.is_file():
            paths.append(candidate)
    return paths


def others(connected: Path) -> list[Path]:
    """Every profile's config but the one at ``connected``'s folder."""
    return [path for path in profiles()
            if os.path.normpath(path.parent) != os.path.normpath(connected) and path.is_file()]


def profile_name(path: Path) -> str:
    """The name ``hermes -p <name>`` gives the profile at ``path``: ``default`` for the root's own."""
    return "default" if os.path.normpath(path.parent) == os.path.normpath(root()) else path.parent.name


_SLUG_RUN = re.compile(r"(?:^|-+)(u[0-9a-f]{4,6}(?:-+u[0-9a-f]{4,6})*)(?:-+|$)")


def unslug(name: str) -> str:
    """A profile id with the ``-uXXXX-`` runs Hermes's slugifyProfileName gives
    each non-ASCII letter or digit turned back into the characters."""
    def back(match: re.Match) -> str:
        out = []
        for token in [t for t in match.group(0).split("-") if t]:
            try:
                char = chr(int(token[1:], 16))
            except (ValueError, OverflowError):
                return match.group(0)
            if ord(char) < 0x80 or not char.isalnum():
                return match.group(0)
            out.append(char)
        return "".join(out)
    return _SLUG_RUN.sub(back, name)


def profile_label(path: Path, name: str) -> str:
    """The name Hermes shows for a profile (profile.yaml's
    ``ui_meta.hermes-bots.title``, then ``display_name``), else its id decoded."""
    doc = read_doc("yaml", path.parent / "profile.yaml")
    title = ((doc.get("ui_meta") or {}).get("hermes-bots") or {}).get("title") if isinstance(doc.get("ui_meta"), dict) else None
    for value in (title, doc.get("display_name")):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return unslug(name)


#: OpenProgram's second entry under ``providers``: the gateway spoken to as
#: Anthropic's Messages, for the models served best there (magpie
#: ``hermesAnthropicID``, #5315). Hermes takes the API from the entry a model
#: is under, never per model, so a Claude model under ``openprogram`` went as
#: chat completions, where Hermes puts no cache marks of its own and its
#: ``prompt_caching.cache_ttl: 1h`` never reached the vendor. On Messages
#: Hermes marks a Claude model's prompt itself, an hour when asked.
ANTHROPIC_ID = PROVIDER_ID + "-anthropic"
OURS = (PROVIDER_ID, ANTHROPIC_ID)
#: Each entry's api_mode as OpenProgram writes it (magpie ``hermesMode``).
WRITTEN_MODE = {PROVIDER_ID: "chat_completions", ANTHROPIC_ID: "anthropic_messages"}
#: The efforts ``agent.reasoning_effort`` takes (Hermes's /reasoning); none
#: turns reasoning off, and unset Hermes asks for medium.
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh")
#: Profiles disconnecting took off OpenProgram's model, for connecting again
#: to put back (magpie ``hermesOffKey``): a stash record of their own.
OFF_RECORD = "hermes#off"


def _providers(doc: dict[str, Any]) -> dict[str, Any]:
    value = doc.get("providers")
    return value if isinstance(value, dict) else {}


def _entry(doc: dict[str, Any], name: str) -> dict[str, Any]:
    value = _providers(doc).get(name)
    return value if isinstance(value, dict) else {}


def mode_in(doc: dict[str, Any], name: str) -> tuple[str, bool]:
    """The api_mode of OpenProgram's entry ``name`` as Hermes reads it
    (api_mode, else transport, canonical), and whether the user set it:
    OpenProgram only ever writes chat_completions into ``openprogram`` and
    anthropic_messages into ``openprogram-anthropic``, so any other value is
    theirs and stays (magpie ``hermesModeIn``)."""
    entry = _entry(doc, name)
    value = str(entry.get("api_mode") or "").strip() or str(entry.get("transport") or "").strip()
    value = MODE_ALIASES.get(value.lower(), value)
    if not value or value == WRITTEN_MODE[name]:
        return WRITTEN_MODE[name], False
    return value, True


def _base(at: Wiring, mode: str) -> str:
    """The gateway's address for an entry on ``mode``: the root for Messages,
    as Anthropic's SDK puts /v1/messages after it, else /v1 (magpie ``hermesBase``)."""
    return at.base("anthropic") if mode == "anthropic_messages" else at.base("openai-chat")


def on_messages(model: dict[str, Any]) -> bool:
    """A catalog model served best on Anthropic's Messages: its provider's own API."""
    return str(model.get("api") or "") == "anthropic-messages"


def split(doc: dict[str, Any], models: list[dict[str, Any]]) -> list[str]:
    """The models that go under the Anthropic entry: those served best on
    Messages, unless the user gave ``openprogram`` an API of their own, which
    then serves every model as they set (magpie ``hermesSplit``)."""
    if mode_in(doc, PROVIDER_ID)[1]:
        return []
    return [m["id"] for m in models if on_messages(m)]


def route(doc: dict[str, Any], models: list[dict[str, Any]], ref: str) -> str:
    """The entry a model of the gateway's goes under (magpie ``hermesRoute``)."""
    return ANTHROPIC_ID if ref in split(doc, models) else PROVIDER_ID


def _loopback_with_path(base: str) -> bool:
    """An http(s) address on this computer with a path (magpie ``loopbackWithPath``)."""
    from urllib.parse import urlparse
    try:
        url = urlparse(base)
    except ValueError:
        return False
    host = (url.hostname or "").lower()
    if url.scheme not in ("http", "https") or host not in ("127.0.0.1", "localhost", "::1") \
            and not host.startswith("127."):
        return False
    return url.path.strip("/") != ""


def prefix(doc: dict[str, Any], at: Wiring) -> str:
    """The local proxy's route OpenProgram's entries reach the gateway behind
    (billion-context's ``http://127.0.0.1:8787/bili/``, :func:`through`), which
    a sync and a pick keep; ``""`` when they go to the gateway itself
    (magpie ``hermesPrefix``)."""
    for name in OURS:
        value = _entry(doc, name).get("base_url")
        if not isinstance(value, str):
            continue
        value = value.strip().rstrip("/")
        base = _base(at, mode_in(doc, name)[0])
        if value.endswith(base):
            head = value[:-len(base)]
            if head and _loopback_with_path(head.rstrip("/")):
                return head
    return ""


def _ref_var(body: str) -> str:
    body = body.strip()
    if body.startswith("env:"):
        return body[4:].strip()
    return "" if SECRET_SOURCE.match(body) else body


def _entry_writes(at: Wiring, path: Path) -> tuple[dict[str, Any], list[str], list[Write], tuple[str, ...]]:
    """OpenProgram's provider entries in the config at ``path``, key by key,
    laid over the ones there (magpie ``hermesEntries``/``hermesKept``):
    ``openprogram``, with every model but those under ``openprogram-anthropic``,
    which is there only while the catalog has a model served best on
    Messages. Hermes writes keys of its own into an entry (``hermes model``
    sets default_model, the dashboard key_env), which stay, as does a name
    and an api_mode the user set; the address follows the api_mode, behind
    a local proxy's prefix when one is there. A key the user keeps as a
    ``${VAR}`` reference stays, and the gateway key goes into the ``.env``
    beside the config under that variable, which Hermes reads over the
    environment. The sets, the deletes, the ``.env`` writes and the entries
    OpenProgram owns."""
    from .model_rows import absent_before, wrote_before
    doc = read_doc("yaml", path)
    behind = prefix(doc, at)
    anthropic = split(doc, at.models)
    sets: dict[str, Any] = {}
    deletes: list[str] = []
    extra: list[Write] = []
    key, var = at.key, ""
    raw = _entry(doc, PROVIDER_ID).get("api_key")
    if isinstance(raw, str):
        match = ENV_REF.fullmatch(raw.strip())
        var = _ref_var(match.group(1)) if match else ""
        if var:
            key = raw
            extra.append(Write(path.parent / ".env", "dotenv", sets={var: at.key}))
    entries = [(PROVIDER_ID, [m["id"] for m in at.models if m["id"] not in anthropic])]
    if anthropic:
        entries.append((ANTHROPIC_ID, anthropic))
    elif _entry(doc, ANTHROPIC_ID) and dotted("providers", ANTHROPIC_ID) in wrote_before("hermes", path):
        # OpenProgram's own entry leaving: recorded as not there before, so
        # disconnecting doesn't put it back
        deletes.extend(absent_before("hermes", path, [dotted("providers", ANTHROPIC_ID)], fmt="yaml"))
    owns = []
    for name, models in entries:
        have = _entry(doc, name)
        mode = mode_in(doc, name)[0]
        headers = have.get("extra_headers") if isinstance(have.get("extra_headers"), dict) else {}
        block: dict[str, Any] = {
            "base_url": behind + _base(at, mode),
            # the key as ``openprogram`` has it: a reference the user keeps there goes into this one too
            "api_key": key,
            "api_mode": mode,
            "extra_headers": {**headers, "User-Agent": USER_AGENT},
            "models": models,
        }
        if not str(have.get("name") or "").strip():
            block["name"] = name
        table = dotted("providers", name)
        sets[table] = block
        owns.append(table)
        if _seeded(have):
            # a bot Hermes made as a copy of a connected config holds
            # OpenProgram's entry, never the user's: it is not put back
            _not_theirs(path, table)
    return sets, deletes, extra, tuple(owns)


def _seeded(entry: dict[str, Any]) -> bool:
    """An entry that is OpenProgram's own, copied: the key is one the gateway gave."""
    return str(entry.get("api_key") or "").startswith("opgw-")


def _not_theirs(path: Path, key: str) -> None:
    """Record ``key`` of ``path`` as not there before OpenProgram, unless a
    first connect recorded it already."""
    from .. import stash
    try:
        stash.remember("hermes", os.path.realpath(path), {key: (False, None)}, fmt="yaml")
    except stash.StashUnreadable:
        pass


#: Hermes's model, which ``hermes model`` rewrites as one pick.
PICK = Pick(("model.provider", "model.default"), "model.provider", {"in": list(OURS)})


#: The key of a profile's model field: ``profile:<name>`` (magpie hermesProfileKey).
PROFILE_KEY = "profile:"


def _model_of(doc: dict[str, Any]) -> dict[str, str]:
    """A config's model: ``provider``, and ``default``, or ``model.model``,
    which Hermes reads with default unset (``key`` says which; magpie ``hermesModelOf``)."""
    model = doc.get("model") if isinstance(doc.get("model"), dict) else {}
    out = {"provider": str(model.get("provider") or ""), "default": str(model.get("default") or ""),
           "key": "model.default"}
    if not out["default"] and model.get("model"):
        out["default"], out["key"] = str(model["model"]), "model.model"
    return out


def _record() -> dict[str, Any]:
    from .. import stash
    try:
        return stash.entry("hermes") or {}
    except stash.StashUnreadable:
        return {}


def _off() -> dict[str, Any]:
    from .. import stash
    try:
        return stash.entry(OFF_RECORD) or {}
    except stash.StashUnreadable:
        return {}


def _wire(at: Wiring) -> list[Write]:
    from .model_rows import wrote_before
    path = config_path()
    doc = read_doc("yaml", path)
    sets, deletes, extra, owns = _entry_writes(at, path)
    writes = [Write(path, "yaml", sets=sets, deletes=deletes, owns=owns, listing=True), *extra]
    if not at.syncing:
        # the pick is the user's to change in Hermes: a sync never writes it
        picks: dict[str, Any] = {"model.provider": route(doc, at.models, at.model), "model.default": at.model}
        dropped: list[str] = []
        effort = at.values.get("effort", "")
        if effort:
            picks["agent.reasoning_effort"] = effort
        elif "agent.reasoning_effort" in wrote_before("hermes", path):
            dropped.append("agent.reasoning_effort")
        writes.append(Write(path, "yaml", sets=picks, deletes=dropped, picks=(PICK,)))
    # the model OpenProgram had set in the connected config, and the one the
    # config is on through it now: a bot still on either follows the pick
    record = _record()
    was = str((record.get("values") or {}).get("model") or "")
    current = _model_of(doc)
    now = current["default"] if current["provider"] in OURS else ""
    off = _off()
    # every other bot is offered the gateway's models too; one picked for
    # on its own row is put on that model, the rest keep their own
    for other in others(path.parent):
        other_doc = read_doc("yaml", other)
        entry, gone, more, owned = _entry_writes(at, other)
        writes.append(Write(other, "yaml", sets=entry, deletes=gone, owns=owned, listing=True))
        writes.extend(more)
        if at.syncing:
            continue
        field = PROFILE_KEY + profile_name(other)
        picked = at.values.get(field, "")
        if picked:
            moved = {"model.provider": route(other_doc, at.models, picked), "model.default": picked}
        else:
            moved = _follow(other, other_doc, at, was, now,
                            str((record.get("values") or {}).get(field) or ""), off.get(str(other)))
        if moved:
            writes.append(Write(other, "yaml", sets=moved,
                                picks=(Pick(tuple(moved), "model.provider", {"in": list(OURS)}),)))
    return writes


def _follow(path: Path, doc: dict[str, Any], at: Wiring, was: str, now: str, picked_before: str,
            off: Any) -> dict[str, Any]:
    """What a bot nobody picked a model for here is moved to.

    One disconnecting took off OpenProgram's model goes back on it while
    nothing has changed it since (magpie ``hermesBack``); one with no model
    at all, which ``hermes`` can't start in, gets the pick, as Hermes would
    seed it. One still on the model OpenProgram had set in the connected
    config (Hermes seeds a new bot with a copy of it and never reads it
    again) follows the new pick (magpie ``hermesFollow``); one on another
    model, OpenProgram's or its own, was set there by its user and stays."""
    ref = at.model
    current = _model_of(doc)
    if isinstance(off, dict) and (off.get("magpie") or {}).get("default"):
        left, before = off.get("left") or {}, off["magpie"]
        if current["provider"] == left.get("provider", "") and current["default"] == left.get("default", ""):
            out = {"model.provider": route(doc, at.models, before["default"]),
                   before.get("key") or "model.default": before["default"]}
            return out
        return {}
    model = doc.get("model")
    if not current["provider"] and not current["default"] and not model:
        return {"model.provider": route(doc, at.models, ref), "model.default": ref}
    if current["provider"] not in OURS:
        return {}
    value = current["default"]
    moved = bool(value) and value != ref and value in (was, now)
    # one picked for this bot on its own row is the user's
    if moved and picked_before != value:
        return {"model.provider": route(doc, at.models, ref), current["key"]: ref}
    return {}


def _on_route(folder: Path, connected: bool, record: dict[str, Any]) -> None:
    """Connected again: the bots put back on OpenProgram's model are no
    longer waiting for it (magpie ``unstash(hermesOffKey)``)."""
    if not connected:
        return
    from .. import stash
    try:
        stash.forget(OFF_RECORD)
    except stash.StashUnreadable:
        pass


def _unwire(record: dict[str, Any]) -> None:
    """What disconnecting does to each other bot first (magpie ``Unwire``,
    ``hermesUnwireProfile``). A bot on one of OpenProgram's entries would be
    left naming a provider that is gone: it goes back to its own model from
    before, when OpenProgram once moved it (its stashed originals), else
    onto the model the user had in the connected config, with that
    provider's entry when the bot lacks it, as Hermes seeds a new bot. With
    no model of the user's anywhere its model goes, and Hermes takes its
    own defaults. What it was on is kept for connecting again (``hermes#off``).
    A bot made while connected, never stashed, is taken off the same way."""
    from .. import stash
    files = record.get("files") or {}
    path = config_path()
    main = read_doc("yaml", path)

    def originals(of: Path) -> dict[str, str] | None:
        """The model ``of`` had before OpenProgram first moved it (``""`` for
        none), ``None`` when OpenProgram never did."""
        kept = (files.get(str(of)) or files.get(os.path.realpath(of)) or {}).get("originals") or {}
        if "model.provider" not in kept and "model.default" not in kept:
            return None
        out = {}
        for key in ("model.provider", "model.default"):
            original = kept.get(key) or {}
            out[key] = str(original.get("value") or "") if original.get("had") else ""
        return out

    connected = _model_of(main)
    if connected["provider"] in OURS:
        was = originals(path) or {"model.provider": "", "model.default": ""}
        own = {"provider": was["model.provider"], "default": was["model.default"]}
    else:
        own = {"provider": connected["provider"], "default": connected["default"]}
    writes: list[Write] = []
    off: dict[str, Any] = {}
    for other in others(path.parent):
        doc = read_doc("yaml", other)
        ours = [dotted("providers", name) for name in OURS if _entry(doc, name)]
        if not ours:
            continue
        current = _model_of(doc)
        if current["provider"] not in OURS:
            writes.append(Write(other, "yaml", deletes=ours))
            continue
        mine = originals(other)
        back = own
        # its own model from before OpenProgram moved it, none when it had
        # none; a bot made on OpenProgram's (Hermes's copy) has none of its own
        if mine is not None and mine["model.provider"] not in OURS:
            back = {"provider": mine["model.provider"], "default": mine["model.default"]}
        sets: dict[str, Any] = {}
        if back["provider"]:
            sets["model.provider"] = back["provider"]
            entry = _providers(main).get(back["provider"])
            if back["provider"] not in _providers(doc) and isinstance(entry, dict):
                sets[dotted("providers", back["provider"])] = entry
        if back["default"]:
            sets["model.default"] = back["default"]
        model = doc.get("model") if isinstance(doc.get("model"), dict) else {}
        gone = [*ours, *(f"model.{key}" for key in ("provider", "default", "model")
                         if key in model and f"model.{key}" not in sets)]
        writes.append(Write(other, "yaml", sets=sets, deletes=gone))
        off[str(other)] = {"magpie": current,
                           "left": {"provider": back["provider"], "default": back["default"]}}
    if off:
        try:
            stash.put(OFF_RECORD, {**(stash.entry(OFF_RECORD) or {}), **off})
        except stash.StashUnreadable:
            pass
    write_now(writes)


def through(prefix_: str) -> None:
    """Point OpenProgram's entries in every Hermes config that has them at the
    gateway through ``prefix_``, the address a local proxy takes the
    gateway's after, ``""`` for the gateway itself (magpie ``HermesThrough``).
    Hermes must be connected already; connecting again records the new
    addresses as OpenProgram's, so a disconnect still takes them out."""
    from .. import edit
    from .engine import AgentConfigError, connect, wiring
    path = config_path()
    values = dict(_record().get("values") or {})
    if not values.get("model") or not _entry(read_doc("yaml", path), PROVIDER_ID):
        raise AgentConfigError("connect Hermes to OpenProgram first: pick one of OpenProgram's models for it")
    at = wiring(agent(), values)
    for config in [path, *others(path.parent)]:
        doc = read_doc("yaml", config)
        sets = {f"{dotted('providers', name)}.base_url": prefix_ + _base(at, mode_in(doc, name)[0])
                for name in OURS if _entry(doc, name)}
        if sets:
            text = edit.ConfigText.read(config)
            text.write(edit.edit("yaml", text.text, sets=sets, where=str(config)))
    connect("hermes", values)


def _profile_fields() -> list[Field]:
    """A model picker for each profile besides the connected one
    (magpie hermesProfileFields), named as ``hermes -p <name>`` names it:
    unset, the profile keeps its own model."""
    fields = []
    for path in others(active()):
        name = profile_name(path)
        fields.append(Field(PROFILE_KEY + name, profile_label(path, name), quiet=True))
    return fields


def _notice() -> str:
    from .running import any_running
    return NOTICE if any_running(r"(^|/)hermes( |$)") else ""


def agent() -> Agent:
    return Agent(
        id="hermes", name="Hermes Agent", icon="hermes", aliases=("hermes-agent",), kind="bot",
        protocol="openai-chat", bin="hermes", dir=active, path=config_path,
        fields=[Field("model", "Model"),
                Field("effort", "Effort", quiet=True, options=fixed(*EFFORTS)),
                *_profile_fields()],
        ua=(USER_AGENT,), wire=_wire, unwire=_unwire, on_route=_on_route, notice=_notice,
    )
