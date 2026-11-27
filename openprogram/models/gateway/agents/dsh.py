"""DeepSeek Harness, ``dsh`` (``$DSH_HOME``, default ``~/.dsh``).

After yetone/magpie ``internal/agent/dsh.go`` (MIT License, Copyright (c)
yetone) and ``dsh_search.go``, without its WSL homes (the registry has no
WSL places). dsh's own models, as dsh itself lists them, are offered on the
model field beside the gateway's (``Field.with_catalog``). This is the user's own dsh put on
OpenProgram's gateway; OpenProgram's own chats run dsh separately
(``runtime/harnesses/deepseek``, harness ``deepseek-harness``), on the
user's own sign-in and models: that client hands its dsh the user's own
setup, never the gateway's (``runtime/harnesses/deepseek/client.py``).

The web-search entry (``web-search-deepseek`` pointed at the gateway, and
the ``web`` row naming it) asks each search as one Messages request with
Anthropic's ``web_search_20250305`` server tool, which the gateway answers
(``models/gateway/web_search.py``).

A dsh before profiles (before 0.0.1-rc.1, ``config.yaml``) is wired as
magpie wires it: its ``llm-deepseek`` row taken over, ``agent-loop`` and
``api-gateway`` on the picked model.

dsh boots from its shipped rows and then applies a personal patch list: a
YAML list of ``{id, config}`` entries, each replacing the whole config of the
row with that id, one per profile at ``profiles/<name>/cordis.patch.yml``
(web and desktop read it live), with ``$DSH_HOME/cordis.patch.yml`` applied
over every profile's.

OpenProgram is one of dsh's custom model providers: routes named
``openprogram`` (Chat Completions), ``openprogram-responses`` and
``openprogram-anthropic`` in the ``llm-pi-ai`` entry's providers (what dsh's
Models page writes for "Add model provider"), each with the gateway as its
endpoint, the models served natively on that API, and as the key a credential
named in ``apiKeyEnv``, which is put in ``$DSH_HOME/.env`` and dsh's own key
store ``.credentials.yaml`` (the desktop app reads only the store).
``agent-default-model`` names the model new sessions start on. dsh's own
DeepSeek row (``llm-deepseek``) is left to dsh, and so are the user's routes
in the same entry. Entries OpenProgram writes whole are marked ``#
openprogram`` on their id line; one of the user's it replaces is kept and put
back. ``settings.yaml``'s ``llm-pi-ai`` routes are written too: deepseek-acp
(dsh in an editor) reads nothing else.

The patch lists are written whole (a top-level YAML list the key editors
cannot address); a copy is taken first, put back on disconnect when nothing
else changed the file, and otherwise OpenProgram's routes and start are taken
out of it as it now is (:func:`unwire`, the agent's ``unwire``).
"""
from __future__ import annotations

import copy
import functools
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

from .base import Agent, App, Field, Wiring, Write, app_bundles, find_tool
from .model_rows import PROVIDER_ID, PROVIDER_NAME, max_tokens, name as model_name, native_api, window

#: The mark on the id line of an entry OpenProgram wrote (magpie ``dshMark``).
MARK = "# openprogram"
#: The credential dsh signs gateway requests with (magpie ``dshKeyRef``).
KEY_REF = "OPENPROGRAM_GATEWAY_KEY"
#: The row custom providers are configured on, and its plugin (magpie ``dshPiRow``).
PI_ROW = "llm-pi-ai"
PI_PLUGIN = "@deepseek-ai/dsh-llm-pi-ai"
START_ROW = "agent-default-model"

#: The patch list dsh writes for a new profile (PROFILE_PATCH_TEMPLATE in
#: DeepSeek Harness Desktop 0.2.0-rc.2's lib/main.js, initProfile).
PROFILE_TEMPLATE = ("# Your patch layer for this dsh profile, applied after every bundle layer:\n"
                    "# a top-level YAML array of loader patch entries (id-targeted config\n"
                    "# overrides, disables, and insert lists; `!!js` expressions allowed).\n"
                    "[]\n")

#: How long llm-pi-ai waits on the route for the next event of a reply, in ms:
#: its own 5 minutes counts no keepalive comment, and cut turns queued behind
#: a slow provider (magpie ``dshIdleWait``). A value already on the route stays.
IDLE_WAIT = 600000
IDLE_KEY = "streamIdleTimeoutMs"


@dataclass(frozen=True)
class Kind:
    """One of OpenProgram's routes in llm-pi-ai (magpie ``dshKind``)."""

    key: str
    api: str
    name: str


#: llm-pi-ai's name for Google's Generative Language API, served by the
#: gateway at /gemini/v1beta (magpie ``dshGeminiAPI``).
GEMINI_API = "google-generative-ai"
#: The provider APIs that are Google's generateContent.
GEMINI_APIS = (GEMINI_API, "google-vertex")
#: The first dsh whose llm-pi-ai serves Gemini's API; an older one refuses a
#: route naming it with its whole config (magpie ``dshGeminiSince``).
GEMINI_SINCE = "0.2.2-rc.2"
#: The first dsh whose patch lists are its profiles' (magpie ``dshProfilesSince``).
PROFILES_SINCE = "0.0.1-rc.1"

#: One route per API a model is best asked on: llm-pi-ai sets the api for a
#: route as a whole, and a model translated from Chat loses its sealed
#: reasoning between tool calls (magpie ``dshKinds``). Gemini's is written
#: only for a dsh that reads it (:func:`speaks_gemini`); for any other a
#: Gemini model goes on the Chat route (:func:`asked`).
KINDS = (
    Kind(PROVIDER_ID, "openai-completions", PROVIDER_NAME),
    Kind(PROVIDER_ID + "-responses", "openai-responses", PROVIDER_NAME + " · Responses"),
    Kind(PROVIDER_ID + "-anthropic", "anthropic-messages", PROVIDER_NAME + " · Anthropic"),
    Kind(PROVIDER_ID + "-gemini", GEMINI_API, PROVIDER_NAME + " · Gemini"),
)
#: Every route key of OpenProgram's, taken out on disconnect.
ROUTE_KEYS = tuple(kind.key for kind in KINDS)
#: The wire protocols of llm-pi-ai a route may speak, the gateway serving
#: each; Google's is kept only on the Gemini route (magpie ``dshAPIs``).
APIS = ("openai-completions", "openai-responses", "anthropic-messages")
#: dsh's thinking levels (pi-ai's), in its order.
THINKING = ("off", "minimal", "low", "medium", "high", "xhigh", "max")
#: The efforts dsh's own DeepSeek row takes, for a dsh before profiles
#: (magpie ``dshEfforts``).
OWN_EFFORTS = ("off", "low", "high", "max")
#: compaction-basic's headroomTokens (magpie ``dshHeadroom``).
HEADROOM = 65536

APP = App("com.deepseek.dsh", ("DeepSeek Harness.app",))


#: The dsh the desktop app ships (also runtime/harnesses/deepseek/client.py's).
APP_LAUNCHER = Path("/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh")


def home() -> Path:
    """``$DSH_HOME``, else ``~/.dsh`` (the home OpenProgram's own dsh chats use too)."""
    return Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh").expanduser()


# Versions (magpie Newer, dshVersion, dshAppVersion, dshSpeaksGemini)

_VERSION = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?")


