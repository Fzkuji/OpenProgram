"""LiveAgent (Stack-Cairn/LiveAgent, a Tauri app): its v2 runs its model calls
through K-brain, a Go backend that keeps every provider in one JSONC
``config.json`` in its data folder: ``$LIVEAGENT_HOME``, else
``$K_BRAIN_HOME`` (each only an absolute path), else the folder
``~/.liveagent/data-dir.json`` points to (``{"path": …}``, set when the user
moves LiveAgent's data), else ``~/.liveagent``.

After yetone/magpie ``internal/agent/liveagent.go`` (MIT License, Copyright
(c) yetone). OpenProgram's models go on three providers by the API each is
served on natively, as K-brain asks a provider's every model on the
provider's one API:

* ``openprogram`` — Chat Completions (type ``codex`` with a
  ``requestFormat``, what LiveAgent's settings write for an OpenAI-compatible
  provider), every model not on the two below;
* ``openprogram-responses`` — OpenAI's Responses API, type ``codex``;
* ``openprogram-anthropic`` — Anthropic's Messages API, type ``claude_code``,
  at the gateway's Anthropic root (K-brain appends ``/v1``).

An OpenAI one keeps the request format the user switched it to, a model the
name they gave it, a provider its name, the models turned off stay off
(``activeModels``) and their order stays (``modelOrder``). K-brain decodes
the file with unknown fields refused, so only fields it knows are written.
A file K-brain wouldn't read as providers (a top-level ``models``, or one
provider written bare) is left alone. The model is picked in LiveAgent's own
picker; the default is set to the model picked here only while it is none,
or still K-brain's own (``model1`` of its empty ``demo`` provider).

The backend reads the file once, as it starts, and writes the whole of it
back whenever a setting changes, so a change made while it runs is lost:
connecting is refused then, where that can be told (not on Windows).

A model of OpenProgram's the user deleted in LiveAgent, or a whole provider
of its, is the user's choice, and a sync must not write it back (magpie
``liveAgentKept``). So the stash remembers which of OpenProgram's models
(``m:<id>``) and providers (``k:<key>``) LiveAgent has had; one gone from the
file since is taken out by the user and stays out until it is back in the
file or LiveAgent is turned off and on here. A model new to the list is
still added. LiveAgent's backend writes back what it holds, so one added
while it ran is pending, with the processes then, and counts as had once
other processes run (LiveAgent started again and read it), or, once none
do, if it is still there. Where what runs can't be asked (Windows) each
counts as had at once.
"""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from typing import Any

from .. import edit
from ..edit import ConfigText, load
from .base import Agent, App, Field, Pick, Wiring, Write
from .engine import AgentConfigError
from .pi_like import PROVIDER_ID, max_tokens, native_api, read_doc

#: OpenProgram's providers: key, API, LiveAgent's type, name.
KINDS = (
    (PROVIDER_ID, "openai-completions", "codex", "OpenProgram"),
    (f"{PROVIDER_ID}-responses", "openai-responses", "codex", "OpenProgram · Responses"),
    (f"{PROVIDER_ID}-anthropic", "anthropic-messages", "claude_code", "OpenProgram · Anthropic"),
)
OPENAI_FORMATS = ("openai-completions", "openai-responses")
DEMO, DEMO_MODEL = "demo", "model1"
#: LiveAgent's processes: the app and the k-brain backend it starts.
PROCESSES = (r"^([^ ]*/)?(liveagent|k-brain)( |$)", r"LiveAgent\.app/Contents/MacOS/(liveagent|k-brain)( |$)")


def folder() -> Path:
    for name in ("LIVEAGENT_HOME", "K_BRAIN_HOME"):
        value = os.environ.get(name, "").strip()
        if value and os.path.isabs(value):
            return Path(value)
    default = Path.home() / ".liveagent"
    try:
        moved = json.loads((default / "data-dir.json").read_text(encoding="utf-8")).get("path")
    except (OSError, ValueError, AttributeError):
        moved = None
    if isinstance(moved, str) and os.path.isabs(moved) and os.path.normpath(moved) != os.path.normpath(default):
        return Path(moved)
    return default


def config_path() -> Path:
    return folder() / "config.json"


def _kind(row: dict[str, Any]) -> tuple[str, str, str, str]:
    native = native_api(row)
    if native == "openai-responses":
        return KINDS[1]
    if native == "anthropic":
        return KINDS[2]
    return KINDS[0]


def _base(api: str, at: Wiring) -> str:
    return at.base("anthropic") if api == "anthropic-messages" else at.base("openai-chat")


