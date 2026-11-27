"""OpenCode's config shape, shared by OpenCode and the agents that keep it.

After yetone/magpie ``internal/agent/agents.go`` (``openCodeLike``,
``magpieProviderJSONAt``'s "opencode" shape, ``openCodeVariants``,
``openCodeKept``, ``openCodeReasons``, ``openCodeLimit``), ``opencode_v2.go``
and ``opencode_providers.go`` (MIT License, Copyright (c) yetone).

OpenCode keeps its providers in ``opencode.json``: OpenProgram's is a block
under ``provider.openprogram`` with ``npm: @ai-sdk/openai-compatible``, the
gateway's Chat Completions as ``options.baseURL`` and its key as
``options.apiKey``, and every enabled model listed with its window, its
input kinds and its reasoning levels (``variants``). A model is named
``openprogram/<provider>/<model>`` in ``model`` and ``small_model``.

OpenCode 2 reads OpenCode 1's config and turns it into its own words as it
loads it (packages/core/src/config/normalize.ts at v2.2.6): ``provider`` into
``providers``, ``small_model`` into ``agents.title.model``, each with a WARN
per model for what it no longer reads. For an OpenCode 2 the file is written
in OpenCode 2's words (:func:`native`); for OpenCode 1, a version not known,
or a fork (MiMo Code, DevEco Code), in OpenCode 1's, which every OpenCode
reads.

What the user added to OpenProgram's provider stays (magpie ``theirsKept``):
a header, a timeout, their own ``setCacheKey``, a variant they put on one of
its models, a ``limit.output`` they typed (``openCodeOutputsKept``). The
models themselves are OpenProgram's, written whole, so the list follows the
catalog. The provider is OpenProgram's own object (``Write.owns``): what the
user added inside it is not drift, and disconnecting takes it out whole.

An OpenCode that loads plugins (1.4.0, 2.0.5 on) fetches the model list
itself as it starts, through the plugin written beside its config
(``opencode_models.js``, magpie ``opencode_discover.go``) from the gateway's
``/models?format=opencode``; its config then keeps only what the user said
of OpenProgram's models. Older ones get the list in the file, kept in step
by the catalog sync.
"""
from __future__ import annotations

import copy
import functools
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Optional

from .. import edit
from ..edit import ConfigText, ConfigUnreadable, get_path
from .base import Wiring, Write, find_tool

PROVIDER_ID = "openprogram"
LEGACY_KEY = f"provider.{PROVIDER_ID}"
NATIVE_KEY = f"providers.{PROVIDER_ID}"
TITLE_KEY = "agents.title.model"

#: Reasoning levels weakest first (magpie ``catalog.EffortRank``).
EFFORT_RANK = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
#: The input kinds OpenCode's config takes for a model (models.dev's words).
KINDS = ("text", "image", "audio", "video", "pdf")
#: The name of a fast variant, alone or after a level's.
FAST = "fast"


def config_home() -> Path:
    """``$XDG_CONFIG_HOME``, else ``~/.config`` (OpenCode reads it on every system)."""
    value = os.environ.get("XDG_CONFIG_HOME", "").strip()
    return Path(value).expanduser() if value and os.path.isabs(value) else Path.home() / ".config"


def config_file(folder: Path, agent_id: str) -> Path:
    """The first of the agent's config files there is, as the agent looks for
    them; with none, a new ``<id>.json`` (magpie ``openCodeLike``)."""
    for name in (f"{agent_id}.jsonc", f"{agent_id}.json", "config.json"):
        if (folder / name).is_file():
            return folder / name
    return folder / f"{agent_id}.json"


def ref(model: str) -> str:
    """A model of OpenProgram's as OpenCode names it: its provider, then the model."""
    return f"{PROVIDER_ID}/{model}"


# Versions (opencode_v2.go, opencode_discover.go)

def version_parts(value: str) -> Optional[tuple[int, int, int]]:
    """``value``'s major, minor and patch; ``None`` for one that doesn't start with them."""
    parts = (value or "").strip().removeprefix("v").split(".", 2)
    if len(parts) < 2:
        return None
    numbers = []
    for part in parts:
        digits = re.match(r"\d*", part).group(0)
        if not digits:
            return None
        numbers.append(int(digits))
    while len(numbers) < 3:
        numbers.append(0)
    return numbers[0], numbers[1], numbers[2]


