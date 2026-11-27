"""oh-my-pi (``omp``, a fork of Pi) and its profiles.

After yetone/magpie ``internal/agent/omp.go``, ``omp_scope.go`` and
``omp_advisor.go`` (MIT License, Copyright (c) yetone). omp keeps its
settings in ``config.yml`` in its agent folder, the model of each role under
``modelRoles`` as ``provider/model``, and providers of the user's own in
``models.yml`` beside it (``.yaml`` where only that is there). OpenProgram
adds itself there as the provider ``openprogram``; a model through it is
``openprogram/<provider>/<model>``, which omp matches whole against
provider/id.

The agent folder is found as omp's pi-utils (dirs.ts) finds it: under
``~/.omp``, or ``~/$PI_CONFIG_DIR``; a profile's (``OMP_PROFILE``, else
``PI_PROFILE``; ``default`` is none) is ``profiles/<name>/agent`` there;
with none, ``PI_CODING_AGENT_DIR`` (taken as given) moves it, else it is
``agent`` there. Each other profile under ``profiles/`` is a row of its own,
``omp#<name>``.

Roles: ``default`` is the model; task (subagents), smol, slow, plan,
vision, advisor, commit, tiny and memory are written only when picked, and
take omp's own fallbacks while unset. A role keeps the thinking level omp
reads off its end (``provider/model:high``). The default role's model joins
``enabledModels`` when that list has plain entries and none takes it in
(#1769): ``openprogram/**`` in place of OpenProgram's models named one by one,
unless the user chose which of them it holds. After a sync, a plain entry
on a model no longer given leaves the list and comes back with its model
(:func:`scope_synced_omp`). A model picked for the advisor
turns ``advisor.enabled`` on while it is unset (omp runs none otherwise).

Each model is asked on the API its provider serves natively (Responses,
Messages at the gateway's Anthropic root, Gemini's at ``/v1beta``), with the
efforts omp offers for it; before omp 16.4.0 a model's efforts stop at
xhigh. omp before 18.1.6 asks with Bun's User-Agent, so the provider names
it ``omp``; from 16.0.6 its session goes in ``x-session-affinity``. The key
is written as ``apiKey``, as the gateway takes no request without one: a key
that names omp, never the gateway key itself.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Optional

from .. import edit
from ..edit import get_path
from .base import Agent, Field, Pick, Wiring, Write, find_tool
from .pi_like import (PROTOCOLS, PROVIDER_ID, adaptive_only, max_tokens, native_api, offered_set, protocol_of,
                      read_doc, scope_synced, scope_without, takes)

EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max")
#: a role's own thinking level: auto, off or an effort
ROLE_LEVELS = ("auto", "off", *EFFORTS)
#: the advisor's settings (magpie ``ompAdvisorReviews``, ``ompAdvisorWaits``, ``ompAdvisorWakes``)
ADVISOR_REVIEWS = ("off", "turn", "agent-end")
ADVISOR_WAITS = ("off", "1", "3", "5", "strict")
ADVISOR_WAKES = ("nit", "concern", "blocker")
#: (field key, label, role) — magpie's order and labels
ROLES = (("subagent", "Subagents", "task"), ("small", "Smol", "smol"), ("slow", "Slow", "slow"),
         ("plan", "Plan", "plan"), ("vision", "Vision", "vision"), ("advisor", "Advisor", "advisor"),
         ("commit", "Commit", "commit"), ("tiny", "Tiny", "tiny"), ("memory", "Memory", "memory"))
EVERY = f"{PROVIDER_ID}/**"
SESSION_SINCE, MAX_SINCE, NAMED_SINCE = "16.0.6", "16.4.0", "18.1.6"
_PROFILE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_RESERVED = re.compile(r"(?i)^(?:CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\..*)?$")
_VERSION = re.compile(r"v?(\d+(?:\.\d+)+(?:[-+][0-9A-Za-z.+-]*)?)")


# Folders

def profile_ok(name: str) -> bool:
    """A profile name omp takes (pi-utils dirs.ts ``normalizeProfileName``)."""
    return bool(name) and name != "default" and bool(_PROFILE.match(name)) and not name.endswith(".") \
        and not _RESERVED.match(name)


def root() -> Path:
    renamed = os.environ.get("PI_CONFIG_DIR", "")
    return Path.home() / renamed if renamed else Path.home() / ".omp"


def folder() -> tuple[Path, bool]:
    """omp's agent folder, and whether it is one Pi may share (PI_CODING_AGENT_DIR moved it)."""
    profile = os.environ["OMP_PROFILE"] if "OMP_PROFILE" in os.environ else os.environ.get("PI_PROFILE", "")
    profile = profile.strip()
    if profile_ok(profile):
        return root() / "profiles" / profile / "agent", False
    moved = os.environ.get("PI_CODING_AGENT_DIR", "")
    if moved and os.path.isabs(moved):
        return Path(os.path.normpath(moved)), True
    return root() / "agent", False