def _read(path: Path) -> dict[str, Any]:
    """config.json as K-brain reads it, or why it is left alone (magpie ``liveAgentRead``)."""
    doc = read_doc(path, "jsonc")
    if "models" in doc:
        raise AgentConfigError(f"{path} has a top-level models, which K-brain refuses, so it was left alone")
    if "providers" not in doc and any(key in doc for key in ("name", "api", "baseUrl", "apiKey")):
        raise AgentConfigError(f"{path} is one provider written bare, so it was left alone")
    if doc.get("providers") is not None and not isinstance(doc.get("providers"), dict):
        raise AgentConfigError(f"{path}: providers is not an object, so it was left alone")
    return doc


def _default_unset(doc: dict[str, Any], model_key: str, provider_key: str) -> bool:
    """The pick is none, or still K-brain's own demo model1 (magpie ``liveAgentDefaultUnset``)."""
    model, provider = str(doc.get(model_key) or ""), str(doc.get(provider_key) or "")
    if provider and provider != DEMO:
        return False
    if not model:
        return not provider
    if model != DEMO_MODEL:
        return False
    for key, entry in (doc.get("providers") or {}).items():
        if not isinstance(entry, dict):
            continue
        if key == DEMO:
            if str(entry.get("baseUrl") or "").strip():
                return False
        elif any(isinstance(m, dict) and m.get("id") == DEMO_MODEL for m in entry.get("models") or []):
            return False
    return True


def _merge(old: Any, key: str, api: str, typ: str, name: str, models: list[dict[str, Any]],
           at: Wiring, prior: dict[str, dict[str, Any]], off: set[str]) -> dict[str, Any]:
    """OpenProgram's provider laid over the one the file has (magpie ``liveAgentMerge``)."""
    old = old if isinstance(old, dict) else None
    entry: dict[str, Any] = dict(old or {})
    merged = []
    for model in models:
        before = prior.get(model["id"])
        if before is None:
            merged.append(model)
            continue
        out = dict(before)
        for field, value in model.items():
            # a name the user gave it in LiveAgent stays
            if field == "displayName" and "displayName" in before:
                continue
            out[field] = value
        merged.append(out)
    chosen = api
    if typ == "codex" and old is not None and old.get("type") == "codex":
        current = old.get("requestFormat") or old.get("api")
        if current in OPENAI_FORMATS:
            chosen = current
    entry.setdefault("name", name)
    entry.update({"type": typ, "baseUrl": _base(api, at), "api": chosen, "apiKey": at.key})
    if typ == "codex":
        entry["requestFormat"] = chosen
    entry["models"] = merged
    ids = [m["id"] for m in models]
    active = (old or {}).get("activeModels")
    if isinstance(active, list) or any(i in off for i in ids):
        kept = [i for i in (active or []) if i in ids and i not in off]
        entry["activeModels"] = kept + [i for i in ids if i not in off and i not in kept]
    else:
        entry["activeModels"] = None
    order = [i for i in (old or {}).get("modelOrder") or [] if i in ids] if old else []
    entry["modelOrder"] = order + [i for i in ids if i not in order]
    return entry


def pids() -> str:
    """LiveAgent's process ids, comma-joined; ``""`` for none, or where that
    can't be told (Windows) (magpie ``liveAgentPIDs``)."""
    if sys.platform == "win32":
        return ""
    import subprocess
    ids: list[str] = []
    for pattern in PROCESSES:
        try:
            out = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        ids += [i for i in out.split() if i not in ids]
    return ",".join(sorted(ids))


def _here(olds: dict[str, dict[str, Any]]) -> set[str]:
    """The items of OpenProgram's providers in config.json now."""
    here = set()
    for key, old in olds.items():
        here.add("k:" + key)
        for model in old.get("models") or []:
            if isinstance(model, dict) and model.get("id"):
                here.add("m:" + str(model["id"]))
    return here


def take(kept: dict[str, Any], olds: dict[str, dict[str, Any]], grouped: dict[str, list[dict[str, Any]]],
         on: dict[str, str], running: str) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str], set[str]]:
    """``grouped`` and ``on`` without what the user took out in LiveAgent, with
    ``kept`` brought up to the file and the processes ``running``; and the
    providers left as the user has them, with nothing OpenProgram would put
    in them (magpie ``liveAgentKept.take``)."""
    here = _here(olds)
    had: list[str] = kept.setdefault("had", [])
    pending: dict[str, str] = kept.setdefault("pending", {})
    gone: list[str] = kept.setdefault("gone", [])
    # a LiveAgent started since read what was added; one that quit read it
    # only if it is still there (its save drops one it never read)
    for item, was in list(pending.items()):
        if was == running:
            continue
        del pending[item]
        if (running or item in here) and item not in had:
            had.append(item)
    if olds:
        gone += [item for item in had if item not in here and item not in gone]
    kept["gone"] = gone = [item for item in gone if item not in here]
    if not gone:
        return grouped, on, set()
    out: dict[str, list[dict[str, Any]]] = {}
    untouched: set[str] = set()
    for key, models in grouped.items():
        if "k:" + key in gone:
            continue
        left = [m for m in models if "m:" + m["id"] not in gone]
        if models and not left:
            untouched.add(key)
            continue
        out[key] = left
    return out, {i: k for i, k in on.items() if k in out and "m:" + i not in gone}, untouched