@functools.lru_cache(maxsize=8)
def _version_of(path: str, _mtime: float) -> str:
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    found = re.search(r"\d+\.\d+(\.\d+)?", out or "")
    return found.group(0) if found else ""


def installed_version(bin_name: str = "opencode") -> str:
    """The version of the command on PATH, ``""`` when not known."""
    path = find_tool(bin_name)
    if not path:
        return ""
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        return ""
    return _version_of(path, mtime)


def is_two(value: str) -> bool:
    """Whether OpenCode of version ``value`` is 2.x (magpie ``openCodeTwo``)."""
    parts = version_parts(value)
    return parts is not None and parts[0] >= 2


# The provider block in OpenCode 1's words (magpieProviderJSONAt "opencode")

def by_strength(efforts: list[str]) -> list[str]:
    """Levels weakest first; one not ranked keeps its place after them (magpie ``ByStrength``)."""
    rank = {name: i for i, name in enumerate(EFFORT_RANK)}
    return sorted(efforts, key=lambda e: rank.get(e, len(EFFORT_RANK)))


def variant(name: str) -> dict[str, Any]:
    """The variant written under ``name`` (magpie ``openCodeVariant``): a level asked
    as ``reasoningEffort``, which @ai-sdk/openai-compatible sends as
    ``reasoning_effort`` and OpenCode 2 reads too; a fast one asks for
    priority processing as well, in ``extraBody`` for OpenCode 2, which drops
    an option it doesn't know."""
    level, fast = (name[: -len("-" + FAST)], True) if name.endswith("-" + FAST) else (name, False)
    if name == FAST:
        level, fast = "", True
    out: dict[str, Any] = {}
    if level:
        out["reasoningEffort"] = level
    if fast:
        out["service_tier"] = "priority"
        out["extraBody"] = {"service_tier": "priority"}
    return out


def variants(efforts: list[str], fast: bool = False) -> dict[str, Any]:
    """The reasoning levels OpenCode offers for a model (magpie ``openCodeVariants``).

    OpenCode 2 makes low, medium and high for every model of an
    openai-compatible provider whose config names no variants, so a model
    whose levels are other ones, or that has none, is given its own: an
    empty set is taken as none. Written weakest first: OpenCode and
    OpenChamber list a model's variants in the object's key order."""
    out: dict[str, Any] = {}
    for effort in by_strength([e for e in efforts if isinstance(e, str) and e]):
        out.setdefault(effort, variant(effort))
    if not fast:
        return out
    if not out:
        return {FAST: variant(FAST)}
    for level in list(out):
        out[f"{level}-{FAST}"] = variant(f"{level}-{FAST}")
    return out


def reasons(model: dict[str, Any]) -> bool:
    """Whether the model is marked ``reasoning`` (magpie ``openCodeReasons``).

    OpenCode 1 shows it in its tooltip and adds low, medium and high of its
    own to the variants of a reasoning model, so one with levels is marked
    only when it has those three; one with none when it thinks at all."""
    levels = list(model.get("thinking_levels") or [])
    if not levels:
        return bool(model.get("reasoning"))
    return all(level in levels for level in ("low", "medium", "high"))


def limit(model: dict[str, Any]) -> dict[str, Any]:
    """The model's window and reply limit; without it OpenCode doesn't know
    when to compact. An output of 0 is OpenCode's own default; one above the
    window is cut to it (magpie ``openCodeLimit``, ``OutputWithin``)."""
    window = int(model.get("context_window") or 0)
    output = int(model.get("max_tokens") or 0)
    if window and output > window:
        output = window
    return {"context": window, "output": output}


def inputs(model: dict[str, Any]) -> Optional[list[str]]:
    """What the model takes in, in OpenCode's words and order: text, image
    when it sees, and the audio, video and pdf its catalog lists; ``None``
    for text alone (magpie ``openCodeInputs``)."""
    listed = set(model.get("input") or [])
    kinds = [kind for kind in KINDS
             if kind == "text" or (kind == "image" and (model.get("vision") or "image" in listed))
             or (kind not in ("text", "image") and kind in listed)]
    return kinds if len(kinds) > 1 else None