def newer(a: str, b: str) -> bool:
    """Version ``a`` is after ``b``, pre-releases before their release (magpie ``Newer``)."""
    a, b = a.strip().removeprefix("v"), b.strip().removeprefix("v")
    an, _, apre = a.partition("-")
    bn, _, bpre = b.partition("-")
    x, y = an.split("."), bn.split(".")
    for i in range(max(len(x), len(y))):
        p = int(x[i]) if i < len(x) and x[i].isdigit() else 0
        q = int(y[i]) if i < len(y) and y[i].isdigit() else 0
        if p != q:
            return p > q
    if apre == bpre:
        return False
    if not apre:
        return True
    if not bpre:
        return False
    xs, ys = apre.split("."), bpre.split(".")
    for p, q in zip(xs, ys):
        if p.isdigit() and q.isdigit():
            if int(p) != int(q):
                return int(p) > int(q)
        elif p != q:
            return p > q
    return len(xs) > len(ys)


@functools.lru_cache(maxsize=8)
def _probe(path: str, size: int, mtime: float) -> str:
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    found = _VERSION.search(out or "")
    return found.group(0) if found else ""


def dsh_version() -> str:
    """The version of the dsh CLI here, ``""`` when there is none or it
    didn't say; probed once per binary (magpie ``dshVersion``)."""
    found = find_tool("dsh")
    if not found:
        return ""
    try:
        resolved = os.path.realpath(found)
        stat = os.stat(resolved)
    except OSError:
        return ""
    return _probe(resolved, stat.st_size, stat.st_mtime)


def app_version() -> str:
    """The version of DeepSeek Harness Desktop, the oldest of those installed;
    ``""`` when there is none, ``"unknown"`` when one doesn't say (magpie
    ``dshAppVersion``)."""
    import plistlib
    oldest = ""
    for bundle in app_bundles(APP):
        try:
            with open(bundle / "Contents" / "Info.plist", "rb") as handle:
                version = str(plistlib.load(handle).get("CFBundleShortVersionString") or "")
        except (OSError, ValueError, plistlib.InvalidFileException):
            version = ""
        if not version:
            return "unknown"
        if not oldest or newer(oldest, version):
            oldest = version
    return oldest


def speaks_gemini() -> bool:
    """Every dsh reading ``~/.dsh`` here (the CLI and the desktop app, each
    where installed) takes a route on Gemini's API; a version not known is
    taken as too old, as a route an old dsh can't read fails its whole
    config (magpie ``dshSpeaksGemini``)."""
    versions = [v for v in (dsh_version(), app_version()) if v]
    return bool(versions) and all(v != "unknown" and not newer(GEMINI_SINCE, v) for v in versions)


def asked(models: list[dict[str, Any]], gemini: bool) -> list[dict[str, Any]]:
    """The models as a dsh is given them: a Gemini on Chat where that dsh
    can't speak Gemini's API, as the gateway translates it (magpie ``dshAsked``)."""
    if gemini:
        return models
    return [{**m, "api": "openai-completions"} if native_api(m) in GEMINI_APIS else m for m in models]


# The patch list (magpie dshParse / dshWrite)

class _Tagged(str):
    """A ``!!js`` (or other tagged) value: read, never written back."""


class _Loader(yaml.SafeLoader):
    pass


_Loader.add_multi_constructor("!", lambda loader, suffix, node: _Tagged(getattr(node, "value", "")))
_Loader.add_multi_constructor("tag:yaml.org,2002:js", lambda loader, suffix, node: _Tagged(getattr(node, "value", "")))

_ID_LINE = re.compile(r"""^(?:- |  )id:\s*['"]?([^'"#\s]+)['"]?\s*(#.*)?$""")


class PatchUnreadable(Exception):
    pass


@dataclass
class Item:
    """One entry of a patch list, as its lines."""

    id: str = ""
    ours: bool = False
    lines: list[str] = field(default_factory=list)

    def node(self) -> dict[str, Any]:
        try:
            doc = yaml.load("\n".join(self.lines), Loader=_Loader)  # noqa: S506 - SafeLoader subclass
        except yaml.YAMLError as exc:
            raise PatchUnreadable(f"the {self.id} entry is not YAML: {exc}") from exc
        if not isinstance(doc, list) or len(doc) != 1 or not isinstance(doc[0], dict):
            raise PatchUnreadable(f"the {self.id} entry is not one OpenProgram can edit")
        return doc[0]

    def config(self) -> dict[str, Any]:
        try:
            value = self.node().get("config")
        except PatchUnreadable:
            return {}
        return value if isinstance(value, dict) else {}


def _tagged(value: Any) -> bool:
    if isinstance(value, _Tagged):
        return True
    if isinstance(value, dict):
        return any(_tagged(v) for v in value.values())
    if isinstance(value, list):
        return any(_tagged(v) for v in value)
    return False


def item_of(node: dict[str, Any], ours: bool) -> Item:
    """``node`` written as a patch list entry, marked when it is OpenProgram's."""
    if _tagged(node):
        raise PatchUnreadable(f"the {node.get('id')} entry holds a !!js value OpenProgram cannot write back")
    text = yaml.safe_dump([node], sort_keys=False, allow_unicode=True, default_flow_style=False, width=4096)
    lines = text.rstrip("\n").split("\n")
    if ours:
        lines[0] += " " + MARK
    return Item(str(node.get("id") or ""), ours, lines)


def parse(text: str, where: str = "patch list") -> tuple[list[str], list[Item]]:
    """What comes before the first entry, and the entries. A profile's ``[]``
    is read as an empty list; a file that is no list is refused."""
    from ..edit.mark import is_note
    head: list[str] = []
    items: list[Item] = []
    opened = False
    for line in text.replace("\r\n", "\n").rstrip("\n").split("\n") if text.strip() else []:
        if is_note(line):  # the notes are put back as the file is written
            continue
        stripped = line.strip()
        if line.startswith("- ") or line == "-":
            items.append(Item(lines=[line]))
        elif not items:
            # "---" may open a patch list, once, and stays in the head
            marker = line.rstrip() == "---" and not opened
            if stripped and not stripped.startswith("#") and stripped != "[]" and not marker:
                raise PatchUnreadable(f"{where} is not a list of entries OpenProgram can edit")
            if stripped and not stripped.startswith("#"):
                opened = True
            if stripped != "[]":
                head.append(line)
            continue
        elif stripped and not stripped.startswith("#") and not line.startswith(" "):
            raise PatchUnreadable(f"{where} is not a list of entries OpenProgram can edit")
        else:
            items[-1].lines.append(line)
        last = items[-1]
        match = _ID_LINE.match(line)
        if match and not last.id:
            last.id = match.group(1)
            last.ours = (match.group(2) or "").strip() == MARK
    return head, items


def join(head: list[str], items: list[Item]) -> str:
    out = list(head)
    for item in items:
        out.extend(item.lines)
    if not items:
        out.append("[]")  # dsh wants a list, even an empty one
    return "\n".join(out) + "\n"


def find(items: list[Item], row: str) -> int:
    return next((i for i, item in enumerate(items) if item.id == row), -1)


def find_last(items: list[Item], row: str) -> int:
    return next((i for i in range(len(items) - 1, -1, -1) if items[i].id == row), -1)


def config_index(items: list[Item], row: str) -> int:
    """The last entry supplying this row's config (magpie ``dshConfigIndex``)."""
    for i in range(len(items) - 1, -1, -1):
        if items[i].id == row:
            try:
                if "config" in items[i].node():
                    return i
            except PatchUnreadable:
                continue
    return -1


def is_route(key: str) -> bool:
    return key in ROUTE_KEYS


def ours(item: Item) -> bool:
    """An entry OpenProgram wrote: marked so, or a start on its route should
    dsh rewrite it without the mark (magpie ``dshOurs``)."""
    return item.ours or item.id == START_ROW and is_route(str(item.config().get("provider") or ""))


# The routes (magpie dshPutRoute, dshPutProviders, dshKindConfig)

