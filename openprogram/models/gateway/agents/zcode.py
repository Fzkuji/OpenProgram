"""ZCode (``~/.zcode/v2/config.json`` and ``provider_config.json``).

After yetone/magpie ``internal/agent/zcode.go`` and ``internal/appdir/
zcode.go`` (MIT License, Copyright (c) yetone). ZCode (Zhipu's desktop app)
keeps its model providers in ``~/.zcode/v2/config.json`` (under the folder the
user moved its data to, when they did: ``dataBaseDir`` in the home folder's
``.zcode/v2/setting.json``), OpenCode's provider shape with a kind of its own::

    {"provider":{"openprogram":{"name":"OpenProgram","kind":"anthropic",
      "options":{"apiKey":"…","baseURL":"http://127.0.0.1:18100/gateway/anthropic"},
      "enabled":true,"source":"custom",
      "models":{"<id>":{"name":…,"limit":{"context":…,"output":…},"modalities":{…},
        "reasoning":{"enabled":true,"levels":[…],"defaultLevel":…,
          "providerOptionsByLevel":{"<level>":{"thinking":{…}}}}}}}}}

An anthropic provider is asked at baseURL + ``/v1/messages``. The model is
picked per task in ZCode's own picker and kept in its window, not in a file,
so what connecting sets is whether OpenProgram's models are in that picker.

ZCode 3.14 moved its providers to ``provider_config.json`` and reads
config.json's only once, to import them. There a provider is a rule, and a
model's context window and inputs are rules of their own; both files are
written, so an older ZCode sees the models too. A model the user set by hand
in ZCode (a manual rule) keeps what they set. A running ZCode reads
provider_config.json again every second, so it needs no restart.

The gateway answers ZCode's three API formats at their addresses (Messages,
Chat Completions, Responses), so the format the user picked in ZCode's
provider settings is theirs: a sync keeps it in both files, and only a
provider OpenProgram adds new starts on Anthropic Messages.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from .. import edit
from ..edit import ConfigText, ConfigUnreadable
from .base import Agent, Field, Wiring, Write

PROVIDER_ID = "openprogram"
ENTRY = f"provider.{PROVIDER_ID}"
PROVIDER_RULES = "config.providerConfigRules.providerRules"
MODEL_RULES = "config.modelConfigRules.providerModelRules"
MANUAL_RULES = "config.modelConfigRules.manualProviderModelRules"
#: ZCode's API formats in provider_config.json (``api.type``) and the kind
#: each is in config.json, as ZCode maps one to the other.
KINDS = {"anthropic-messages": "anthropic", "openai-chat-completions": "openai-compatible",
         "openai-responses": "openai"}
PROTOCOLS = {"anthropic-messages": "anthropic", "openai-chat-completions": "openai-chat",
             "openai-responses": "openai-responses"}
#: Caps a model's output limit: some vendors report their context window there.
MAX_OUTPUT = 128000
#: The thinking budget ZCode sends for each level, which the gateway reads
#: back as that level (``conversion.budget_level``).
BUDGETS = {"minimal": 1024, "low": 2048, "medium": 8192, "high": 24000, "xhigh": 32768, "max": 64000}


def data_home() -> Path:
    """Where ZCode keeps its ``.zcode`` data: the home folder, unless moved in its settings."""
    user = Path.home()
    try:
        setting = json.loads((user / ".zcode" / "v2" / "setting.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return user
    moved = setting.get("dataBaseDir") if isinstance(setting, dict) else None
    if isinstance(moved, str) and moved.strip() and os.path.isabs(moved.strip()):
        return Path(moved.strip())
    return user


def home() -> Path:
    return data_home() / ".zcode"


def config_path() -> Path:
    return home() / "v2" / "config.json"


def rules_path() -> Path:
    return home() / "v2" / "provider_config.json"


def _doc(path: Path) -> tuple[ConfigText, dict[str, Any]]:
    text = ConfigText.read(path)
    return text, (edit.load("json", text.text, str(path)) if text.text.strip() else {})


def _list(doc: dict[str, Any], key: str) -> list[Any]:
    value = edit.get_path(doc, key)[1]
    return list(value) if isinstance(value, list) else []


def _mine(rule: Any) -> bool:
    return isinstance(rule, dict) and rule.get("providerId") == PROVIDER_ID


def _format(config: dict[str, Any], rules: dict[str, Any]) -> str:
    """The API format of OpenProgram's provider: the one the user picked in
    ZCode's settings, else Anthropic Messages (magpie ``zcodeFormat``). Both
    files are written together, so where they differ the user changed one of
    them in ZCode; the one written last has their choice."""
    ruled = next((r.get("config", {}).get("api", {}).get("type") for r in _list(rules, PROVIDER_RULES)
                  if _mine(r) and isinstance(r.get("config"), dict) and isinstance(r["config"].get("api"), dict)), "")
    ruled = ruled if ruled in KINDS else ""
    kind = str((edit.get_path(config, ENTRY)[1] or {}).get("kind") or "") if isinstance(
        edit.get_path(config, ENTRY)[1], dict) else ""
    kinded = next((f for f, k in KINDS.items() if k == kind), "")
    if not kinded and not ruled:
        return "anthropic-messages"
    if not kinded or not ruled or kinded == ruled:
        return kinded or ruled
    try:
        return kinded if config_path().stat().st_mtime > rules_path().stat().st_mtime else ruled
    except OSError:
        return ruled


def levels_of(row: dict[str, Any]) -> list[str]:
    """A model's reasoning levels as ZCode names them: ``disabled`` turns thinking off."""
    out: list[str] = []
    for level in row.get("thinking_levels") or []:
        level = "disabled" if level in ("none", "off") else level
        if level not in out:
            out.append(level)
    return out