def provider_block(models: list[dict[str, Any]], base_url: str, key: str) -> dict[str, Any]:
    """OpenProgram's provider in OpenCode 1's words, models in the catalog's order.

    ``setCacheKey``: without it OpenCode sends no prompt_cache_key, and an
    upstream that caches by it misses every turn."""
    listed: dict[str, Any] = {}
    for model in models:
        model_id = model.get("id") or ""
        if not model_id:
            continue
        entry: dict[str, Any] = {"name": model.get("name") or model_id}
        kinds = inputs(model)
        if kinds:
            # modalities alone say a model takes images: OpenCode 2 drops the
            # legacy attachment flag with a WARN per model
            entry["modalities"] = {"input": kinds, "output": ["text"]}
        if model.get("context_window"):
            entry["limit"] = limit(model)
        if reasons(model):
            entry["reasoning"] = True
        entry["variants"] = variants(list(model.get("thinking_levels") or []))
        # OpenCode counts a session's cost by it, in USD per million tokens
        # (models.dev's cost), and counted 0 without it
        if model.get("price"):
            price = model["price"]
            entry["cost"] = {"input": price["input"], "output": price["output"],
                             "cache_read": price["cache_read"], "cache_write": price["cache_write"]}
        listed[model_id] = entry
    return {"npm": "@ai-sdk/openai-compatible", "name": "OpenProgram",
            "options": {"baseURL": base_url, "apiKey": key, "setCacheKey": True}, "models": listed}


# The same block in OpenCode 2's words (opencode_providers.go)

#: The options of an @ai-sdk/openai-compatible model OpenCode 2 reads itself;
#: any other a variant has goes in the request's body as it is.
SETTINGS = ("apiKey", "baseURL", "headers", "body", "extraBody", "timeout", "chunkTimeout", "headerTimeout",
            "disabled", "reasoningEffort", "textVerbosity", "user", "strictJsonSchema")


def native_variant(name: str, options: Any) -> dict[str, Any]:
    """A variant as OpenCode 2 has it: ``{id, settings, body?}`` (magpie ``openCodeNativeVariant``)."""
    options = options if isinstance(options, dict) else {}
    settings = {k: v for k, v in options.items() if k in SETTINGS}
    body = {k: v for k, v in options.items() if k not in SETTINGS}
    out: dict[str, Any] = {"id": name, "settings": settings}
    if body:
        out["body"] = body
    return out


def native(block: dict[str, Any]) -> dict[str, Any]:
    """The block as OpenCode 2 reads it from ``provider`` (migrate.ts,
    migrateStandardProvider; magpie ``openCodeNative``): ``npm`` is ``package
    "aisdk:<npm>"``, options are ``settings``, ``setCacheKey`` is
    ``compatibility.supportsPromptCacheKey``, a model's modalities are
    ``capabilities``, its variants a list; ``reasoning`` goes."""
    out: dict[str, Any] = {"name": block.get("name")}
    if block.get("npm"):
        out["package"] = "aisdk:" + block["npm"]
    options = block.get("options")
    if isinstance(options, dict):
        options = dict(options)
        if "setCacheKey" in options:
            out["compatibility"] = {"supportsPromptCacheKey": options.pop("setCacheKey")}
        out["settings"] = options
    models = block.get("models")
    if not isinstance(models, dict):
        return out
    listed: dict[str, Any] = {}
    for model_id, model in models.items():
        entry: dict[str, Any] = {"name": model.get("name")}
        modalities = model.get("modalities")
        if isinstance(modalities, dict):
            entry["capabilities"] = {"tools": True, "input": modalities.get("input"),
                                     "output": modalities.get("output")}
        if "limit" in model:
            entry["limit"] = model["limit"]
        # a cost is a list of tiers in OpenCode 2, its cache prices under cache
        cost = model.get("cost")
        if isinstance(cost, dict):
            entry["cost"] = [{"input": cost.get("input"), "output": cost.get("output"),
                              "cache": {"read": cost.get("cache_read"), "write": cost.get("cache_write")}}]
        if isinstance(model.get("variants"), dict):
            entry["variants"] = [native_variant(name, opts) for name, opts in model["variants"].items()]
        listed[model_id] = entry
    out["models"] = listed
    return out


# What the user added stays (sync.go theirsKept, openCodeKept, openCodeNativeKept)