def route_of(model: dict[str, Any]) -> str:
    """The route a model goes on: the one for the API its provider serves it on."""
    api = native_api(model)
    if api in GEMINI_APIS:
        return KINDS[3].key
    if api.endswith("responses"):
        return KINDS[1].key
    if api == "anthropic-messages":
        return KINDS[2].key
    return KINDS[0].key


def kind_of(key: str) -> Kind:
    return next((kind for kind in KINDS if kind.key == key), KINDS[0])


def own_pick(ref: str) -> bool:
    """A pick of one of dsh's own models, ``provider:model`` as dsh names it
    (``deepseek-official:deepseek-v4-flash``), not one of OpenProgram's: dsh
    asks for it itself, on its own key (magpie's ``dshModels``, ``Direct``)."""
    return ":" in ref and "/" not in ref


def own_options() -> list[tuple[str, str]]:
    """dsh's own models to pick beside OpenProgram's, as dsh itself lists
    them (magpie dsh's model ``Options``): read by the runtime from a session
    of dsh's (``runtime/harnesses/models.py``), handed down through
    :func:`lifecycle.provide_own_models`. One picked that the listing doesn't
    have now stays offered."""
    from .base import picked
    from .lifecycle import own_models_for
    from .model_rows import unprefixed
    options = [(str(m["id"]), str(m.get("label") or m["id"]))
               for m in own_models_for("dsh") if own_pick(str(m["id"]))]
    current = unprefixed(picked("dsh").get("model") or "")
    if own_pick(current) and not any(value == current for value, _label in options):
        options.insert(0, (current, current.partition(":")[2]))
    return options


def route_for(ref: str, models: list[dict[str, Any]]) -> str:
    return next((route_of(m) for m in models if m.get("id") == ref), KINDS[0].key)