def _pick(directory: Path, name: str) -> Path:
    """omp reads the .yml and falls back to the .yaml."""
    yml = directory / f"{name}.yml"
    if not yml.exists() and (directory / f"{name}.yaml").exists():
        return directory / f"{name}.yaml"
    return yml


# Versions

def newer(a: str, b: str) -> bool:
    """Version ``a`` is newer than ``b`` (magpie ``Newer``)."""
    def parts(v: str) -> list[int]:
        return [int(p) for p in re.findall(r"\d+", v.split("-", 1)[0].split("+", 1)[0])]
    return parts(a) > parts(b)


def installed_version() -> str:
    """The version of the omp on PATH, ``""`` when not known."""
    found = find_tool("omp")
    if not found:
        return ""
    try:
        out = subprocess.run([found, "--version"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    match = _VERSION.search(out or "")
    return match.group(1) if match else ""


# Roles

def split_level(value: str) -> tuple[str, str]:
    """A role's model and the ``:level`` omp reads off its end (magpie
    ``ompLevel``); anything else after a colon is part of the model id, and a
    list of models has no level of its own."""
    head, sep, tail = value.rpartition(":")
    if sep and head and "," not in value and (tail in ("off", "auto") or tail in EFFORTS):
        return head, ":" + tail
    return value, ""


def _scope_takes(pattern: str, model: str) -> bool:
    """magpie ``ompTakes``: Pi's rules, and a trailing ``**`` taking in everything under it."""
    p, _ = split_level(pattern.strip())
    if p.endswith("**"):
        base = p[:-2]
        if not any(ch in base for ch in "*?[") and (base == "" or base.endswith("/")):
            return model.lower().startswith(base.lower())
    return takes(p, model)


def scope_with(scope: Any, model: str) -> Optional[list[Any]]:
    """``enabledModels`` letting a session start on ``model``, ``None`` for
    no change (magpie ``ompScopeWith``). Entries scoped to folders are the
    user's and kept as they are."""
    if not isinstance(scope, list):
        return None
    plain = [entry for entry in scope if isinstance(entry, str)]
    if not plain:
        return None
    on_ours = model.lower().startswith(PROVIDER_ID + "/")

    def own_choice() -> bool:
        for entry in plain:
            m, level = split_level(entry.strip())
            glob = any(ch in m for ch in "*?[")
            if glob and _scope_takes(m, model) or m.lower().startswith(PROVIDER_ID + "/") and (glob or level):
                return True
        return False

    if not on_ours or own_choice():
        if any(_scope_takes(entry, model) for entry in plain):
            return None
        return [*scope, model]
    kept = [entry for entry in scope if not (isinstance(entry, str) and not any(ch in entry for ch in "*?[")
                                             and entry.strip().lower().startswith(PROVIDER_ID + "/"))]
    return [*kept, EVERY]


# The provider

def _gemini_mode(model_id: str) -> str:
    name = model_id.rsplit("/", 1)[-1].lower()
    return "budget" if name.startswith(("gemini-1", "gemini-2")) else "google-level"


def provider_entry(at: Wiring, version: str) -> dict[str, Any]:
    """OpenProgram's entry in models.yml (magpie ``ompProviderAt``)."""
    takes_max = bool(version) and not newer(MAX_SINCE, version)
    models = []
    for row in at.models:
        entry: dict[str, Any] = {"id": row["id"], "name": row.get("name") or row["id"]}
        mode = "effort"
        native = native_api(row)
        if native == "gemini":
            entry["api"], entry["baseUrl"] = "google-generative-ai", at.base("gemini") + "/v1beta"
            mode = _gemini_mode(row["id"])
        elif native == "openai-responses":
            entry["api"] = "openai-responses"
        elif native == "anthropic":
            entry["api"], entry["baseUrl"] = "anthropic-messages", at.base("anthropic")
            mode = "anthropic-adaptive" if adaptive_only(row["id"]) else "budget"
        levels = list(row.get("thinking_levels") or [])
        efforts = [x for x in EFFORTS if not (x == "max" and not takes_max)
                   and (x in levels or x == "xhigh" and not takes_max and "max" in levels)]
        entry["reasoning"] = bool(efforts)
        if efforts:
            entry["thinking"] = {"mode": mode, "efforts": efforts}
        if int(row.get("context_window") or 0) > 0:
            entry["contextWindow"] = int(row["context_window"])
        if max_tokens(row) > 0:
            entry["maxTokens"] = max_tokens(row)
        if row.get("vision"):
            entry["input"] = ["text", "image"]
        models.append(entry)
    out: dict[str, Any] = {"baseUrl": at.base("openai-chat"), "api": "openai-completions", "apiKey": at.key}
    agent_name = "omp" if not version else (f"omp/{version}" if newer(NAMED_SINCE, version) else "")
    if agent_name:
        out["headers"] = {"User-Agent": agent_name}
    if version and not newer(SESSION_SINCE, version):
        out["compat"] = {"promptCacheSessionHeader": "x-session-affinity"}
    out["models"] = models
    return out


def _own_agent_name(value: Any) -> bool:
    return value == "omp" or (isinstance(value, str) and value.startswith("omp/") and bool(_VERSION.fullmatch(value[4:])))


def provider_kept(have: Any, entry: dict[str, Any]) -> dict[str, Any]:
    """``entry`` laid over the one in models.yml (magpie ``ompKept``): what
    else the user put there stays; a User-Agent of theirs stays theirs."""
    if not isinstance(have, dict):
        return entry
    out = dict(entry)
    for key, value in have.items():
        if key not in out and key not in ("auth",):
            out[key] = value
    theirs = {k: v for k, v in (have.get("headers") or {}).items()
              if k.lower() != "user-agent" or not _own_agent_name(v)} if isinstance(have.get("headers"), dict) else {}
    headers = dict(theirs)
    if not any(k.lower() == "user-agent" for k in headers) and (entry.get("headers") or {}).get("User-Agent"):
        headers["User-Agent"] = entry["headers"]["User-Agent"]
    if headers:
        out["headers"] = headers
    else:
        out.pop("headers", None)
    return out


# The agent

def provider_on(at: Wiring, entry: dict[str, Any], on: str) -> dict[str, Any]:
    """``entry`` with every model asked on the API ``on`` (magpie ``ompOn``),
    as it is for ``""``: on Anthropic's Messages a model names the gateway's
    Anthropic root and thinks on a budget (adaptively where that is all it
    takes); on Chat and Responses it is asked an effort."""
    if not on:
        return entry
    models = []
    for model in entry.get("models") or []:
        model = {k: v for k, v in model.items() if k not in ("api", "baseUrl")}
        mode = "effort"
        if on == "anthropic-messages":
            model["api"], model["baseUrl"] = on, at.base("anthropic")
            mode = "anthropic-adaptive" if adaptive_only(model["id"]) else "budget"
        if isinstance(model.get("thinking"), dict):
            model["thinking"] = {**model["thinking"], "mode": mode}
        models.append(model)
    return {**entry, "api": on, "models": models}


def _role_levels() -> list[tuple[str, str]]:
    return [(v, v) for v in ROLE_LEVELS]


def advisor_sets(doc: dict[str, Any], values: dict[str, str]) -> dict[str, Any]:
    """The advisor's settings in config.yml (magpie ``ompAdvisorFields``):
    whether it runs and when it reviews, how long the main agent waits for a
    review, and the least note that calls a finished agent back. A model
    picked for the advisor turns it on while ``advisor.enabled`` is unset:
    omp runs none otherwise. omp's enum settings take strings only."""
    sets: dict[str, Any] = {}
    advisor = doc.get("advisor") if isinstance(doc.get("advisor"), dict) else {}
    review = values.get("advisor_review", "")
    if review == "off":
        sets["advisor.enabled"] = False
    elif review in ADVISOR_REVIEWS:
        sets["advisor.enabled"], sets["advisor.reviewMode"] = True, review
    elif values.get("advisor") and "enabled" not in advisor:
        sets["advisor.enabled"] = True
    if values.get("advisor_wait") in ADVISOR_WAITS:
        sets["advisor.syncBacklog"] = values["advisor_wait"]
    if values.get("advisor_wake") in ADVISOR_WAKES:
        sets["advisor.continueSeverity"] = values["advisor_wake"]
    return sets


def scope_synced_omp(doc: dict[str, Any], given: list[str], at: Wiring) -> tuple[Any, bool]:
    """omp's ``enabledModels`` caught up with the models given (magpie
    ``ompScopeSynced``): its plain entries alone, as ``pi_like.scope_synced``
    does Pi's; an entry scoped to folders is the user's list for those
    projects and stays where it is. The list and whether it changed; what is
    remembered is kept in the stash record as ``scope_gone``."""
    scope = doc.get("enabledModels")
    offered = offered_set(given)
    memory = at.previous.get("scope_gone") or {}
    if offered is None:
        return scope, False
    bare = lambda p: split_level(p)[0]  # noqa: E731
    if "enabledModels" not in doc or scope is None:
        out: dict[str, Any] = {}
        made: list[str] = []
        if memory.get("emptied"):
            made, _, out, _ = scope_synced([], False, offered, memory, bare)
        _remember(at, out)
        return (made, True) if made else (scope, False)
    if not isinstance(scope, list):
        return scope, False
    plain = [entry for entry in scope if isinstance(entry, str)]
    if not plain and not memory.get("emptied"):
        # none holds a session to them; one put here would
        return scope, False
    nxt, _, out, changed = scope_synced(plain, True, offered, memory, bare)
    if not changed:
        _remember(at, out)
        return scope, False
    gone = {p for p in plain if p not in nxt}
    kept = [entry for entry in scope if not (isinstance(entry, str) and entry in gone)]
    kept += [p for p in nxt if p not in plain]
    if not any(isinstance(entry, str) for entry in kept) and out.get("entries"):
        out["emptied"] = True
    else:
        out.pop("emptied", None)
    _remember(at, out)
    return kept, True


def _remember(at: Wiring, gone: dict[str, Any]) -> None:
    at.record["scope_gone"] = gone


def _make(agent_id: str, name: str, directory: Callable[[], Path], *, version: Callable[[], str],
          **extra: Any) -> Agent:
    config = lambda: _pick(directory(), "config")  # noqa: E731

    def wire(at: Wiring) -> list[Write]:
        path = config()
        models = _pick(directory(), "models")
        doc = read_doc(path, "yaml")
        roles = doc.get("modelRoles") if isinstance(doc.get("modelRoles"), dict) else {}
        sets: dict[str, Any] = {}
        for key, role in (("model", "default"), *((k, r) for k, _, r in ROLES)):
            pick = at.values.get(key)
            if not pick:
                continue
            value = f"{PROVIDER_ID}/{pick}"
            level = at.values.get(f"{key}_thinking", "") if key != "model" else ""
            current = roles.get(role)
            if level in ROLE_LEVELS:
                value += ":" + level
            elif isinstance(current, str):
                # the role keeps its thinking level: omp clamps one the model
                # lacks and drops it for one that doesn't reason
                value += split_level(current)[1]
            sets[f"modelRoles.{role}"] = value
        if at.values.get("effort") in ("auto", *EFFORTS):
            sets["defaultThinkingLevel"] = at.values["effort"]
        sets.update(advisor_sets(doc, at.values))
        base = None
        if not models.exists() and (directory() / "models.json").exists():
            # omp moves an older models.json to models.yml only while there is
            # no models.yml, so one written first carries it over: omp's own
            # move, done for it, so nothing of it is OpenProgram's (magpie
            # JSONToYAML before its SetYAML)
            old = read_doc(directory() / "models.json")
            base = edit.edit("yaml", "", sets={k.replace(".", "\\."): v for k, v in old.items()},
                             where=str(models)) if old else None
        current = (read_doc(models, "yaml").get("providers") or {}) if models.exists() else {}
        have = current.get(PROVIDER_ID) if isinstance(current, dict) else None
        entry = provider_kept(have, provider_on(at, provider_entry(at, version()), protocol_of(at)))
        provider = f"providers.{PROVIDER_ID}"
        synced: dict[str, Any] = {}
        gone: list[str] = []
        scope, changed = scope_synced_omp(doc, [m.get("id", "") for m in entry.get("models") or []], at)
        if changed:
            if scope:
                synced["enabledModels"] = scope
            else:
                gone.append("enabledModels")
        listed = scope_with(scope, f"{PROVIDER_ID}/{at.model}")
        if listed is not None:
            sets["enabledModels"] = listed
        # the synced list and the provider are rewritten alone when the
        # catalog changes; the picks are written over them on a connect
        # the provider is OpenProgram's whole, edited or not. A role, and
        # the thinking level beside the default one, is omp's own picker's
        # too: it goes back while it is still on OpenProgram and stays once
        # the user moved it off (magpie's role reset, ompOnMagpie)
        picks = tuple(Pick((key,), key, {"prefix": PROVIDER_ID + "/"}) for key in sets if key.startswith("modelRoles."))
        if "defaultThinkingLevel" in sets:
            picks += (Pick(("defaultThinkingLevel",), "modelRoles.default", {"prefix": PROVIDER_ID + "/"}),)
        return [Write(models, "yaml", sets={provider: entry}, owns=(provider,), base=base, listing=True),
                Write(path, "yaml", sets=synced, deletes=gone, listing=True),
                Write(path, "yaml", sets=sets, picks=picks)]

    def unwired(record: dict[str, Any]) -> None:
        # once its provider is gone, OpenProgram's models leave enabledModels
        # (magpie ompScopeWithout), openprogram/** among them
        if not get_path(read_doc(_pick(directory(), "models"), "yaml"), f"providers.{PROVIDER_ID}")[0]:
            scope_without(config(), "yaml", every=EVERY)

    def notice() -> str:
        from .running import any_running
        if any_running(r"(^|/)omp( |$)", r"@oh-my-pi/pi-coding-agent"):
            return "omp reads its settings at start-up — restart open omp sessions to use this."
        return ""

    fields = [Field("model", "Model")]
    for key, label, _ in ROLES:
        # each role but the default one has a thinking level of its own, the
        # ``:level`` omp reads off the end of the role's model
        fields += [Field(key, label, quiet=True, follows="model"),
                   Field(f"{key}_thinking", f"{label} thinking", quiet=True, options=_role_levels)]
    fields += [
        # what sessions start at; unset, omp takes high, and auto has it pick each turn
        Field("effort", "Thinking", quiet=True, options=lambda: [(v, v) for v in ("auto", *EFFORTS)]),
        Field("advisor_review", "Advisor review", quiet=True, options=lambda: [(v, v) for v in ADVISOR_REVIEWS]),
        Field("advisor_wait", "Advisor wait", quiet=True, options=lambda: [(v, v) for v in ADVISOR_WAITS]),
        Field("advisor_wake", "Advisor wakes on", quiet=True, options=lambda: [(v, v) for v in ADVISOR_WAKES]),
        Field("protocol", "Protocol", quiet=True, options=lambda: list(PROTOCOLS)),
    ]
    return Agent(
        id=agent_id, name=name, icon="omp", kind="cli", protocol="openai-chat", dir=directory, path=config,
        fields=fields,
        # a role is put back as the YAML it was written in (magpie keeps its text)
        before_copy=True, wire=wire, notice=notice, unwired=unwired,
        # models.yml is a file others may read: a key that names it
        named_key=True,
        # enabledModels is the user's list as much as OpenProgram's
        loose=("enabledModels",), **extra)


def agent() -> Agent:
    directory, shared = folder()
    extra: dict[str, Any] = {}
    if shared:
        # Pi's folder is no sign of omp: its config.yml or its command is
        extra["detect"] = lambda: _pick(directory, "config").is_file() or bool(find_tool("omp"))
        extra["detect_only"] = True
    return _make("omp", "omp", lambda: folder()[0], version=installed_version, aliases=("oh-my-pi",),
                 ua=("oh-my-pi",), bin="omp", **extra)


def extra_agents() -> list[Agent]:
    """The named profiles under ``profiles/``, each an omp of its own; the one
    the default row already picks is left out (magpie ``ompProfiles``)."""
    base = root() / "profiles"
    try:
        names = sorted(entry.name for entry in os.scandir(base) if entry.is_dir() and profile_ok(entry.name))
    except OSError:
        return []
    current = folder()[0]
    out = []
    for name in names:
        directory = base / name / "agent"
        if directory == current:
            continue
        out.append(_make(f"omp#{name}", f"omp · {name}", lambda d=directory: d, version=installed_version,
                         detect=lambda p=base / name: p.is_dir(), detect_only=True))
    return out