def merge_theirs(have: Any, want: Any, whole: tuple[str, ...] = ()) -> Any:
    """``want`` with each key of ``have`` it lacks, objects in both merged
    alike; a key in ``whole`` is ``want``'s as it is (magpie ``mergeTheirs``)."""
    if not isinstance(have, dict) or not isinstance(want, dict):
        return want
    out = dict(want)
    for key, value in have.items():
        if key not in want:
            out[key] = value
        elif key not in whole and isinstance(value, dict) and isinstance(want[key], dict):
            out[key] = merge_theirs(value, want[key])
    return out


def kept(doc: dict[str, Any], block: dict[str, Any]) -> dict[str, Any]:
    """``block`` as written over the one ``doc`` has under ``provider.openprogram``:
    a setCacheKey already there is the user's, and so is a variant they put
    on one of the models, where it is or after OpenProgram's."""
    block = copy.deepcopy(block)
    present, have = get_path(doc, LEGACY_KEY)
    if not present or not isinstance(have, dict):
        return block
    if get_path(doc, LEGACY_KEY + ".options.setCacheKey")[0]:
        block.get("options", {}).pop("setCacheKey", None)
    for model_id, model in (have.get("models") or {}).items():
        ours = block["models"].get(model_id)
        theirs = model.get("variants") if isinstance(model, dict) else None
        if not isinstance(ours, dict) or not isinstance(theirs, dict):
            continue
        for name, options in theirs.items():
            if options != variant(name):
                ours.setdefault("variants", {})[name] = options
    return merge_theirs(have, block, whole=("models",))


def _own_native_variant(item: Any) -> bool:
    """A variant under ``providers`` that says what OpenProgram's of its name
    says and nothing else, its options split as OpenCode 2 splits them or all
    in settings (magpie ``openCodeOwnVariant``)."""
    if not isinstance(item, dict) or not isinstance(item.get("id"), str):
        return False
    options: dict[str, Any] = {}
    for key, part in item.items():
        if key == "id":
            continue
        if key not in ("settings", "body") or not isinstance(part, dict):
            return False
        for name, value in part.items():
            if name in options:
                return False
            options[name] = value
    return options == variant(item["id"])


def from_v1(block: Any) -> Optional[dict[str, Any]]:
    """A provider in OpenCode 1's words in OpenCode 2's, as OpenCode 2.2.6
    reads it (migrate.ts migrateStandardProvider; magpie ``openCodeFromV1``):
    ``npm`` is ``package "aisdk:<npm>"``, ``api`` a baseURL in settings,
    options settings but for headers and body, which are the provider's own,
    and ``setCacheKey`` compatibility's ``supportsPromptCacheKey``. Of a model
    the parts OpenProgram's models leave to the user: its name, limit and
    variants. ``None`` for one OpenCode doesn't read."""
    if not isinstance(block, dict):
        return None
    options = block.get("options")
    if options is not None and not isinstance(options, dict):
        return None
    out: dict[str, Any] = {}
    for key in ("name", "env"):
        if key in block:
            out[key] = block[key]
    if isinstance(block.get("npm"), str) and block["npm"]:
        out["package"] = "aisdk:" + block["npm"]
    if options and "setCacheKey" in options:
        out["compatibility"] = {"supportsPromptCacheKey": options["setCacheKey"]}
    if options is not None or block.get("api"):
        settings = {k: v for k, v in (options or {}).items() if k not in ("setCacheKey", "headers", "body")}
        if block.get("api"):
            settings["baseURL"] = block["api"]
        out["settings"] = settings
    for key in ("headers", "body"):
        if options and key in options:
            out[key] = options[key]
    models = block.get("models")
    if isinstance(models, dict):
        listed: dict[str, Any] = {}
        for model_id, model in models.items():
            if not isinstance(model, dict):
                continue
            entry = {key: model[key] for key in ("name", "limit") if key in model}
            if isinstance(model.get("variants"), dict):
                entry["variants"] = [native_variant(name, opts) for name, opts in model["variants"].items()
                                     if isinstance(opts, dict)]
            listed[model_id] = entry
        out["models"] = listed
    return out