def default_level(levels: list[str]) -> str:
    """medium where the model has it, else its middle level that thinks."""
    if "medium" in levels:
        return "medium"
    on = [level for level in levels if level != "disabled"]
    return on[len(on) // 2] if on else levels[0]


def options_by_level(levels: list[str], own: dict[str, Any]) -> dict[str, Any]:
    """The provider options ZCode sends for each level: thinking off for
    ``disabled``, on with the level's budget for every other (with none for a
    level ZCode asks without thinking, and an always-thinking model refuses).
    A level's options already there are the user's and stay."""
    out: dict[str, Any] = {}
    for level in levels:
        if level in own:
            out[level] = own[level]
            continue
        thinking: dict[str, Any] = {"type": "disabled"}
        if level != "disabled":
            thinking = {"type": "enabled", "budget_tokens": BUDGETS.get(level, BUDGETS["medium"])}
        out[level] = {"thinking": thinking}
    return out


def _output(row: dict[str, Any]) -> int:
    return min(int(row.get("max_tokens") or 0), MAX_OUTPUT)


def provider_entry(at: Wiring, current: Any, fmt: str, base: str) -> dict[str, Any]:
    """OpenProgram's provider in config.json; one turned off in ZCode stays off."""
    current = current if isinstance(current, dict) else {}
    old_models = current.get("models") if isinstance(current.get("models"), dict) else {}
    models: dict[str, Any] = {}
    for row in at.models:
        limit: dict[str, Any] = {"context": int(row.get("context_window") or 200000)}
        if _output(row):
            # without it ZCode caps every reply at 32000 tokens
            limit["output"] = _output(row)
        inputs = ["text", "image"] if row.get("vision") else ["text"]
        model: dict[str, Any] = {"name": row.get("name") or row["id"], "limit": limit,
                                 "modalities": {"input": inputs, "output": ["text"]}}
        levels = levels_of(row)
        if levels:
            old = (old_models.get(row["id"]) or {}).get("reasoning") if isinstance(old_models.get(row["id"]), dict) else None
            old = old if isinstance(old, dict) else {}
            was = old.get("defaultLevel")
            own = old.get("providerOptionsByLevel") if isinstance(old.get("providerOptionsByLevel"), dict) else {}
            model["reasoning"] = {"enabled": True, "levels": levels,
                                  "defaultLevel": was if was in levels else default_level(levels),
                                  "providerOptionsByLevel": options_by_level(levels, own)}
        models[row["id"]] = model
    return {"name": "OpenProgram", "kind": KINDS[fmt], "enabled": current.get("enabled") is not False,
            "source": "custom", "options": {"apiKey": at.key, "baseURL": base}, "models": models}


def _merge_kept(old: dict[str, Any], new: dict[str, Any], replace: tuple[str, ...] = ()) -> dict[str, Any]:
    """``new`` with every key ``old`` had that ``new`` doesn't set, except ``replace`` (magpie ``mergeKept``)."""
    out = dict(new)
    for key, value in old.items():
        if key not in out and key not in replace:
            out[key] = value
    return out


# Native web search (magpie zcodeWebSearch)
#
# ZCode offers a model its WebSearch tool only where the model's rule says it
# has native web search (``properties.supportsNativeWebSearch``, 原生联网搜索
# in a model's settings), off by default. Its tool then asks the model again
# with Anthropic's web_search tool, which only an Anthropic Messages provider
# is sent: on another API format ZCode fails the search. The gateway has the
# provider search for such a request where the provider can
# (``conversion.searches``), so native web search is turned on for each such
# model OpenProgram lists while the format is Anthropic Messages.
#
# ZCode writes a change to a model with its recommended config into the
# model's rule and leaves a value equal to its default out of it: unticking
# native web search takes the key out. So the models OpenProgram turned it on
# for are kept (:func:`searched_path`), and one of them whose rule has no key
# now was unticked in ZCode and stays off. A value in the rule is kept.

SEARCH_KEY = "supportsNativeWebSearch"


def searched_path() -> Path:
    """The models OpenProgram turned ZCode's native web search on for (magpie
    keeps them in its stash, ``zcode.web_search``)."""
    from ..keys import gateway_dir
    return gateway_dir() / "zcode-web-search.json"


def read_searched() -> tuple[set[str], bool]:
    """The models in :func:`searched_path`, and whether it could be read: one
    there that can't be is unknown, and nothing is turned on."""
    try:
        data = json.loads(searched_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set(), True
    except (OSError, ValueError):
        return set(), False
    if not isinstance(data, list):
        return set(), False
    return {str(model) for model in data}, True


def put_searched(searched: set[str], ids: list[str]) -> None:
    """Keep ``searched``, only for models still listed (``ids``), written only when it changes."""
    keep = [model for model in ids if model in searched]
    if read_searched() == (set(keep), True) and (keep or not searched_path().exists()):
        return
    edit.write_atomic(searched_path(), json.dumps(keep) + "\n", keep_mode=False)


def searches(model: str) -> bool:
    from ..conversion import ref_searches
    return ref_searches(model)


def web_search(old: Optional[dict[str, Any]], model: str, can: bool, searched: set[str]) -> tuple[bool, bool]:
    """What OpenProgram's rule for ``model``, which says ``old`` now, says of
    native web search: ``(value, set)``. ``can`` is whether ZCode's search
    reaches a provider that searches (Anthropic Messages, the provider
    searches, and the record could be read). ``searched`` is updated."""
    config = (old or {}).get("config") if isinstance((old or {}).get("config"), dict) else {}
    props = config.get("properties") if isinstance(config.get("properties"), dict) else {}
    was = props.get(SEARCH_KEY)
    had = isinstance(was, bool)
    if not can:
        # OpenProgram's on can't be used there; the user's own value stays
        if had and was and model in searched:
            searched.discard(model)
            return False, False
        return bool(was), had
    if had:
        if was:
            searched.add(model)
        return bool(was), True
    if model in searched:
        return False, False  # unticked in ZCode
    searched.add(model)
    return True, True


def rules_sets(at: Wiring, rules: dict[str, Any], fmt: str, base: str) -> dict[str, Any]:
    """OpenProgram's provider rule and its models' rules in provider_config.json,
    every other rule as ZCode wrote it (magpie ``zcodeRules``)."""
    old: Optional[dict[str, Any]] = None
    providers: list[Any] = []
    at_index = -1
    for rule in _list(rules, PROVIDER_RULES):
        if _mine(rule):
            old, at_index = rule, len(providers)
            continue
        providers.append(rule)
    manual = _list(rules, MANUAL_RULES)
    by_hand = {rule.get("modelId") for rule in manual if _mine(rule)}
    models = [rule for rule in _list(rules, MODEL_RULES) if not _mine(rule)]
    ours = {rule.get("modelId"): rule for rule in _list(rules, MODEL_RULES) if _mine(rule)}
    searched, known = read_searched()
    ids = []
    for row in at.models:
        ids.append(row["id"])
        if row["id"] in by_hand:
            searched.discard(row["id"])
            continue  # set by hand in ZCode: its own settings
        props: dict[str, Any] = {"inputFormat": {"supportsImage": bool(row.get("vision"))}}
        if row.get("context_window"):
            props["contextWindow"] = int(row["context_window"])
        can = fmt == "anthropic-messages" and known and searches(row["id"])
        value, has = web_search(ours.get(row["id"]), row["id"], can, searched)
        if has:
            props[SEARCH_KEY] = value
        config: dict[str, Any] = {"properties": props}
        # ZCode's own rules give a model it doesn't know 32000 tokens out and
        # thinking only on or off
        specs: dict[str, Any] = {}
        if _output(row):
            specs["maxOutputTokens"] = {"max": _output(row)}
        if levels_of(row):
            specs["reasoningLevel"] = {"values": levels_of(row)}
        if specs:
            config["optionSpecs"] = specs
        models.append({"providerId": PROVIDER_ID, "modelId": row["id"], "config": config})
    rule: dict[str, Any] = {
        "providerId": PROVIDER_ID, "providerName": "OpenProgram", "enabled": True,
        "config": {"group": "standard-personal", "access": {"type": "api-key", "apiKey": at.key},
                   "api": {"type": fmt, "baseUrl": base}, "personalModelIds": ids, "modelOrder": ids},
    }
    if old is not None:
        # turned off in ZCode, it stays off; what the user set on it in ZCode
        # (its name, logo, a header) stays; the address, key, format and model
        # lists are OpenProgram's
        if isinstance(old.get("enabled"), bool):
            rule["enabled"] = old["enabled"]
        if isinstance(old.get("providerName"), str) and old["providerName"].strip():
            rule["providerName"] = old["providerName"]
        if isinstance(old.get("config"), dict):
            rule["config"] = _merge_kept(old["config"], rule["config"], ("group", "personalModelIds", "modelOrder"))
        rule = _merge_kept(old, rule)
    if at_index >= 0:
        providers.insert(at_index, rule)
    else:
        providers.append(rule)
    if known:
        put_searched(searched, ids)
    sets: dict[str, Any] = {PROVIDER_RULES: providers, MODEL_RULES: models, MANUAL_RULES: manual}
    if rules.get("schemaVersion") is None:
        sets["schemaVersion"] = 1
    return sets


def _wire(at: Wiring) -> list[Write]:
    config_file, rules_file = config_path(), rules_path()
    _, config = _doc(config_file)
    rules_text, rules = _doc(rules_file)
    fmt = _format(config, rules)
    base = at.base(PROTOCOLS[fmt])
    writes = [Write(config_file, "json", listing=True,
                    sets={ENTRY: provider_entry(at, edit.get_path(config, ENTRY)[1], fmt, base)})]
    # a ZCode that keeps its providers as rules, with OpenProgram's taken out
    # there, had it removed in ZCode: a sync doesn't put it back
    removed = bool(at.previous) and rules_text.exists and not any(_mine(r) for r in _list(rules, PROVIDER_RULES))
    if not removed:
        writes.append(Write(rules_file, "json", listing=True, sets=rules_sets(at, rules, fmt, base)))
    return writes


def _unwire(record: dict[str, Any]) -> None:
    """OpenProgram's provider out of both files, however ZCode changed them since."""
    searched_path().unlink(missing_ok=True)
    for path in (config_path(), rules_path()):
        text, doc = _doc(path)
        if not text.exists:
            continue
        sets: dict[str, Any] = {}
        deletes: list[str] = []
        if edit.get_path(doc, ENTRY)[0]:
            deletes.append(ENTRY)
        for key in (PROVIDER_RULES, MODEL_RULES, MANUAL_RULES):
            listed = _list(doc, key)
            if any(_mine(rule) for rule in listed):
                sets[key] = [rule for rule in listed if not _mine(rule)]
        if sets or deletes:
            try:
                text.write(edit.edit("json", text.text, sets=sets or None, deletes=deletes or None, where=str(path)))
            except OSError as exc:
                raise ConfigUnreadable(f"{path} cannot be written: {exc}") from exc


def agent() -> Agent:
    return Agent(
        id="zcode", name="ZCode", icon="zcode", aliases=("z-code",), kind="app", protocol="anthropic",
        dir=home, path=config_path, fields=[Field("model", "Model")], ua=("zcode",),
        wire=_wire, unwire=_unwire, loose=(ENTRY, PROVIDER_RULES, MODEL_RULES, MANUAL_RULES),
        # its config is a file others may read: a key that names it
        named_key=True,
    )