def wrote(kept: dict[str, Any], olds: dict[str, dict[str, Any]], grouped: dict[str, list[dict[str, Any]]],
          running: str) -> None:
    """``kept`` brought up to what this write leaves in the file (magpie
    ``liveAgentKept.wrote``): what was there LiveAgent has had, what is added
    now too unless LiveAgent runs, and what left the list is forgotten,
    except what the user took out."""
    here = _here(olds)
    now = set()
    for key, models in grouped.items():
        now.add("k:" + key)
        now |= {"m:" + m["id"] for m in models}
    for key, old in olds.items():
        if key not in grouped:
            # a provider kept as the user has it, its models too
            now |= _here({key: old})
    had = [item for item in kept.get("had") or [] if item in now]
    pending = {item: was for item, was in (kept.get("pending") or {}).items() if item in now}
    for item in sorted(now):
        if item in had or pending.get(item):
            continue
        if item in here or not running:
            had.append(item)
        else:
            pending[item] = running
    kept["had"], kept["pending"] = sorted(had), pending


def _wire(at: Wiring) -> list[Write]:
    if sys.platform != "win32" and not at.previous:
        # a sync while it runs is written as well: its next save puts back
        # what it holds, and the next sync OpenProgram's models
        from .running import any_running
        if any_running(*PROCESSES):
            raise AgentConfigError("LiveAgent writes its settings back over this while it runs: quit LiveAgent, then try again")
    path = config_path()
    doc = _read(path)
    providers = doc.get("providers") if isinstance(doc.get("providers"), dict) else {}
    olds = {kind[0]: providers.get(kind[0]) for kind in KINDS if isinstance(providers.get(kind[0]), dict)}
    prior: dict[str, dict[str, Any]] = {}
    off: set[str] = set()
    for kind in KINDS:
        old = olds.get(kind[0])
        if old is None:
            continue
        active = old.get("activeModels")
        for model in old.get("models") or []:
            if isinstance(model, dict) and model.get("id"):
                prior.setdefault(model["id"], model)
                if isinstance(active, list) and model["id"] not in active:
                    off.add(model["id"])
    grouped: dict[str, list[dict[str, Any]]] = {}
    on: dict[str, str] = {}
    for row in at.models:
        if row["id"] in on:
            continue
        kind = _kind(row)
        model: dict[str, Any] = {"id": row["id"]}
        if row.get("name") and row["name"] != row["id"]:
            model["displayName"] = row["name"]
        if row.get("vision"):
            model["input"] = ["text", "image"]
        if int(row.get("context_window") or 0) > 0:
            model["contextWindow"] = int(row["context_window"])
        if max_tokens(row) > 0:
            model["maxTokens"] = max_tokens(row)
        grouped.setdefault(kind[0], []).append(model)
        on[row["id"]] = kind[0]
    if not grouped:
        grouped[KINDS[0][0]] = []
    # what the user took out in LiveAgent stays out
    kept: dict[str, Any] = copy.deepcopy(at.previous.get("kept") or {})
    running = pids()
    grouped, on, untouched = take(kept, olds, grouped, on, running)
    sets: dict[str, Any] = {}
    deletes: list[str] = []
    for key, api, typ, name in KINDS:
        if key in grouped:
            models = sorted(grouped[key], key=lambda m: m["id"])
            sets[f"providers.{key}"] = _merge(olds.get(key), key, api, typ, name, models, at, prior, off)
        elif key in olds and key not in untouched:
            deletes.append(f"providers.{key}")
    wrote(kept, olds, grouped, running)
    for name in ("had", "pending", "gone"):
        if not kept.get(name):
            kept.pop(name, None)
    at.record["kept"] = kept
    # a pick of OpenProgram's model follows it to its provider
    for model_key, provider_key in PICKS:
        provider = doc.get(provider_key)
        moved = on.get(str(doc.get(model_key) or ""))
        if provider in olds and moved and moved != provider:
            sets[provider_key] = moved
    if at.model in on and _default_unset(doc, "defaultModel", "defaultProvider"):
        sets["defaultModel"], sets["defaultProvider"] = at.model, on[at.model]
    # each provider is OpenProgram's whole: disconnecting takes it out however
    # LiveAgent changed it since, or puts back the one the file had before
    # (magpie ``liveAgentUnwire``); a pick goes back as a pair while it is
    # still on one of them. Once the user has deleted every one of them in
    # LiveAgent, a sync writes nothing (magpie: Sync only while
    # liveAgentWired); connecting again here brings them back
    ours = {"in": [kind[0] for kind in KINDS]}
    if at.syncing and not olds:
        return []
    return [Write(path, "jsonc", sets=sets, deletes=deletes, listing=True,
                  owns=tuple(key for key in sets if key.startswith("providers.")),
                  picks=tuple(Pick(pair, pair[1], ours) for pair in PICKS))]