def native_have(doc: dict[str, Any]) -> Optional[dict[str, Any]]:
    """OpenProgram's provider as OpenCode 2 reads the file: the one under
    ``providers``, else the one under ``provider`` in its words, which it
    becomes when the file moves to OpenCode 2's (magpie ``moveProvider``)."""
    present, have = get_path(doc, NATIVE_KEY)
    if present:
        return have if isinstance(have, dict) else None
    return from_v1(get_path(doc, LEGACY_KEY)[1])


def native_kept(doc: dict[str, Any], block: dict[str, Any]) -> dict[str, Any]:
    """:func:`kept` for the block under ``providers.openprogram``; where the
    file has it under ``provider`` alone, that one carries over."""
    out = native(block)
    have = native_have(doc)
    if have is None:
        return out
    if get_path(have, "compatibility.supportsPromptCacheKey")[0]:
        compatibility = out.get("compatibility") or {}
        compatibility.pop("supportsPromptCacheKey", None)
        if not compatibility:
            out.pop("compatibility", None)
    for model_id, model in (have.get("models") or {}).items():
        ours = out.get("models", {}).get(model_id)
        theirs = model.get("variants") if isinstance(model, dict) else None
        if not isinstance(ours, dict) or not isinstance(theirs, list):
            continue
        listed = ours.setdefault("variants", [])
        for item in theirs:
            if _own_native_variant(item) or not isinstance(item, dict) or not item.get("id"):
                continue
            at = next((i for i, o in enumerate(listed) if o.get("id") == item["id"]), -1)
            if at >= 0:
                listed[at] = item
            else:
                listed.append(item)
    return merge_theirs(have, out, whole=("models",))


# The write

def read_doc(path: Path) -> dict[str, Any]:
    """The config as it is now; a file that doesn't parse is left alone."""
    text = ConfigText.read(path)
    return edit.load("jsonc", text.text, str(path)) if text.text.strip() else {}


def file_format(path: Path) -> str:
    return "jsonc" if path.suffix == ".jsonc" else "json"


def discovers(value: str) -> bool:
    """Whether OpenCode of version ``value`` loads OpenProgram's model plugin:
    OpenCode 1 from 1.4.0, whose ``{id, server}`` plugins' config hook runs
    before it reads its providers, OpenCode 2 from 2.0.5, whose setup has
    ``ctx.model.transform`` (magpie ``openCodeDiscovers``). An OpenCode older
    than those, or of a version not known, gets the list in its config."""
    parts = version_parts(value)
    if parts is None or parts[0] == 0:
        return False
    return parts >= ((1, 4, 0) if parts[0] == 1 else (2, 0, 5))


# A provider of the user's own on the gateway (agents.go ownGatewayProvider)

def same_gateway(base: Any, v1: str) -> bool:
    """Whether a base URL is the gateway's, its host spelled 127.0.0.1 or localhost."""
    def norm(url: str) -> str:
        return url.strip().rstrip("/").replace("://localhost:", "://127.0.0.1:", 1)
    return isinstance(base, str) and bool(base) and norm(base) == norm(v1)


def own_gateway_provider(doc: dict[str, Any], model: str, v1: str) -> str:
    """The provider in the config, other than OpenProgram's, whose baseURL is
    the gateway's and that lists ``model``, as a layout of one provider per
    family of models has; ``""`` when there is none. One under ``providers``
    is read as OpenCode 2 reads it, before the same name under ``provider``."""
    found: dict[str, tuple[Any, Any]] = {}
    for name, entry in (doc.get("provider") or {}).items() if isinstance(doc.get("provider"), dict) else []:
        if isinstance(entry, dict):
            found[name] = ((entry.get("options") or {}).get("baseURL"), entry.get("models"))
    for name, entry in (doc.get("providers") or {}).items() if isinstance(doc.get("providers"), dict) else []:
        if isinstance(entry, dict):
            found[name] = ((entry.get("settings") or {}).get("baseURL"), entry.get("models"))
    for name in sorted(found):
        base, models = found[name]
        if name != PROVIDER_ID and isinstance(models, dict) and model in models and same_gateway(base, v1):
            return name
    return ""


def pick(doc: dict[str, Any], model: str, v1: str) -> tuple[str, bool]:
    """The value that makes the config send ``model``, and whether it is a
    provider of the user's own: one already sending it to the gateway is
    named rather than a second list of the same models (magpie ``openCodeRef``)."""
    own = own_gateway_provider(doc, model, v1)
    return (f"{own}/{model}", True) if own else (ref(model), False)