def dsh_max_tokens(model: dict[str, Any]) -> int:
    """The reply limit listed: dsh reserves it, with 65536 of headroom, out of
    the window before it compacts, so a vendor's figure near its window left
    dsh no room; it is held to half the window less the headroom, or a
    quarter of it where that is more (magpie ``dshMaxTokens``)."""
    output = max_tokens(model)
    context = window(model)
    if not output or not context:
        return output
    room = max(context // 4, context // 2 - HEADROOM)
    return min(output, max(1, room))


def levels(model: dict[str, Any], api: str = "") -> dict[str, str]:
    """Each of dsh's thinking levels the model takes on a route speaking
    ``api``, with the effort sent for it (magpie ``dshLevelsOn``); none for
    a model offering no level but off. On Gemini's API only the levels
    Google takes as a thinking level stay: pi-ai's Google adapter refuses
    any other before it sends (magpie ``dshGeminiLevels``)."""
    own = [str(level) for level in (model.get("thinking_levels") or []) if level]
    if not own and model.get("reasoning"):
        own = ["low", "medium", "high"]
    out = {}
    for level in THINKING:
        if level == "off":
            if "none" in own or "off" in own:
                out["off"] = "none"
        elif level in own:
            out[level] = level
    if api == GEMINI_API:
        out = {level: effort for level, effort in out.items()
               if level == "off" or effort.lower() in ("minimal", "low", "medium", "high")}
    return out if set(out) - {"off"} else {}


def levels_at(model: dict[str, Any]) -> dict[str, str]:
    """The levels dsh offers for a model (already :func:`asked`) on the route it goes on."""
    return levels(model, kind_of(route_of(model)).api) if model else {}


_CLAUDE = re.compile(r"claude-(?:[a-z]+-)?(\d+)(?:[-.](\d{1,2})(?!\d))?")
_GLM_EFFORT = re.compile(r"glm-5\.[23](?:$|[-.:/\[])", re.I)
_GEMINI_GEN = re.compile(r"(?:^|/)gemini-(\d+)")


def asks_adaptively(model_id: str) -> bool:
    """A model pi-ai asks on Anthropic's Messages API with
    ``forceAdaptiveThinking``: a Claude from 4.6 on, which turns a budget
    away; Z.ai's GLM-5.2 and 5.3; a Gemini 3 or later. pi-ai's budget path
    clamps xhigh and max to high (magpie ``gateway.AsksAdaptively``)."""
    lowered = model_id.lower()
    found = _CLAUDE.search(lowered)
    if found:
        major, minor = int(found.group(1)), int(found.group(2) or 0)
        if major > 4 or major == 4 and minor >= 6:
            return True
    if _GLM_EFFORT.search(lowered):
        return True
    gen = _GEMINI_GEN.search(lowered)
    return bool(gen) and int(gen.group(1)) >= 3


def base_url(api: str, at: Wiring) -> str:
    """The gateway's address for a route on ``api``: pi-ai hands it to the
    vendor's SDK, Anthropic's adding /v1/messages itself, OpenAI's only
    /chat/completions or /responses, Google's /models/<id>:… with no
    version of its own (magpie ``dshBaseURL``)."""
    if api == "anthropic-messages":
        return at.base("anthropic")
    if api == GEMINI_API:
        return at.base("gemini") + "/v1beta"
    return at.base("openai-chat")


#: The stash record of the reply limits OpenProgram wanted in dsh's routes
#: (magpie ``outputsWantedKey("dsh:routes")``), one for every file dsh keeps them in.
OUTPUTS_RECORD = "dsh#outputs"
#: How many values a model's record keeps (magpie ``outputsRemembered``).
OUTPUTS_REMEMBERED = 8


def outputs_kept(want: dict[str, int], have: dict[str, int]) -> dict[str, int]:
    """``want``, the reply limit OpenProgram hands each model, with ``have``'s,
    the file's own, kept where it is the user's: a value OpenProgram never
    wanted for that model, typed into dsh's Models page or by hand. Every
    value wanted is recorded (magpie ``outputsKept``)."""
    from .. import stash
    try:
        record = dict((stash.entry(OUTPUTS_RECORD) or {}).get("wanted") or {})
    except stash.StashUnreadable:
        return dict(want)
    out: dict[str, int] = {}
    after: dict[str, list[int]] = {}
    for ref, wanted in want.items():
        out[ref] = wanted
        was = [int(v) for v in record.get(ref) or [] if isinstance(v, int)]
        found = have.get(ref) or 0
        if ref in record and found > 0 and found != wanted and found not in was:
            out[ref] = found
        if wanted <= 0 or wanted in was:
            if was:
                after[ref] = was
            continue
        after[ref] = (was + [wanted])[-OUTPUTS_REMEMBERED:]
    if after != record:
        try:
            stash.put(OUTPUTS_RECORD, {"wanted": after} if after else None)
        except stash.StashUnreadable:
            pass
    return out


def _outputs(route: dict[str, Any], cur: Any) -> None:
    """A maxTokens the user typed into one of OpenProgram's models on the
    route stays (magpie ``dshOutputsKept``)."""
    want = {m["id"]: m["maxTokens"] for m in route["models"] if m.get("maxTokens")}
    if not want:
        return
    have = {}
    for model in (cur or {}).get("models") or [] if isinstance(cur, dict) else []:
        if isinstance(model, dict) and isinstance(model.get("maxTokens"), int):
            have[str(model.get("id"))] = model["maxTokens"]
    kept = outputs_kept(want, have)
    for model in route["models"]:
        if kept.get(model["id"], 0) > 0:
            model["maxTokens"] = kept[model["id"]]


def kind_config(kind: Kind, models: list[dict[str, Any]], api: str, at: Wiring) -> dict[str, Any]:
    """OpenProgram's route ``kind`` for ``models``. ``api`` is the protocol the
    route has now, kept when the gateway speaks it: one the user switched to
    in dsh's Models page stays; Google's only on the Gemini route (magpie
    ``dshAPI``)."""
    api = api if api in APIS or api == kind.api else kind.api
    rows = []
    for model in models:
        # without contextWindow and maxTokens dsh takes every model for a
        # 128K one writing 16K at most; without input for a text-only one
        row: dict[str, Any] = {"id": model["id"], "name": model_name(model)}
        if window(model):
            row["contextWindow"] = window(model)
        if dsh_max_tokens(model):
            row["maxTokens"] = dsh_max_tokens(model)
        if model.get("vision"):
            row["input"] = ["text", "image"]
        if levels(model, api):
            row["reasoningEfforts"] = levels(model, api)
        if api == "anthropic-messages" and asks_adaptively(str(model["id"]).partition("/")[2] or str(model["id"])):
            # asked for a budget, pi-ai clamps xhigh and max to high, and a
            # Claude thinking only adaptively turns a budget away
            row["compat"] = {"forceAdaptiveThinking": True}
        elif api == "openai-responses":
            # with strict left out the Responses API takes every property
            # of a tool for required, and optional ones come back filled
            row["compat"] = {"supportsStrictMode": True}
        rows.append(row)
    return {"displayName": kind.name, "apiKeyEnv": KEY_REF, "api": api, "baseURL": base_url(api, at),
            "models": rows, IDLE_KEY: IDLE_WAIT}


def keep_compat(cur: dict[str, Any], route: dict[str, Any]) -> None:
    """The compat switches set by hand in dsh's Models page on a model of the
    route stay while the route keeps its protocol; a false one is the user's
    and stays over OpenProgram's true (magpie ``dshKeepCompat``)."""
    if cur.get("api") != route.get("api"):
        return
    old = {m.get("id"): m.get("compat") for m in cur.get("models") or [] if isinstance(m, dict)
           and isinstance(m.get("compat"), dict)}
    for model in route.get("models") or []:
        kept = old.get(model.get("id"))
        if not kept:
            continue
        compat = model.setdefault("compat", {})
        for key, value in kept.items():
            if key not in compat or value is False:
                compat[key] = value


def put_providers(providers: dict[str, Any], on: bool, models: list[dict[str, Any]], at: Optional[Wiring]) -> None:
    """OpenProgram's routes for ``models`` in a llm-pi-ai providers mapping, or
    with ``on`` false taken out; the user's routes stay, and so do the
    protocol and switches picked for OpenProgram's in dsh's Models page."""
    if not on:
        for key in ROUTE_KEYS:
            providers.pop(key, None)
        return
    assert at is not None
    by_route: dict[str, list[dict[str, Any]]] = {}
    for model in models:
        by_route.setdefault(route_of(model), []).append(model)
    for kind in KINDS:
        rows = by_route.get(kind.key) or []
        if not rows and (kind.key != KINDS[0].key or models):
            providers.pop(kind.key, None)
            continue
        cur = providers.get(kind.key)
        route = kind_config(kind, rows, str((cur or {}).get("api") or "") if isinstance(cur, dict) else "", at)
        _outputs(route, cur)
        if isinstance(cur, dict):
            keep_compat(cur, route)
            for key, value in route.items():
                if key == IDLE_KEY and IDLE_KEY in cur:
                    continue  # the user's own wait, or the one written before
                cur[key] = value
        else:
            providers[kind.key] = route


def put_route(items: list[Item], on: bool, models: list[dict[str, Any]], at: Optional[Wiring],
              *, add: bool = True) -> list[Item]:
    """OpenProgram's routes among the custom providers of the llm-pi-ai entry,
    or with ``on`` false taken out; an entry left with nothing goes."""
    i = config_index(items, PI_ROW)
    if i < 0:
        i = find_last(items, PI_ROW)
    if i < 0 and (not on or not add):
        return items
    items = list(items)
    if i >= 0:
        node, mine = items[i].node(), items[i].ours
    else:
        node, mine = {"id": PI_ROW, "name": PI_PLUGIN}, True
        items.append(Item(PI_ROW, True, []))
        i = len(items) - 1
    config = node.get("config") if isinstance(node.get("config"), dict) else {}
    providers = config.get("providers") if isinstance(config.get("providers"), dict) else {}
    before = copy.deepcopy(node)
    put_providers(providers, on, models, at)
    config["providers"] = providers
    node["config"] = config
    if not on:
        if not providers:
            config.pop("providers", None)
        if not config:
            node.pop("config", None)
        if set(node) <= {"id", "name"}:
            return items[:i] + items[i + 1:]
    if node == before and items[i].lines:
        return items
    items[i] = item_of(node, mine)
    return items


def start_lines(route: str, model: str, effort: str = "") -> Item:
    """The agent-default-model entry: the model new sessions start on, on
    ``route``, with the thinking effort they start with (magpie ``dshDefaultLines``)."""
    lines = [f"- id: {START_ROW} {MARK}", "  config:", f"    provider: {route}", f"    model: {json.dumps(model)}"]
    if effort:
        lines.append(f"    reasoningEffort: {json.dumps(effort)}")
    return Item(START_ROW, True, lines)


def put_start(items: list[Item], ref: str, models: list[dict[str, Any]], *, keep_pick: bool,
              effort: Optional[str] = None, only_existing: bool = False) -> list[Item]:
    """The start of new sessions on ``ref`` through the gateway. ``keep_pick``
    is a re-apply of the same pick (a catalog sync, a new port): the start a
    profile has is then the user's, picked in dsh since or not, and only
    moves to the route its model is on now; a profile without one is given
    it (magpie ``dshRouteAgain``, ``dshFillNewProfiles``). ``effort`` is the
    thinking effort picked (``""`` dsh's own default), ``None`` when it
    didn't change: a new model then carries the one there over where it
    takes it (magpie ``dshSetEffort``, ``dshSet``)."""
    items = list(items)
    i = find(items, START_ROW)
    if only_existing and config_index(items, START_ROW) < 0:
        return items
    model = next((m for m in models if m.get("id") == ref), {})
    if i >= 0:
        cfg = items[i].config()
        if keep_pick and effort is None:
            return start_on_its_route(items, models)
        if keep_pick and cfg.get("model"):
            # the effort alone: the start, the user's or OpenProgram's, keeps its model
            node = items[i].node()
            node.setdefault("config", {})
            if effort:
                node["config"]["reasoningEffort"] = effort
            else:
                node["config"].pop("reasoningEffort", None)
            items[i] = item_of(node, items[i].ours)
            return start_on_its_route(items, models)
        if effort is None:
            # the effort carries over where the new model takes it
            was = str(cfg.get("reasoningEffort") or "")
            effort = was if was in (OWN_EFFORTS if own_pick(ref) else levels_at(model)) else ""
    if own_pick(ref):
        # one of dsh's own: on its own provider, with OpenProgram not in the way
        provider, _, name = ref.partition(":")
        entry = start_lines(provider, name, effort or "")
    else:
        entry = start_lines(route_for(ref, models), ref, effort or "")
    if i >= 0:
        items[i] = entry
    else:
        items.append(entry)
    return start_on_its_route(items, models)


def start_on_its_route(items: list[Item], models: list[dict[str, Any]]) -> list[Item]:
    """A start on a model of OpenProgram's moves to the route that model is on
    now: dsh finds a model by route and id (magpie ``dshStartOnItsRoute``)."""
    out = list(items)
    for i, item in enumerate(out):
        if item.id != START_ROW:
            continue
        cfg = item.config()
        provider, model = str(cfg.get("provider") or ""), str(cfg.get("model") or "")
        if not is_route(provider) or not model or not any(m.get("id") == model for m in models):
            continue
        want = route_for(model, models)
        if want == provider:
            continue
        node = item.node()
        node["config"]["provider"] = want
        out[i] = item_of(node, item.ours)
    return out


def unwire(items: list[Item], kept_start: Optional[list[str]]) -> list[Item]:
    """OpenProgram taken out of a patch list as it is now: its routes, and its
    start, the user's own put back. A start dsh saved into OpenProgram's
    entry off its routes is the user's pick and stays, unmarked."""
    items = put_route(items, False, [], None)
    out: list[Item] = []
    for item in items:
        if item.ours and (item.id in (SEARCH_ROW, *LEGACY_ROWS) or item.id == WEB_ROW and web_ours(item)):
            continue
        if item.id == START_ROW and ours(item):
            cfg = item.config()
            if item.ours and not is_route(str(cfg.get("provider") or "")) and cfg.get("model"):
                lines = list(item.lines)
                lines[0] = lines[0].replace(MARK, "").rstrip()
                out.append(Item(item.id, False, lines))
            elif kept_start:
                out.append(Item(START_ROW, False, list(kept_start)))
                kept_start = None
            continue
        out.append(item)
    return out


# Web search (magpie dsh_search.go)

SEARCH_ROW = "web-search-deepseek"
SEARCH_PLUGIN = "@deepseek-ai/dsh-web-search-deepseek"
SEARCH_KEY_REF = "DEEPSEEK_API_KEY"
#: dsh-web, the row that picks the provider each search runs on, and what
#: OpenProgram's names: deepseek-official (the plugin above), as every dsh
#: base before 0.2.2 did, so a session on a gateway model doesn't search on
#: DeepSeek's paid Search API (magpie ``dshWebLines``, #7461).
WEB_ROW = "web"
WEB_PLUGIN = "@deepseek-ai/dsh-web"
WEB_SEARCH = "deepseek-official"
WEB_FETCH = "http"
WEB_SEARCH_ENV = "DSH_WEB_SEARCH_PROVIDER"
#: The stash record of the patch lists OpenProgram's web row was put in
#: (magpie ``dshWebPinKey``).
SEARCH_RECORD = "dsh#search"


def search_lines(model: str, at: Wiring) -> Item:
    """OpenProgram's web-search-deepseek entry: the gateway's Messages API
    (the plugin adds /messages to the base) on its key, asking ``model``."""
    return Item(SEARCH_ROW, True, [f"- id: {SEARCH_ROW} {MARK}", f"  name: {json.dumps(SEARCH_PLUGIN)}",
                                   "  config:", f"    apiKeyEnv: {KEY_REF}",
                                   f"    baseURL: {json.dumps(at.base('anthropic') + '/v1')}",
                                   f"    model: {json.dumps(model)}"])


def web_lines() -> Item:
    return Item(WEB_ROW, True, [f"- id: {WEB_ROW} {MARK}", f"  name: {json.dumps(WEB_PLUGIN)}", "  config:",
                                f"    fetchProvider: {WEB_FETCH}", f"    searchProvider: {WEB_SEARCH}"])


def web_ours(item: Item) -> bool:
    """A web row of OpenProgram's as it wrote it: dsh's settings page keeps
    the mark when it writes another provider into it."""
    return item.ours and item.config() == {"fetchProvider": WEB_FETCH, "searchProvider": WEB_SEARCH}


def _env_value(path: Path, key: str) -> str:
    from .model_rows import read_doc
    return str(read_doc("dotenv", path).get(key) or "").strip()


def own_search_key(dir_: Path) -> bool:
    """dsh has a DeepSeek key of its own to search with: in its .env, or in
    its key store (magpie ``dshOwnSearchKey``)."""
    from .model_rows import read_doc
    if _env_value(dir_ / ".env", SEARCH_KEY_REF):
        return True
    creds = read_doc("yaml", dir_ / ".credentials.yaml")
    refs, records = creds.get("refs") or {}, creds.get("records") or {}
    return bool(isinstance(refs, dict) and refs.get(SEARCH_KEY_REF)) or \
        isinstance(records, dict) and isinstance(records.get(SEARCH_KEY_REF), dict)


def search_picked(dir_: Path) -> bool:
    """The user picked dsh's search provider outside the profile's patch
    list: a web row in the home patch, or DSH_WEB_SEARCH_PROVIDER in dsh's
    .env or here; a home patch that can't be read counts as picked."""
    if _env_value(dir_ / ".env", WEB_SEARCH_ENV) or (os.environ.get(WEB_SEARCH_ENV) or "").strip():
        return True
    path = home_patch(dir_)
    try:
        text, exists = _read(path)
        return exists and find_last(parse(text, str(path))[1], WEB_ROW) >= 0
    except Exception:  # noqa: BLE001 - unreadable counts as picked
        return True


def _pinned() -> dict[str, bool]:
    from .. import stash
    try:
        return dict((stash.entry(SEARCH_RECORD) or {}).get("pinned") or {})
    except stash.StashUnreadable:
        return {}


def _set_pinned(f: Path, on: bool) -> None:
    from .. import stash
    pinned = _pinned()
    key = os.path.realpath(f)
    if bool(pinned.get(key)) == on:
        return
    if on:
        pinned[key] = True
    else:
        pinned.pop(key, None)
    try:
        stash.put(SEARCH_RECORD, {"pinned": pinned} if pinned else None)
    except stash.StashUnreadable:
        pass


def put_search(items: list[Item], f: Path, dir_: Path, model: str, at: Wiring) -> list[Item]:
    """dsh's web search pointed at the gateway, asking ``model``, or with
    ``model`` ``""`` OpenProgram's entry taken out, in patch list ``f``. An
    entry of the user's stays, and so does dsh's own search where it has a
    DeepSeek key; while OpenProgram's entry is there the web row names it
    (magpie ``dshPutSearch``)."""
    items = list(items)
    i = find_last(items, SEARCH_ROW)
    if i < 0 or items[i].ours:
        if not model or own_search_key(dir_):
            if i >= 0:
                del items[i]
        elif i >= 0:
            items[i] = search_lines(model, at)
        else:
            items.append(search_lines(model, at))
    i = find_last(items, SEARCH_ROW)
    return pin_search(items, f, dir_, i >= 0 and items[i].ours)


def pin_search(items: list[Item], f: Path, dir_: Path, on: bool) -> list[Item]:
    """OpenProgram's web row in patch list ``f``, or taken out. A web row of
    the user's stays, and none is written where the user picked a provider
    (:func:`search_picked`) or where OpenProgram's was before and is gone:
    "Automatic" on dsh's Web search page takes the row out (magpie
    ``dshPinSearch``)."""
    i = find_last(items, WEB_ROW)
    mine = i >= 0 and web_ours(items[i])
    if not on:
        if mine:
            items = items[:i] + items[i + 1:]
            _set_pinned(f, False)
        return items
    if i >= 0 or _pinned().get(os.path.realpath(f)) or search_picked(dir_):
        return items
    _set_pinned(f, True)
    return [*items, web_lines()]


def start_model(items: list[Item]) -> str:
    """The gateway model the patch list starts on, ``""`` for one off its routes."""
    i = find(items, START_ROW)
    if i < 0:
        return ""
    cfg = items[i].config()
    return str(cfg.get("model") or "") if is_route(str(cfg.get("provider") or "")) else ""


# A dsh before profiles (magpie dshBeforeProfiles, dshProviderLines, dshLoopLines, dshRouteLines)

#: The rows of a ``config.yaml`` OpenProgram takes over.
LEGACY_ROWS = ("llm-deepseek", "agent-loop", "api-gateway")
LEGACY_FILE = "config.yaml"


def before_profiles(dir_: Path) -> bool:
    """A dsh with no profile at ``dir_`` is one from before profiles, which
    reads ``config.yaml``: by its version where that is known, else only
    where config.yaml holds an entry that isn't OpenProgram's."""
    version = dsh_version()
    if version:
        return newer(PROFILES_SINCE, version)
    try:
        text, exists = _read(dir_ / LEGACY_FILE)
        return exists and any(not item.ours for item in parse(text)[1])
    except Exception:  # noqa: BLE001 - a config.yaml that can't be read is none
        return False


def legacy_lines(ref: str, models: list[dict[str, Any]], at: Wiring, effort: str) -> list[Item]:
    """The llm-deepseek entry pointing a dsh before profiles at the gateway,
    with the catalog as the models its /model offers and the key in the
    entry; agent-loop and api-gateway on ``ref``."""
    lines = [f"- id: llm-deepseek {MARK}", "  config:", f"    apiKey: {json.dumps(at.key)}",
             f"    baseURL: {json.dumps(at.base('openai-chat'))}", "    thinking: enabled",
             f"    reasoningEffort: {effort if effort in OWN_EFFORTS else 'high'}",
             "    models:" if models else "    models: []"]
    for model in models:
        lines += [f"      - id: {json.dumps(model['id'])}", f"        name: {json.dumps(model_name(model))}"]
        if window(model):
            lines.append(f"        contextWindow: {window(model)}")
        if dsh_max_tokens(model):
            lines.append(f"        maxTokens: {dsh_max_tokens(model)}")
        if model.get("vision"):
            lines.append("        inputModalities: [text, image]")
    loop = [f"- id: agent-loop {MARK}", "  config:", "    agents:", "      - id: main",
            "        provider: deepseek-official", f"        model: {json.dumps(ref)}",
            "        cwd: !!js process.cwd()"]
    route = [f"- id: api-gateway {MARK}", "  config:", "    provider: deepseek-official", f"    model: {json.dumps(ref)}"]
    return [Item("llm-deepseek", True, lines), Item("agent-loop", True, loop), Item("api-gateway", True, route)]


def put_legacy(items: list[Item], ref: str, models: list[dict[str, Any]], at: Wiring, effort: str) -> list[Item]:
    out = list(items)
    for entry in legacy_lines(ref, models, at, effort):
        i = find(out, entry.id)
        if i >= 0:
            out[i] = entry
        else:
            out.append(entry)
    return out


# Files

def profiles(dir_: Path) -> list[Path]:
    """The profiles' patch lists, the web profile's first (magpie ``dshProfiles``)."""
    files = sorted((dir_ / "profiles").glob("*/cordis.patch.yml"))
    return [f for f in files if f.parent.name == "web"] + [f for f in files if f.parent.name != "web"]


def home_patch(dir_: Path) -> Path:
    """dsh's home patch layer, applied over every profile's."""
    return dir_ / "cordis.patch.yml"


def desktop_file(dir_: Path) -> Path:
    return dir_ / "profiles" / "desktop" / "cordis.patch.yml"


def web_file(dir_: Path) -> Path:
    return dir_ / "profiles" / "web" / "cordis.patch.yml"


def desktop_installed() -> bool:
    return bool(app_bundles(APP))


def _read(path: Path) -> tuple[str, bool]:
    from ..edit import ConfigText
    text = ConfigText.read(path)
    return text.text, text.exists


_VALUE_LINE = re.compile(r"""^\s*(?:- )?([A-Za-z0-9_-]+):\s*["']?([^"'#\s]+)["']?\s*$""")


def mark_patch(text: str, orig: str) -> str:
    """The undo note over each line of a patch list that points at the gateway
    (magpie ``edit.MarkYAMLLines``); a top-level list, which ``mark_yaml``
    does not take."""
    from ..edit import mark
    if mark.note is None:
        return text
    lines = [line for line in text.splitlines(keepends=True) if not mark.is_note(line)]
    notes = {}
    for i, line in enumerate(lines):
        match = _VALUE_LINE.match(line.rstrip("\r\n"))
        if match:
            said = mark.note(PI_ROW, match.group(1), match.group(2))
            if said:
                notes[i] = said
    out = "".join(mark._with_notes(lines, orig, notes))
    try:
        same = yaml.load(out, Loader=_Loader) == yaml.load(text, Loader=_Loader)  # noqa: S506
    except yaml.YAMLError:
        same = False
    return out if same else text


def _patch_write(path: Path, build: Callable[[list[Item]], list[Item]]) -> Optional[Write]:
    """The whole-file write of one patch list, ``None`` when it would not change."""
    from ..edit import ConfigUnreadable
    text, exists = _read(path)
    if not exists:
        return None
    try:
        head, items = parse(text, str(path))
        new_items = build(items)
    except PatchUnreadable as exc:
        raise ConfigUnreadable(f"{path}: {exc}") from exc
    content = join(head, new_items)
    content = mark_patch(content, text)
    if content == text:
        return None
    return Write(path, "file", content=content, listing=True)


def make_profile(f: Path) -> None:
    """The patch list of a profile dsh hasn't made yet, as dsh's template,
    in an owner-only folder as dsh makes it; dsh's initProfile adds the
    profile's other files beside it on its first boot and keeps it (magpie
    ``dshMakeProfile``)."""
    from ..edit import write_atomic
    f.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not f.exists():
        write_atomic(f, PROFILE_TEMPLATE, keep_mode=False)


def _settings_write(dir_: Path, models: list[dict[str, Any]], at: Wiring) -> Optional[Write]:
    """OpenProgram's routes in ``settings.yaml``'s llm-pi-ai providers, which
    deepseek-acp reads and nothing else; dsh 0.2 imports and renames the
    file at each start, so it is written again at each sync. A dsh 0.1.x
    model pick saved there goes over the patch list, so it goes (magpie
    ``dshSettingsRoute``)."""
    from .model_rows import read_doc
    path = dir_ / "settings.yaml"
    doc = read_doc("yaml", path)
    if not doc:
        keep_imported(dir_)
    pi = doc.get(PI_ROW)
    if pi is not None and not isinstance(pi, dict):
        return None  # not a section dsh reads: the user's to mend
    providers = copy.deepcopy((pi or {}).get("providers") or {})
    if not isinstance(providers, dict):
        return None
    put_providers(providers, True, models, at)
    sets = {f"{PI_ROW}.providers.{key}": providers[key] for key in ROUTE_KEYS if key in providers}
    deletes = [f"{PI_ROW}.providers.{key}" for key in ROUTE_KEYS if key not in providers]
    deletes.append(START_ROW)
    return Write(path, "yaml", sets=sets, deletes=deletes, listing=True)


def _only_our_routes(doc: Any) -> bool:
    """A settings.yaml holding nothing but OpenProgram's routes under llm-pi-ai
    (magpie ``dshOnlyOurRoute``)."""
    if not isinstance(doc, dict) or list(doc) != [PI_ROW]:
        return False
    pi = doc[PI_ROW]
    if not isinstance(pi, dict) or list(pi) != ["providers"]:
        return False
    providers = pi["providers"]
    return isinstance(providers, dict) and bool(providers) and all(is_route(key) for key in providers)


def keep_imported(dir_: Path) -> None:
    """``settings.yaml.imported``, what dsh imported from the user's own
    settings.yaml, copied to ``settings.yaml.imported.kept`` before
    OpenProgram makes a settings.yaml of its own: dsh's next start renames
    that one over it. One that is only OpenProgram's routes, or one kept
    already, isn't copied (magpie ``dshKeepImported``)."""
    from ..edit import write_atomic
    imported = dir_ / "settings.yaml.imported"
    kept = dir_ / "settings.yaml.imported.kept"
    try:
        raw = imported.read_text(encoding="utf-8")
    except OSError:
        return
    if not raw.strip() or kept.exists():
        return
    try:
        if _only_our_routes(yaml.safe_load(raw)):
            return
    except yaml.YAMLError:
        pass
    try:
        write_atomic(kept, raw)  # a new file is owner-only
    except OSError:
        return


def owner_only(path: Path) -> None:
    """The group's and others' bits off a file of dsh's: its key store is
    refused, and deepseek-acp won't start, when anyone else can read it.
    Windows has no such bits (magpie ``ownerOnly``)."""
    if sys.platform == "win32":
        return
    try:
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            os.chmod(path, mode & ~0o077)
    except OSError:
        return


def _creds_write(dir_: Path, at: Wiring) -> Optional[Write]:
    """The key in dsh's own key store, ``.credentials.yaml`` (refs), which
    the desktop app reads and .env alone does not reach: a new store is made
    owner-only, as dsh wants it, and one there already, the user's, may be
    readable by others, which dsh refuses: it is made owner-only too (the
    write keeps a file's mode). One in a layout other than version 1 is
    dsh's to move and isn't touched (magpie ``dshCreds``)."""
    from .model_rows import read_doc
    path = dir_ / ".credentials.yaml"
    text, exists = _read(path)
    sets: dict[str, Any] = {f"refs.{KEY_REF}": at.key}
    if not text.strip():
        sets = {"version": 1, **sets}
    elif str(read_doc("yaml", path).get("version")) != "1":
        return None
    if exists:
        owner_only(path)
    return Write(path, "yaml", sets=sets)


@dataclass
class Home:
    """A dsh home OpenProgram wires: ``fresh`` is the patch list made for a
    dsh that has no profile yet, ``config.yaml`` for a dsh before profiles
    (magpie ``dshOn``); ``gemini`` whether every dsh reading it speaks
    Gemini's API."""

    agent_id: str
    dir: Callable[[], Optional[Path]]
    fresh: Callable[[Path], Path]
    gemini: Callable[[], bool] = lambda: False

    def files(self) -> list[Path]:
        dir_ = self.dir()
        if dir_ is None:
            return []
        return profiles(dir_) or [self.fresh(dir_)]

    def path(self) -> Path:
        files = self.files()
        return files[0] if files else Path()

    def legacy(self) -> bool:
        """The home is read by a dsh before profiles."""
        files = self.files()
        return bool(files) and files[0].name == LEGACY_FILE

    def models(self, models: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return asked(models, self.gemini())

    def efforts(self, values: dict[str, str]) -> list[str]:
        """The thinking efforts sessions can start with on the picked model:
        its levels on its route; dsh's own four for a dsh before profiles
        (magpie's effort ``Options``)."""
        from .model_rows import picked
        if self.legacy():
            return list(OWN_EFFORTS)
        if own_pick(str(values.get("model") or "")):
            return list(OWN_EFFORTS)
        row = picked(values)
        return list(levels_at(self.models([row])[0])) if row else []

    def wire(self, at: Wiring) -> list[Write]:
        from .engine import AgentConfigError
        from .model_rows import unprefixed
        dir_ = self.dir()
        if dir_ is None:
            raise AgentConfigError("its data folder can't be told: nothing to write")
        models = self.models(at.models)
        ref = unprefixed(at.model)
        record = self._record()
        recorded = record.get("values") or {}
        # a sync, or a re-apply of the same pick (a reseat, a new port),
        # leaves a start the user picked in dsh since; a new pick takes over from it
        keep_pick = at.syncing or str(recorded.get("model") or "") == at.model
        effort = self._effort(at, recorded, models, ref)
        if self.legacy():
            if own_pick(ref):
                raise AgentConfigError("a DeepSeek Harness before profiles picks its own models in dsh itself")
            return self._wire_legacy(dir_, at, models, ref, effort)
        writes: list[Write] = []
        for f in self.files():
            if not f.exists():
                # the desktop app not opened yet, or a dsh that hasn't booted
                # a profile: the profile is made as dsh makes it, and dsh
                # keeps the patch list it finds (#2279)
                make_profile(f)
            self._refresh_copy(f, record)

            def build(items: list[Item], f: Path = f) -> list[Item]:
                items = put_start(put_route(items, True, models, at), ref, models, keep_pick=keep_pick,
                                  effort=effort)
                return put_search(items, f, dir_, start_model(items), at)
            write = _patch_write(f, build)
            if write is not None:
                writes.append(write)
        # the home layer replaces each profile's entry of the same id: what is
        # written there goes there too, but only into entries it already has
        # (one added there would take the user's own providers out, #804)
        home = home_patch(dir_)
        if home.exists():
            def mirror(items: list[Item]) -> list[Item]:
                if config_index(items, PI_ROW) >= 0:
                    items = put_route(items, True, models, at)
                return put_start(items, ref, models, keep_pick=keep_pick, effort=effort, only_existing=True)
            self._refresh_copy(home, record)
            write = _patch_write(home, mirror)
            if write is not None:
                writes.append(write)
        settings = _settings_write(dir_, models, at) if models else None
        for write in (settings, _creds_write(dir_, at)):
            if write is not None:
                writes.append(write)
        writes.append(Write(dir_ / ".env", "dotenv", sets={KEY_REF: at.key}))
        return writes

    def _effort(self, at: Wiring, recorded: dict[str, Any], models: list[dict[str, Any]],
                ref: str) -> Optional[str]:
        """The effort picked, ``None`` when it didn't change. One the model
        doesn't take is refused as it is picked; one a new model doesn't
        take is dropped (magpie ``dshSetEffort``)."""
        from .engine import AgentConfigError
        value = str(at.values.get("effort") or "")
        was = str(recorded.get("effort") or "")
        if value == was:
            return None
        if not value:
            return ""
        if self.legacy() or own_pick(ref):
            if value not in OWN_EFFORTS:
                raise AgentConfigError(f"DeepSeek Harness takes an effort of {', '.join(OWN_EFFORTS)}, not {value!r}")
            return value
        offered = levels_at(next((m for m in models if m.get("id") == ref), {}))
        if value in offered:
            return value
        if not offered:
            raise AgentConfigError(f"DeepSeek Harness has no thinking levels for {ref}")
        raise AgentConfigError(f"DeepSeek Harness takes an effort of {', '.join(offered)} for {ref}, not {value!r}")

    def _wire_legacy(self, dir_: Path, at: Wiring, models: list[dict[str, Any]], ref: str,
                     effort: Optional[str]) -> list[Write]:
        """``config.yaml`` of a dsh before profiles (magpie ``dshSetFile`` there)."""
        from ..edit import ConfigUnreadable
        f = dir_ / LEGACY_FILE
        text, exists = _read(f)
        try:
            head, items = parse(text, str(f)) if exists else ([], [])
        except PatchUnreadable as exc:
            raise ConfigUnreadable(f"{f}: {exc}") from exc
        if effort is None:
            i = find(items, "llm-deepseek")
            effort = str(items[i].config().get("reasoningEffort") or "") if i >= 0 else ""
        content = join(head, put_legacy(items, ref, models, at, effort))
        writes = [] if exists and content == text else [Write(f, "file", content=content, listing=True)]
        writes.append(Write(dir_ / ".env", "dotenv", sets={KEY_REF: at.key}))
        return writes

    def _record(self) -> dict[str, Any]:
        from .. import stash
        try:
            return stash.entry(self.agent_id) or {}
        except stash.StashUnreadable:
            return {}

    def _refresh_copy(self, f: Path, record: dict[str, Any]) -> None:
        """The copy taken of a patch list before OpenProgram first wrote it is
        what a disconnect puts back while nothing else changed the file
        since. dsh writes these files itself (its Models page, its /model),
        so before each later write the copy becomes the file as it is now
        with OpenProgram taken out: what dsh or the user added since stays
        on disconnect (magpie takes its entries out of the file as it is).
        A file as OpenProgram last wrote it leaves the copy as it is, the
        user's own lines byte for byte."""
        from ..edit import write_atomic
        from .engine import _sha
        entry = (record.get("files") or {}).get(str(Path(os.path.realpath(f))))
        if not entry or not entry.get("copy") or not f.exists():
            return
        try:
            text = f.read_text(encoding="utf-8")
            if _sha(text) == ((entry.get("wrote") or {}).get("") or {}).get("sha"):
                return
            head, items = parse(text, str(f))
            starts = record.get("starts") or {}
            write_atomic(Path(entry["copy"]), join(head, unwire(items, starts.get(str(Path(os.path.realpath(f)))))))
        except (OSError, PatchUnreadable):
            return

    def unwire(self, record: dict[str, Any]) -> None:
        """What disconnecting writes before the restore (magpie ``Unwire``):
        OpenProgram's routes, start, search rows and legacy rows out of every
        patch list something else wrote since OpenProgram last did. A patch
        list as OpenProgram left it is the restore's: its copy goes back."""
        from .engine import _sha
        writes = []
        starts = record.get("starts") or {}
        for path, entry in (record.get("files") or {}).items():
            if entry.get("format") != "file":
                continue
            f = Path(path)
            _set_pinned(f, False)
            try:
                text = f.read_text(encoding="utf-8")
            except OSError:
                continue
            if _sha(text) == ((entry.get("wrote") or {}).get("") or {}).get("sha"):
                continue
            try:
                head, items = parse(text, path)
                new = join(head, unwire(items, starts.get(path)))
            except PatchUnreadable:
                continue
            if new != join(head, items):
                writes.append(Write(f, "file", content=new))
        from .model_rows import write_now
        write_now(writes)

    def on_route(self, _where: Path, connected: bool, record: dict[str, Any]) -> None:
        if connected:
            self._keep_starts(record)
            return
        # what was made for a dsh that never booted it since goes again,
        # after the restore put the template back (magpie ``dshTidyMade``)
        folder = self.dir()
        if folder is not None:
            _tidy(self.fresh(folder))

    def _keep_starts(self, record: dict[str, Any]) -> None:
        """The user's own starts the first connect replaced, read from the
        copies taken before it, for a disconnect that finds the file changed."""
        from .. import stash
        kept: dict[str, list[str]] = {}
        for path, entry in (record.get("files") or {}).items():
            if entry.get("format") != "file" or not entry.get("copy"):
                continue
            try:
                _head, items = parse(Path(entry["copy"]).read_text(encoding="utf-8"))
            except (OSError, PatchUnreadable):
                continue
            i = find(items, START_ROW)
            if i >= 0 and not ours(items[i]):
                kept[path] = items[i].lines
        if kept:
            try:
                stash.update(self.agent_id, starts=kept)
            except stash.StashUnreadable:
                pass


def _tidy(f: Path) -> None:
    """Take out a profile patch list that is dsh's template alone with nothing
    beside it (dsh never booted it: no package.json), and the folders that
    leaves empty."""
    try:
        if f.read_text(encoding="utf-8").replace("\r\n", "\n") != PROFILE_TEMPLATE:
            return
        if [p.name for p in f.parent.iterdir()] != [f.name]:
            return
        f.unlink()
    except OSError:
        return
    _rmdirs(f.parent)


def _rmdirs(profile: Path) -> None:
    """A profile's folder and ``profiles/``, each only while empty."""
    for folder in (profile, profile.parent):
        try:
            folder.rmdir()
        except OSError:
            return


def effort_field(agent_id: str, efforts: Callable[[dict[str, str]], list[str]]) -> Field:
    """The thinking effort new sessions start with: agent-default-model's
    reasoningEffort, ``""`` dsh's own (its route's level); the picked model's
    levels on its route (magpie's ``effort`` field)."""
    from .model_rows import choice
    return Field("effort", "Thinking", quiet=True, options=choice(agent_id, efforts))


def notice_for(dir_: Callable[[], Optional[Path]], unopened_note: str, desktop: Callable[[], bool],
               fresh: Callable[[Path], Path], running: tuple[str, ...], restart_note: str, new_note: str) -> str:
    folder = dir_()
    if folder is None:
        return ""
    made = fresh(folder)
    if desktop() and made.is_file() and not (made.parent / "package.json").exists():
        return unopened_note
    if profiles(folder):
        return new_note
    from .running import any_running
    if any_running(*running):
        return restart_note
    return ""


# DeepSeek Harness

def _fresh(dir_: Path) -> Path:
    # the desktop app installed and never opened has no profile yet: its
    # own is made as dsh makes it; a dsh before profiles reads config.yaml;
    # else the web profile `dsh web` boots
    if desktop_installed():
        return desktop_file(dir_)
    if before_profiles(dir_):
        return dir_ / LEGACY_FILE
    return web_file(dir_)


_RUNNING = (r"DeepSeek Harness\.app/", r"DSH (NEXT|Desktop)\.app/", r"(^|/)dsh( |$)",
            r"(^|/)deepseek-harness( |$)", r"(^|/)dsh-desktop(-next)?( |$)")


def _notice() -> str:
    return notice_for(
        home,
        "DeepSeek Harness Desktop hasn't been opened yet: it starts on this the first time it opens.",
        lambda: desktop_installed(), _fresh, _RUNNING,
        "dsh reads its config at start-up — restart open dsh sessions to use this.",
        "New dsh sessions start on this. A session made before keeps its model, open now or reopened "
        "later, until you pick another in it with /model or the model button by its message box. "
        "A dsh started with -p or in a terminal reads it at start-up.")


def _held() -> list[Path]:
    paths = [APP_LAUNCHER, Path("/Applications/DeepSeek Harness.app")]
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            paths.append(Path(local) / "Programs" / "DeepSeek Harness" / "DeepSeek Harness.exe")
    return paths


def compact_field() -> Field:
    """The model DeepSeek Harness compacts its context on, whatever model the
    session is on (#5395): unset, dsh compacts on the session's latest
    model, and a switch away from one whose provider is out of its limit
    failed on its compaction. dsh keeps its own setting in each agent
    preset's whole config, which a profile patch can only replace whole, so
    the gateway moves dsh's compactions instead (``gateway/dsh_compact.py``),
    reading this pick on each request (magpie ``dshCompactField``)."""
    return Field("compact_model", "Compaction Model", quiet=True)


def agent() -> Agent:
    wiring = Home("dsh", home, _fresh, gemini=lambda: speaks_gemini())
    return Agent(
        id="dsh", name="DeepSeek Harness", icon="deepseek", aliases=("deepseek-harness",), kind="cli",
        protocol="openai-chat", bin="dsh", dir=home, path=wiring.path,
        fields=[Field("model", "Model", options=own_options, with_catalog=True),
                effort_field("dsh", wiring.efforts), compact_field()],
        ua=("deepseek-harness",), wire=wiring.wire, unwire=wiring.unwire, on_route=wiring.on_route,
        # the desktop app installed and never opened has no data folder yet (magpie ``sign``, #2279)
        notice=_notice, held_at=_held, sign=lambda: desktop_installed(),
    )