#: LiveAgent's picks, each a model and the provider it is on (magpie ``liveAgentPicks``).
PICKS = (("defaultModel", "defaultProvider"), ("compactModel", "compactProvider"), ("taskModel", "taskProvider"))


def _unwire(record: dict[str, Any]) -> None:
    """A pick still on one of OpenProgram's providers that OpenProgram didn't
    write (the user picked it in LiveAgent) is reset as K-brain's own (magpie
    ``liveAgentUnwire``): to its demo's ``model1`` while that is there or no
    other provider is, the default model to none (K-brain then takes the
    first), others taken out. One OpenProgram wrote goes back by its stash."""
    text = ConfigText.read(config_path())
    if not text.exists:
        return
    doc = load("jsonc", text.text, str(text.path))
    providers = doc.get("providers") if isinstance(doc, dict) and isinstance(doc.get("providers"), dict) else {}
    keys = {kind[0] for kind in KINDS}
    if not keys & set(providers):
        return
    only: set[str] = set()
    for key in keys & set(providers):
        entry = providers[key]
        only |= {str(m.get("id")) for m in (entry.get("models") or []) if isinstance(m, dict)} \
            if isinstance(entry, dict) else set()
    left = 0
    for key, entry in providers.items():
        if key in keys:
            continue
        left += 1
        if isinstance(entry, dict):
            only -= {str(m.get("id")) for m in (entry.get("models") or []) if isinstance(m, dict)}
    demo = isinstance(providers.get(DEMO), dict) and any(
        isinstance(m, dict) and m.get("id") == DEMO_MODEL for m in providers[DEMO].get("models") or [])
    originals = (((record.get("files") or {}).get(str(text.path)) or {}).get("originals") or {})
    sets: dict[str, Any] = {}
    deletes: list[str] = []
    for model_key, provider_key in PICKS:
        if provider_key in originals:
            continue
        model, provider = str(doc.get(model_key) or ""), str(doc.get(provider_key) or "")
        if provider not in keys and (provider or model not in only):
            continue
        if provider:
            deletes.append(provider_key)
        if model_key != "taskModel" and (left == 0 or demo):
            sets[model_key] = DEMO_MODEL
        elif model_key == "defaultModel":
            sets[model_key] = ""
        else:
            deletes.append(model_key)
    if sets or deletes:
        text.write(edit.edit("jsonc", text.text, sets=sets, deletes=deletes, where=str(text.path)))


def _notice() -> str:
    from .running import any_running
    if sys.platform == "win32" or any_running(*PROCESSES):
        return ("LiveAgent keeps its settings in memory while it runs and writes them all back when one changes — "
                "quit LiveAgent before changing this, then open it and pick an OpenProgram model in its model picker.")
    return ""


def _windows_app() -> bool:
    if sys.platform != "win32":
        return False
    return any((Path(os.environ[name]) / "LiveAgent" / "liveagent.exe").is_file()
               for name in ("LOCALAPPDATA", "ProgramW6432", "ProgramFiles") if os.environ.get(name))


APP = App("com.xiaofei.liveagent", ("LiveAgent.app",))


def _detect() -> bool:
    from .base import app_bundles
    return folder().is_dir() or (Path.home() / ".liveagent").is_dir() or bool(app_bundles(APP)) or _windows_app()


def agent() -> Agent:
    return Agent(
        id="liveagent", name="LiveAgent", icon="liveagent", aliases=("live-agent", "k-brain", "kbrain"),
        kind="app", protocol="openai-chat", dir=folder, path=config_path, fields=[Field("model", "Model")],
        named_key=True, before_copy=True, wire=_wire, unwire=_unwire, notice=_notice, detect=_detect,
        detect_only=True,
        # LiveAgent edits all of it in its own settings and writes it all
        # back, so a change there is no drift and leaves the next sync to run
        loose=tuple(f"providers.{kind[0]}" for kind in KINDS),
    )