# The models found by OpenCode itself (opencode_discover.go)

#: Opens the plugin OpenProgram writes; a file at its path without it is the
#: user's own, never written over or removed.
PLUGIN_MARK = "// openprogram: written by OpenProgram"
PLUGIN_FILE = "openprogram-models.js"


def plugin_path(config: Path) -> Path:
    """OpenProgram's plugin beside the OpenCode config at ``config``: OpenCode
    loads every ``{plugin,plugins}/*.{js,ts}`` in its config folders."""
    return config.parent / "plugins" / PLUGIN_FILE


def model_list(block: dict[str, Any]) -> list[dict[str, Any]]:
    """A block's models as the plugin reads them: ``[{id, entry}]``, a list so
    the order stays the catalog's (a JSON object's integer-like keys come
    first in JavaScript)."""
    return [{"id": model_id, "entry": entry} for model_id, entry in (block.get("models") or {}).items()]


def served_models(models: list[dict[str, Any]], two: bool) -> list[dict[str, Any]]:
    """The gateway's ``/models?format=opencode`` (``opencode2``): every enabled
    model as OpenCode's config lists it under OpenProgram's provider."""
    block = provider_block(models, "", "")
    return model_list(native(block) if two else block)


def plugin_source(block: dict[str, Any]) -> str:
    """The plugin for ``block``: the gateway's address and key, and the models
    it lists when the gateway doesn't answer."""
    import json
    options = block.get("options") or {}
    data = json.dumps({"baseURL": options.get("baseURL"), "apiKey": options.get("apiKey"),
                       "v1": model_list(block), "v2": model_list(native(block))}, ensure_ascii=False)
    template = (Path(__file__).parent / "opencode_models.js").read_text(encoding="utf-8")
    return template.replace("__OPENPROGRAM__", data, 1)


def plugin_ours(path: Path) -> bool:
    """No file there, or one OpenProgram wrote."""
    try:
        return path.read_text(encoding="utf-8").lstrip("\ufeff").startswith(PLUGIN_MARK)
    except FileNotFoundError:
        return True
    except OSError:
        return False


def json_delta(have: Any, base: Any) -> tuple[Any, bool]:
    """What ``have`` says other than ``base``: of objects the keys whose values
    differ, alike; of lists the items ``base`` lacks (magpie ``jsonDelta``)."""
    if isinstance(have, dict) and isinstance(base, dict):
        out = {}
        for key, value in have.items():
            if key not in base:
                out[key] = value
                continue
            delta, keep = json_delta(value, base[key])
            if keep:
                out[key] = delta
        return out, bool(out)
    if isinstance(have, list) and isinstance(base, list):
        out_list = [item for item in have if item not in base]
        return out_list, bool(out_list)
    return have, have != base


#: How many reply limits a model's record of those OpenProgram wanted keeps.
OUTPUTS_REMEMBERED = 8


def outputs_kept(block: dict[str, Any], have: dict[str, int],
                 wanted: dict[str, list[int]]) -> tuple[dict[str, Any], dict[str, list[int]]]:
    """``block`` (OpenCode 1's words) with a ``limit.output`` the user typed
    into one of OpenProgram's models kept, and the record of the reply limits
    OpenProgram has wanted there (magpie ``outputsKept``,
    ``openCodeOutputsKept``). A value in the file that is none OpenProgram
    ever wanted is the user's; one with no record yet (a model new to the
    file) is OpenProgram's."""
    block = copy.deepcopy(block)
    following: dict[str, list[int]] = {}
    for model_id, entry in (block.get("models") or {}).items():
        lim = entry.get("limit") if isinstance(entry, dict) else None
        want = int((lim or {}).get("output") or 0)
        if not want:
            continue
        was = [n for n in wanted.get(model_id) or [] if isinstance(n, int)]
        found = have.get(model_id) or 0
        if model_id in wanted and found > 0 and found != want and found not in was:
            lim["output"] = found
        if want not in was:
            was = [*was, want][-OUTPUTS_REMEMBERED:]
        if was:
            following[model_id] = was
    return block, following


