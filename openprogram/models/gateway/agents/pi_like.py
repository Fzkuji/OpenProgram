"""Pi's config format, shared by Pi and the agents that keep it.

After yetone/magpie ``internal/agent/agents.go`` (``piLike``, ``piLikeKeyed``,
``piModelJSON``, ``piModelOn``, ``piPromptCache``, ``modelEffort``,
``piThinkingLevels``, ``piBlockKept``, ``homeDir``), ``pi.go``,
``pi_protocol.go`` (``piProviderOn``), ``pi_api_kept.go``, ``sync.go``
(``theirsKept``, ``mergeTheirs``) and ``internal/gateway/anthropic.go``
(``claudeVersion``, ``adaptiveOnly``, ``AsksAdaptively``,
``ThinksOnlyWhenAsked``) (MIT License, Copyright (c) yetone).

Pi keeps the model a session starts on in its agent folder's
``settings.json`` (``defaultProvider`` and ``defaultModel``) and the
providers of the user's own in ``models.json`` beside it. OpenProgram adds
itself there as the provider ``openprogram``: the gateway's Chat Completions
at ``/v1``, the gateway key, ``compat.sendSessionAffinityHeaders`` (Pi names
its session to a provider only when asked to), and one entry per enabled
model. Each model is asked on the API its own provider speaks natively, so
the gateway relays what Pi sent as it is instead of translating Chat: one on
OpenAI's Responses API is ``api: openai-responses``, one on Anthropic's
Messages API ``anthropic-messages`` at the gateway's Anthropic root (the SDK
adds ``/v1``), one on Gemini's API ``google-generative-ai`` at ``/v1beta``
(left out of the folders of Pi, mu and StepCode, ``drop_gemini``).

Pi's ``enabledModels`` is its Ctrl+P list: while it names anything, a new
session starts on ``defaultModel`` only when the list takes it in, so the
model picked joins the list (:func:`scope_with`), as Pi's own picker does;
none is made where there is none. What else the user put in the provider
block stays through a rewrite (:func:`block_kept`); the model list is
OpenProgram's alone.

The list is the user's too (a ``loose`` key): after a sync, an entry naming
one of OpenProgram's models outright whose model is no longer given leaves
it, as Pi warns on every start about one that names nothing, and comes back
once the model is given again (:func:`scope_synced`, magpie
``scope_gone.go``); a list left with nothing goes and comes back with its
entries, and one the user removed isn't made again.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from .. import edit
from ..edit import ConfigText, ConfigUnreadable, get_path, load
from .base import Agent, Field, Pick, Wiring, Write

PROVIDER_ID = "openprogram"
#: Pi's thinking levels (pi-ai's EXTENDED_THINKING_LEVELS); a scope pattern may end in one.
PI_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")


# Folders

def home_dir(value: str) -> Optional[Path]:
    """The folder an agent's variable names, ``~`` expanded as Pi does;
    ``None`` when it is empty or relative (magpie ``homeDir``)."""
    value = (value or "").strip()
    if value == "~" or value.startswith("~/") or (sys.platform == "win32" and value.startswith("~\\")):
        value = str(Path.home()) + value[1:]
    if not value or not os.path.isabs(value):
        return None
    return Path(os.path.normpath(value))


def env_dir(*names: str) -> Optional[Path]:
    """The first of ``names`` set to an absolute folder (``~`` expanded)."""
    for name in names:
        found = home_dir(os.environ.get(name, ""))
        if found is not None:
            return found
    return None


# Models

# A Claude model's version however a relay spells it (magpie ``claudeVersion``).
_CLAUDE_VERSION = re.compile(
    r"(?:^|[^a-z0-9])(?:claude-)?(?:opus|sonnet|haiku|fable|mythos)-(\d{1,2})(?:[-.](\d{1,2}))?(?:[^0-9]|$)"
    r"|claude-(\d+)(?:[-.](\d))?-(?:opus|sonnet|haiku)")
_ANTHROPIC_MODEL = re.compile(r"(?i)(?:^|[/.:-])claude-")
_GLM_OUTPUT_EFFORT = re.compile(r"(?i)glm-5\.[23](?:$|[-.:/\[])")
_GEMINI_GENERATION = re.compile(r"gemini-(\d+)")


def _claude_version(model: str) -> Optional[tuple[int, int]]:
    found = _CLAUDE_VERSION.search(model.lower())
    if not found:
        return None
    major, minor = (found.group(1), found.group(2)) if found.group(1) else (found.group(3), found.group(4))
    return int(major), int(minor or 0)


def adaptive_only(model: str) -> bool:
    """A Claude from 4.6 on, which thinks only adaptively (magpie ``adaptiveOnly``)."""
    version = _claude_version(model)
    if version is None:
        return False
    major, minor = version
    return major > 4 or (major == 4 and minor >= 6)


def asks_adaptively(model: str) -> bool:
    """A model Pi should not ask for a thinking budget (magpie
    ``AsksAdaptively``): an adaptive-only Claude, GLM-5.2/5.3, Gemini 3 on."""
    lowered = model.lower()
    generation = _GEMINI_GENERATION.search(lowered)
    return adaptive_only(model) or bool(_GLM_OUTPUT_EFFORT.search(model)) or bool(
        generation and int(generation.group(1)) >= 3)


def thinks_only_when_asked(model: str) -> bool:
    """A Claude, which takes thinking disabled whatever its levels (magpie ``ThinksOnlyWhenAsked``)."""
    return bool(_ANTHROPIC_MODEL.search(model)) or _claude_version(model) is not None


def native_api(row: dict[str, Any]) -> str:
    """The gateway API a model's provider serves it on natively: ``gemini``,
    ``openai-responses``, ``anthropic`` or ``""`` for Chat (magpie
    ``catalog.Model.APIs``, read from the model's own ``api``)."""
    api = str(row.get("api") or "")
    if api == "google-generative-ai":
        return "gemini"
    if api in ("openai-responses", "azure-openai-responses", "openai-codex-responses"):
        return "openai-responses"
    if api == "anthropic-messages":
        return "anthropic"
    return ""


def model_effort(levels: list[str], level: str) -> str:
    """The model's own name for ``level``, ``""`` when it has none (magpie ``modelEffort``)."""
    if level in levels:
        return level
    if level == "none" and "off" in levels:
        return "off"
    return ""


def thinking_levels(levels: list[str], claude_off: bool) -> Optional[dict[str, Any]]:
    """Pi's ``thinkingLevelMap`` for a model's levels (magpie
    ``piThinkingLevels``): each of Pi's levels mapped to the model's word for
    it, or null to hide it. Pi builds ``/thinking`` from the map, and a level
    left out is one it offers (all but xhigh and max), so every level is
    named. Off is the model's none; a model without one has off hidden,
    except a Claude on the Messages API (``claude_off``), where Pi's off sends
    thinking disabled, which Claude takes. A model whose levels aren't known
    gets no map: Pi's own defaults."""
    if not levels:
        return None
    out: dict[str, Any] = {}
    for level in PI_LEVELS:
        out[level] = model_effort(levels, "none" if level == "off" else level) or None
    if out["off"] is None and claude_off:
        del out["off"]
    return out


def prompt_cache(api: Any, model_id: str) -> Optional[dict[str, int]]:
    """How long, in seconds, the vendor keeps a model's prompt cache for each
    of Pi's retention tiers, where the gateway relays Pi's request as it is
    (magpie ``piPromptCache``): Claude on Messages 5 minutes or an hour, GPT
    on Responses 5 minutes."""
    name = model_id.rsplit("/", 1)[-1].lower()
    if api == "anthropic-messages" and name.startswith("claude"):
        return {"short": 300, "long": 3600}
    if api == "openai-responses" and name.startswith("gpt"):
        return {"short": 300}
    return None


def max_tokens(row: dict[str, Any]) -> int:
    """A model's reply limit within its window (magpie ``provider.OutputWithin``)."""
    window, output = int(row.get("context_window") or 0), int(row.get("max_tokens") or 0)
    return min(output, window) if window and output else output


def model_entry(row: dict[str, Any], at: Wiring, *, native: bool = True, on: str = "",
                drop_gemini: bool = False) -> dict[str, Any]:
    """One enabled model as an entry of Pi's ``models.json`` (magpie
    ``piModelOn``): ``on`` the api every model is asked on, ``""`` for each
    on its own native API; ``native`` False leaves each on the provider's
    ``openai-completions`` (Pencil)."""
    levels = [str(level) for level in row.get("thinking_levels") or [] if level]
    entry: dict[str, Any] = {"id": row["id"], "name": row.get("name") or row["id"],
                             # reasoning lets Pi offer its thinking levels for the model
                             "reasoning": bool(row.get("reasoning")) or bool([l for l in levels if l not in ("off", "none")])}
    api = native_api(row) if native and not on else ""
    if api == "gemini" and drop_gemini:
        api = ""
    if on == "openai-responses" or api == "openai-responses":
        if api:
            entry["api"] = "openai-responses"
        # strict left out of a tool is taken as a strict-mode schema (#3055)
        entry["compat"] = {"supportsStrictMode": True}
    elif on == "anthropic-messages" or api == "anthropic":
        entry["api"], entry["baseUrl"] = "anthropic-messages", at.base("anthropic")
        if asks_adaptively(row["id"]):
            entry["compat"] = {"forceAdaptiveThinking": True}
    elif api == "gemini":
        entry["api"], entry["baseUrl"] = "google-generative-ai", at.base("gemini") + "/v1beta"
    if row.get("vision"):
        entry["input"] = ["text", "image"]
    asked = entry.get("api") or on or None
    mapped = thinking_levels(levels, asked == "anthropic-messages" and thinks_only_when_asked(row["id"]))
    if mapped is not None:
        if entry.get("api") == "google-generative-ai":
            # Pi resolves a level there to one of Gemini's four and throws
            # before sending on any other name (#3282)
            for level, value in list(mapped.items()):
                if isinstance(value, str) and level != "off" and value.lower() not in ("minimal", "low", "medium", "high"):
                    mapped[level] = None
        entry["thinkingLevelMap"] = mapped
    # without it Pi takes every model for a 128K one and compacts early
    if int(row.get("context_window") or 0) > 0:
        entry["contextWindow"] = int(row["context_window"])
    # without it Pi caps every reply at 16384 tokens
    if max_tokens(row) > 0:
        entry["maxTokens"] = max_tokens(row)
    cache = prompt_cache(asked, row["id"])
    if cache is not None:
        entry["promptCache"] = cache
    return entry


def provider_block(at: Wiring, *, drop_gemini: bool = False, on: str = "") -> dict[str, Any]:
    """OpenProgram's provider in a Pi ``models.json`` (magpie ``piProviderOn``)."""
    models = [model_entry(row, at, on=on, drop_gemini=drop_gemini) for row in at.models]
    # Pi names its session to a provider (x-session-affinity) only when asked
    # to; without it a turn on Chat or Messages reached the gateway with no session
    return {"name": "OpenProgram", "baseUrl": at.base("openai-chat"), "api": on or "openai-completions",
            "apiKey": at.key, "compat": {"sendSessionAffinityHeaders": True}, "models": models}


# What the user put in a file stays

def read_doc(path: Path, fmt: str = "json") -> dict[str, Any]:
    """A config file as data; ``{}`` when it isn't there."""
    text = ConfigText.read(path)
    return load(fmt, text.text, str(path)) if text.exists else {}


def merge_theirs(have: Any, want: Any, whole: tuple[str, ...] = ()) -> Any:
    """``want`` with each key of ``have`` it lacks, objects in both merged
    alike; a key named in ``whole`` is ``want``'s as it is (magpie ``mergeTheirs``)."""
    if not isinstance(have, dict) or not isinstance(want, dict):
        return want
    out = dict(want)
    for key, value in have.items():
        if key not in want:
            out[key] = value
        elif key not in whole and isinstance(value, dict) and isinstance(want[key], dict):
            out[key] = merge_theirs(value, want[key])
    return out


def theirs_kept(doc: dict[str, Any], key: str, want: Any, whole: tuple[str, ...] = ("models",)) -> Any:
    """``want`` laid over what ``doc`` holds at ``key`` (magpie ``theirsKept``)."""
    present, have = get_path(doc, key)
    return merge_theirs(have, want, whole) if present else want


def block_kept(doc: dict[str, Any], block: dict[str, Any], key: str = f"providers.{PROVIDER_ID}", *,
               picked: bool = False) -> Any:
    """OpenProgram's provider block laid over the one in a Pi ``models.json``
    (magpie ``piBlockKept``): a ``sendSessionAffinityHeaders`` already there
    (the user's, or pi-cache-optimizer's) stays as it is, and so does an api
    the user set for the provider, which OpenProgram only ever writes as
    ``openai-completions`` (``pi_api_kept.go``), unless a protocol was
    ``picked`` since, the newer word."""
    present, have = get_path(doc, key)
    if present and isinstance(have, dict):
        compat = have.get("compat")
        if isinstance(compat, dict) and "sendSessionAffinityHeaders" in compat:
            block = dict(block, compat={k: v for k, v in block.get("compat", {}).items()
                                        if k != "sendSessionAffinityHeaders"})
            if not block["compat"]:
                del block["compat"]
        theirs = have.get("api")
        if isinstance(theirs, str) and theirs and not picked and block.get("api") == "openai-completions" \
                and theirs != "openai-completions":
            block = dict(block, api=theirs)
    return theirs_kept(doc, key, block)


# enabledModels (pi.go)

def takes(pattern: str, ref: str) -> bool:
    """Pi's scope pattern surely takes in ``ref``, a provider/model: the same
    reference with a ``:level`` or not, or a glob that ``ref`` or its model id
    fits (magpie ``piTakes``)."""
    glob = any(ch in pattern for ch in "*?[")
    head, sep, tail = pattern.rpartition(":")
    if sep and tail in PI_LEVELS:
        pattern = head
    pattern, ref = pattern.strip().lower(), ref.lower()
    if pattern == ref:
        return True
    if not glob:
        return False
    model = ref.split("/", 1)[1] if "/" in ref else ""
    return any(path_match(pattern, candidate) for candidate in (ref, model))


def path_match(pattern: str, name: str) -> bool:
    """Go's ``path.Match``, which Pi's matching follows: ``*`` and ``?`` stop at a ``/``."""
    out, i = [], 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*":
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        elif ch == "[":
            end = pattern.find("]", i + 1)
            if end < 0:
                return False
            body = pattern[i + 1:end]
            if body.startswith(("^", "!")):
                body = "^" + body[1:]
            out.append("[" + body.replace("\\", "\\\\") + "]")
            i = end
        else:
            out.append(re.escape(ch))
        i += 1
    try:
        return re.fullmatch("".join(out), name) is not None
    except re.error:
        return False


def scope(doc: dict[str, Any], key: str = "enabledModels") -> Optional[list[str]]:
    """``enabledModels``; ``None`` when missing, empty or not a list of strings."""
    value = doc.get(key)
    if not isinstance(value, list) or not value or not all(isinstance(v, str) for v in value):
        return None
    return value


def scope_with(doc: dict[str, Any], ref: str, key: str = "enabledModels") -> Optional[list[str]]:
    """``enabledModels`` with ``ref`` added, when there is such a list and
    nothing in it takes ``ref`` in; ``None`` for no change (magpie ``piScopeWith``)."""
    pats = scope(doc, key)
    if pats is None or any(takes(p, ref) for p in pats):
        return None
    return [*pats, ref]


# enabledModels after a sync (scope_gone.go)

def pi_bare(pattern: str) -> str:
    """A Pi entry without its thinking level (magpie ``piBare``)."""
    head, sep, tail = pattern.rpartition(":")
    return head if sep and tail in PI_LEVELS else pattern


def scope_one(pattern: str, bare: Callable[[str], str]) -> str:
    """The model an entry names outright on OpenProgram, lower case and its
    level off; ``""`` for a glob or an entry on another provider (magpie
    ``scopeMagpieOne``)."""
    one = bare(pattern.strip()).lower()
    if any(ch in one for ch in "*?[") or not one.startswith(PROVIDER_ID + "/"):
        return ""
    return one


def offered_set(ids: list[str]) -> Optional[set[str]]:
    """The models given, as ``openprogram/<id>`` lower case; ``None`` for none,
    which says nothing about what is given (magpie ``offeredSet``)."""
    out = {f"{PROVIDER_ID}/{i.strip()}".lower() for i in ids if isinstance(i, str) and i.strip()}
    return out or None


def scope_synced(cur: list[str], has: bool, offered: set[str], memory: dict[str, Any],
                 bare: Callable[[str], str] = pi_bare) -> tuple[list[str], bool, dict[str, Any], bool]:
    """An ``enabledModels`` list (``has`` False when there is none) caught up
    with the models given (magpie ``scopeSynced``): an entry on a model no
    longer given leaves and is remembered, one remembered goes back on the
    end once its model is given again. ``memory`` is what was remembered
    (``entries``, and ``emptied`` when the list went for it); the result is
    the list, whether there is one, what to remember, whether it changed."""
    entries = [e for e in memory.get("entries") or [] if isinstance(e, str)]
    if not has and not memory.get("emptied"):
        # no list, or one the user removed: nothing to take out or put back
        return cur, has, {}, False
    out: list[str] = []
    nxt: list[str] = []
    listed: set[str] = set()
    for pattern in cur:
        one = scope_one(pattern, bare)
        if one and one not in offered:
            if pattern not in out:
                out.append(pattern)
            continue
        nxt.append(pattern)
        if one:
            listed.add(one)
    for pattern in entries:
        one = scope_one(pattern, bare)
        if not one or one in listed and one in offered:
            continue
        if one in offered:
            nxt.append(pattern)
            listed.add(one)
        elif pattern not in out:
            out.append(pattern)
    next_has = bool(nxt)
    remembered: dict[str, Any] = {"entries": out} if out else {}
    if not next_has and out:
        remembered["emptied"] = True
    return nxt, next_has, remembered, next_has != has or nxt != cur


def scope_writes(doc: dict[str, Any], given: list[str], ref: str, at: Wiring, key: str = "enabledModels",
                 bare: Callable[[str], str] = pi_bare) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    """``enabledModels`` caught up with the models ``given`` (:func:`scope_synced`,
    what it remembers kept in the stash record as ``scope_gone``), as the sets
    and deletes of the synced write, and taking in the model picked ``ref``
    (:func:`scope_with`), as the sets of the write a pick makes."""
    sets: dict[str, Any] = {}
    deletes: list[str] = []
    value = doc.get(key)
    has = key in doc
    offered = offered_set(given)
    cur: list[str] = value if isinstance(value, list) and all(isinstance(v, str) for v in value) else []
    if offered is not None and (not has or cur):
        nxt, next_has, remembered, changed = scope_synced(cur, has, offered, at.previous.get("scope_gone") or {}, bare)
        at.record["scope_gone"] = remembered
        if changed:
            if next_has:
                sets[key] = nxt
            else:
                deletes.append(key)
            # a list made again takes the model picked while there was none
            if not has and next_has and ref:
                with_ref = scope_with({key: nxt}, ref, key)
                if with_ref is not None:
                    sets[key] = with_ref
            doc = {**doc, key: sets[key]} if next_has else {k: v for k, v in doc.items() if k != key}
    picked: dict[str, Any] = {}
    if ref:
        # a model outside the user's Ctrl+P list would never start
        listed = scope_with(doc, ref, key)
        if listed is not None:
            picked[key] = listed
    return sets, deletes, picked


def scope_without(path: Path, fmt: str = "json", key: str = "enabledModels", every: str = "") -> None:
    """OpenProgram's models out of ``enabledModels`` once its provider is gone,
    where they would name nothing and Pi warns of each on every start (magpie
    ``piScopeWithout``, omp's ``ompScopeWithout``): every entry naming one of
    them outright, ``every`` too (omp's ``openprogram/**``); a glob of the
    user's and anything that isn't a plain entry stay. A list left with
    nothing goes, never written empty."""
    text = ConfigText.read(path)
    if not text.exists:
        return
    doc = load(fmt, text.text, str(path))
    value = doc.get(key) if isinstance(doc, dict) else None
    if not isinstance(value, list) or not value:
        return

    def ours(entry: Any) -> bool:
        if not isinstance(entry, str):
            return False
        bare = entry.strip().lower()
        if every and bare == every.lower():
            return True
        return not any(ch in bare for ch in "*?[") and bare.startswith(PROVIDER_ID + "/")

    kept = [entry for entry in value if not ours(entry)]
    if len(kept) == len(value):
        return
    if kept:
        new = edit.edit(fmt, text.text, sets={key: kept}, where=str(path))
    else:
        new = edit.edit(fmt, text.text, deletes=[key], where=str(path))
    if new != text.text:
        text.write(new)


#: Pi's model a session starts on, picked again in Pi's own /model as one.
PICK = Pick(("defaultProvider", "defaultModel", "defaultThinkingLevel"), "defaultProvider", PROVIDER_ID)


def level_options() -> list[tuple[str, str]]:
    """Pi's thinking levels, as its /thinking offers them; Pi clamps one to
    what the model takes (magpie ``piLevels``)."""
    return [(level, level) for level in PI_LEVELS]


#: The APIs of pi-ai's the gateway serves, as Pi's protocol picker lists them
#: (magpie ``piProtocolNames``); unpicked, each model is asked on its own.
PROTOCOLS = (("openai-completions", "OpenAI Chat Completions"), ("openai-responses", "OpenAI Responses"),
             ("anthropic-messages", "Anthropic Messages"))


def protocol_of(at: Wiring) -> str:
    picked = at.values.get("protocol", "")
    return picked if picked in dict(PROTOCOLS) else ""


# The agent

def pi_like(agent_id: str, name: str, folder: Callable[[], Path], *, named_key: bool = True,
            drop_gemini: bool = False, protocol: bool = False, **extra: Any) -> Agent:
    """An agent that keeps Pi's ``settings.json`` and ``models.json`` in
    ``folder`` (magpie ``piLike``). The key written is one that names the
    agent (magpie ``piLikeKeyed``), never the gateway key itself: models.json
    is a file others may read. Its thinking level is
    ``defaultThinkingLevel``; ``protocol`` offers the API every model is
    asked on (Pi's, magpie ``piProtocolField``)."""
    settings = lambda: folder() / "settings.json"  # noqa: E731
    models = lambda: folder() / "models.json"  # noqa: E731

    def wire(at: Wiring) -> list[Write]:
        on = protocol_of(at) if protocol else ""
        block = block_kept(read_doc(models()), provider_block(at, drop_gemini=drop_gemini, on=on), picked=bool(on))
        given = [m.get("id", "") for m in block.get("models") or [] if isinstance(m, dict)]
        synced, gone, picked = scope_writes(read_doc(settings()), given, f"{PROVIDER_ID}/{at.model}", at)
        picked.update({"defaultProvider": PROVIDER_ID, "defaultModel": at.model})
        if at.values.get("effort"):
            picked["defaultThinkingLevel"] = at.values["effort"]
        # the synced list is rewritten alone when the catalog changes; the
        # pick is written over it on a connect. The provider is OpenProgram's
        # whole: disconnecting takes it out however the user edited it since
        # (magpie DelJSON providers.magpie)
        key = f"providers.{PROVIDER_ID}"
        return [Write(models(), "json", sets={key: block}, owns=(key,), listing=True),
                Write(settings(), "json", sets=synced, deletes=gone, listing=True),
                Write(settings(), "json", sets=picked, picks=(PICK,))]

    extra.setdefault("icon", agent_id)
    extra.setdefault("bin", agent_id)
    fields = [Field("model", "Model"), Field("effort", "Thinking", quiet=True, options=level_options)]
    if protocol:
        fields.append(Field("protocol", "Protocol", quiet=True, options=lambda: list(PROTOCOLS)))
    return Agent(id=agent_id, name=name, kind=extra.pop("kind", "cli"), protocol="openai-chat",
                 dir=folder, path=settings, fields=fields, wire=wire,
                 named_key=named_key, before_copy=True, loose=("enabledModels",),
                 unwired=lambda record: unwired(models(), settings()), **extra)


def unwired(models: Path, settings: Path) -> None:
    """After a disconnect, ``enabledModels`` loses OpenProgram's entries once
    its provider is gone from ``models`` (one of the user's own of that name,
    put back, keeps them)."""
    if not get_path(read_doc(models), f"providers.{PROVIDER_ID}")[0]:
        scope_without(settings, "json" if settings.suffix == ".json" else "toml")