def outputs_in(doc: dict[str, Any]) -> dict[str, int]:
    """Each of OpenProgram's models' ``limit.output`` in the file, under
    ``provider``, or ``providers`` where the file keeps it there alone."""
    present, models = get_path(doc, LEGACY_KEY + ".models")
    if not present:
        present, models = get_path(doc, NATIVE_KEY + ".models")
    out: dict[str, int] = {}
    for model_id, model in (models or {}).items() if isinstance(models, dict) else []:
        lim = model.get("limit") if isinstance(model, dict) else None
        if isinstance(lim, dict) and isinstance(lim.get("output"), (int, float)) and not isinstance(lim["output"], bool):
            out[model_id] = int(lim["output"])
    return out


def models_left(have: Optional[dict[str, Any]], kept_block: dict[str, Any], pure: dict[str, Any],
                native_words: bool, moved: bool) -> Optional[dict[str, Any]]:
    """The models left in the file's provider ``have`` once OpenCode finds the
    list itself: of each model OpenProgram lists, the fields of its entry as
    a sync keeps it (``kept_block``: a variant, a limit.output the user typed)
    that say other than its own (``pure``), which the plugin lays over the
    gateway's; a model it doesn't list was OpenProgram's while the file held
    the whole list and goes the first time (``moved`` false), after that it
    is the user's and stays (magpie ``modelsLeft``)."""
    models = (have or {}).get("models")
    if not isinstance(models, dict):
        return None
    ours = pure.get("models") or {}
    kept_by = kept_block.get("models") or {}
    out: dict[str, Any] = {}
    for model_id, current in models.items():
        if model_id not in ours:
            if moved:
                out[model_id] = current
            continue
        current = kept_by.get(model_id, current)
        delta, keep = json_delta(current, ours[model_id])
        if not keep:
            continue
        if isinstance(delta, dict) and isinstance(current, dict):
            # a limit or cost the user changed stays whole: OpenCode refuses
            # one without its context and output
            for part in ("limit", "cost"):
                if part in delta and current.get(part) is not None:
                    delta[part] = current[part]
            if native_words and delta.get("variants") is None:
                # with none to say, OpenCode 2 would make up its own
                delta["variants"] = []
        out[model_id] = delta
    return out or None


def _found(block: dict[str, Any], left: Optional[dict[str, Any]], key: str, inside: list[str]) -> dict[str, Any]:
    """``block`` without its models, but for what the user said of them;
    with none left, the file's go too (``inside``: a delete inside the
    provider OpenProgram owns)."""
    out = {k: v for k, v in block.items() if k != "models"}
    if left:
        out["models"] = left
    else:
        inside.append(key + ".models")
    return out


def _moved(path: Path) -> bool:
    """The file's list was taken out before: OpenProgram's plugin is beside
    it, as it is only while the OpenCode finds the models itself (magpie's
    ``opencode-discover`` stash word)."""
    plugin = plugin_path(path)
    return plugin.is_file() and plugin_ours(plugin)


def _loose(key: str) -> tuple[str, ...]:
    """What inside OpenProgram's provider is the user's to change without it
    being drift: a model's variants and its reply limit, which a sync keeps."""
    return (f"{key}.models.*.variants", f"{key}.models.*.limit.output")


def cleared(doc: dict[str, Any], previous: dict[str, Any], groups: list[tuple[str, ...]]) -> list[str]:
    """The deletes that take out what OpenProgram wrote before and isn't
    picked now (magpie ``set("")`` of a field): of each group, its keys when
    the first still holds what OpenProgram wrote there; one changed since is
    the user's and stays. An object OpenProgram added to hold them goes too
    once nothing else is in it."""
    wrote = previous.get("wrote") or {}
    after = copy.deepcopy(doc)
    out: list[str] = []
    for group in groups:
        written = wrote.get(group[0]) or {}
        if "set" not in written or get_path(doc, group[0]) != (True, written["set"]):
            continue
        for key in group:
            if get_path(after, key)[0]:
                out.append(key)
                edit.delete_path(after, key)
    if not out:
        return out
    for parent in sorted(previous.get("parents") or [], key=len, reverse=True):
        present, value = get_path(after, parent)
        if present and value == {}:
            out.append(parent)
            edit.delete_path(after, parent)
    return out


def opencode_writes(path: Path, at: Wiring, *, two: bool, discover: bool = False,
                    picks: bool = True) -> list[Write]:
    """OpenProgram's provider, and with ``picks`` the model and small model,
    in the OpenCode config at ``path``, in OpenCode 2's words when ``two``.

    For OpenCode 1 the block goes under ``provider``, and where the file
    keeps it under ``providers`` too (OpenChamber's form saves it there,
    which OpenCode 2 reads instead), there as well. For OpenCode 2 it goes
    under ``providers`` alone, carrying over what the user added to the one
    under ``provider``, which goes, and the small model is the title agent's
    (opencode_v2.go ``moveProvider``, ``setSmall``). A small model left
    empty is the agent's own: one OpenProgram wrote before goes.

    Where the OpenCode finds the models itself (``discover``), the provider
    is written without them, and the plugin that fetches them beside it. A
    pick that a provider of the user's own already sends to the gateway is
    named there, and the provider is written only for a pick that needs it.

    The provider is the model list a sync rewrites; the picks are a write of
    their own on the same file, left as the user changes them. Both are
    ``shared``: OpenChamber names OpenProgram's provider in OpenCode's own
    file, which stays while either agent is connected."""
    doc = read_doc(path)
    v1 = at.base("openai-chat")
    previous = (at.previous.get("files") or {}).get(str(path)) or {}
    sets: dict[str, Any] = {}
    deletes: list[str] = []
    needs = not picks
    if picks:
        model, own = pick(doc, at.model, v1)
        needs = needs or not own
        sets["model"] = model
        small = at.values.get("small") or ""
        if small:
            small, own = pick(doc, small, v1)
            needs = needs or not own
            if two:
                sets[TITLE_KEY] = small
                deletes.append("small_model")
            else:
                sets["small_model"] = small
        else:
            deletes.extend(cleared(doc, previous, [(TITLE_KEY,), ("small_model",)]))
    writes: list[Write] = []
    provider: dict[str, Any] = {}
    gone: list[str] = []
    if needs:
        block = provider_block(at.models, v1, at.key)
        # a limit.output the user typed stays (openCodeOutputsKept)
        own_outputs, at.record["outputs"] = outputs_kept(block, outputs_in(doc), at.previous.get("outputs") or {})
        moved = discover and _moved(path)
        if two:
            native_block = native_kept(doc, own_outputs)
            if discover:
                native_block = _found(native_block, models_left(native_have(doc), native_block, native(block),
                                                                True, moved), NATIVE_KEY, gone)
            provider[NATIVE_KEY] = native_block
            if get_path(doc, LEGACY_KEY)[0]:
                # and ``provider`` with it where OpenProgram added it and
                # nothing else is in it (magpie dropEmpty)
                rest = doc.get("provider")
                alone = isinstance(rest, dict) and set(rest) == {PROVIDER_ID}
                gone.append("provider" if alone and "provider" in (previous.get("parents") or []) else LEGACY_KEY)
        else:
            legacy = kept(doc, own_outputs)
            if discover:
                legacy = _found(legacy, models_left(get_path(doc, LEGACY_KEY)[1], legacy, block, False, moved),
                                LEGACY_KEY, gone)
            provider[LEGACY_KEY] = legacy
            if get_path(doc, NATIVE_KEY)[0]:
                native_block = native_kept(doc, block)
                if discover:
                    native_block = _found(native_block, models_left(native_have(doc), native_block, native(block),
                                                                    True, moved), NATIVE_KEY, gone)
                provider[NATIVE_KEY] = native_block
    fmt = file_format(path)
    if provider:
        loose = tuple(inner for key in provider for inner in _loose(key))
        writes.append(Write(path, fmt, sets=provider, deletes=gone, listing=True, shared=True,
                            owns=tuple(provider), loose=loose))
    if sets or deletes:
        writes.append(Write(path, fmt, sets=sets, deletes=deletes, shared=True))
    if needs:
        plugin = plugin_path(path)
        if discover and plugin_ours(plugin):
            writes.append(Write(plugin, "file", content=plugin_source(block), listing=True, shared=True))
    return writes



def unreadable(path: Path, why: str) -> ConfigUnreadable:
    return ConfigUnreadable(f"{path}: {why}")
