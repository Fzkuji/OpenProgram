"""Kimi Code, Moonshot's kimi CLI (``config.toml`` in ``~/.kimi-code`` or the old ``~/.kimi``).

After yetone/magpie ``internal/agent/kimi.go`` and ``kimi_secondary.go`` (MIT
License, Copyright (c) yetone). The new Kimi Code (TypeScript, 2.x) keeps its
settings in ``~/.kimi-code`` (``$KIMI_CODE_HOME``), the old Python kimi-cli in
``~/.kimi`` (``$KIMI_SHARE_DIR``), in the same shape: the model sessions start
with as ``default_model``, a key of its ``[models."<key>"]`` tables, each
naming a ``[providers.<name>]`` table and the model to ask it for.

Connecting adds the provider ``openprogram`` (the gateway, spoken to as
Kimi's own chat completions, ``type = "kimi"``, so the thinking goes both ways
as ``reasoning_content``) and one model table per catalog model, keyed
``openprogram/<provider>/<model>``, so the catalog joins Kimi's ``/model``
picker, and points ``default_model`` at the pick. Kimi's ``/model`` rewrites
it with the thinking level as one pick: they go back on disconnect while it
still names one of OpenProgram's, and a sync of the model list never writes
them, nor the subagents' picks. Kimi refuses a config whose
``default_model`` or a model's provider is missing; magpie orders its writes
for that, here the whole file is written at once. The provider's keys are
written in place, so the rest of its table (``custom_headers``, ``env``) stays.

The subagent model is ``[secondary_model] default_model`` (Kimi Code 2.x
only): put in the pool too where there is one, as Kimi's own
``/secondary-model`` does, and a model leaving the catalog is taken out of the
pool first, since Kimi refuses a pool naming a model it doesn't have. The
pool (``[secondary_model.models]``, a value of one model per line) and the
subagents' effort (``default_effort``, only a level every pool model takes,
else Kimi refuses to start) are fields too (``kimi_secondary.go``). A pool
that was there before OpenProgram stays on disconnect, empty or not.

The thinking level sessions start at is ``[thinking]`` in Kimi Code 2.x
(``enabled = false`` for off, else ``effort``) and kimi-cli's top-level
``default_thinking`` (magpie ``kimiThinkingField``). Its telemetry switch is
the top-level ``telemetry = false`` (magpie ``kimiTelemetry``), a kept
choice (``telemetry.py``).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import telemetry
from .base import Agent, Field, Pick, Wiring, Write
from .model_rows import (PROVIDER_ID, absent_before, choice, dotted, efforts, images, max_tokens, name, picked,
                         prefixed, read_doc, window, write_now, wrote_before)

AGENT_ID = "kimi"
PROVIDER_TABLE = f"providers.{PROVIDER_ID}"
SECONDARY = "secondary_model"
#: What Kimi is told of a model whose context isn't known: it has to be told
#: one, and compacts as it nears it (magpie ``kimiContext``).
KIMI_CONTEXT = 128000


def kimi_dir() -> tuple[Path, bool]:
    """The folder Kimi keeps its config.toml in, and whether it is the old
    kimi-cli's (magpie ``KimiDir``). kimi-cli (1.50 on) hands over to the new
    Kimi Code, which reads only ``~/.kimi-code``: it offers to copy ``~/.kimi``'s
    config over when it first starts and never reads ``~/.kimi`` again, so a
    model written there is one it never sees. ``~/.kimi`` is kimi-cli's only
    where the new one isn't."""
    if os.environ.get("KIMI_CODE_HOME"):
        return Path(os.environ["KIMI_CODE_HOME"]).expanduser(), False
    code = Path.home() / ".kimi-code"
    if code.is_dir():
        return code, False
    if os.environ.get("KIMI_SHARE_DIR"):
        return Path(os.environ["KIMI_SHARE_DIR"]).expanduser(), True
    old = Path.home() / ".kimi"
    if old.is_dir():
        return old, True
    return code, False


def home() -> Path:
    return kimi_dir()[0]


def config_path() -> Path:
    return home() / "config.toml"


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _own(was: dict[str, Any], last: dict[str, Any], key: str, mine: Any) -> tuple[Any, bool]:
    """A value in one of OpenProgram's model tables (magpie ``kimiOwn``): one the
    user set there themselves (other than what OpenProgram wrote last) stays,
    else OpenProgram's own; and whether it is OpenProgram's."""
    value = was.get(key)
    if value not in (None, "") and (key not in last or value != last[key]):
        return value, False
    return mine, mine not in (None, "")


def _efforts(levels_in: list[str], vendor: str, was: Any, last: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """``support_efforts`` and ``default_effort`` of a thinking model for the new
    Kimi Code's thinking picker (magpie ``kimiEfforts``): without them it offers
    thinking on or off only. The default is the model's own, else high where it
    has it; one set by hand stays while the model has it. none is left out:
    Kimi Code's own off turns thinking off."""
    levels = [level for level in levels_in if level != "none"]
    if not levels:
        return {}, False
    out: dict[str, Any] = {"support_efforts": levels}
    mine = vendor if vendor in levels else ("high" if "high" in levels else "")
    # high in a table with no record is what was written before one was kept
    if "default_effort" not in last and was == "high":
        was = ""
    value, ours = _own({"default_effort": was}, last, "default_effort", mine)
    if not ours and value not in levels:
        value, ours = mine, bool(mine)
    if value:
        out["default_effort"] = value
    return out, ours


def model_tables(models: list[dict[str, Any]], legacy: bool, doc: dict[str, Any],
                 last: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """The leaves of OpenProgram's model tables, and those of its keys the
    user's own values now hold (left out of the write) (magpie
    ``kimiModelTables``). The new Kimi Code is told of tool calling too, which
    kimi-cli has no word for and refuses."""
    sets: dict[str, Any] = {}
    deletes: list[str] = []
    tables = doc.get("models") if isinstance(doc.get("models"), dict) else {}
    for model in models:
        key = prefixed(model["id"])
        was = tables.get(key) if isinstance(tables.get(key), dict) else {}
        mine_last = {k[len(dotted("models", key)) + 1:]: v for k, v in last.items()
                     if k.startswith(dotted("models", key) + ".")}
        leaves: dict[str, Any] = {"provider": PROVIDER_ID, "model": model["id"],
                                  "max_context_size": window(model, KIMI_CONTEXT)}
        # the name Kimi's /model picker and status line show (kimi-cli 1.37 on)
        users: set[str] = set()
        value, ours = _own(was, mine_last, "display_name", name(model) if name(model) != model["id"] else "")
        if value and ours:
            leaves["display_name"] = value
        elif value:
            users.add("display_name")
        levels = efforts(model)
        caps = []
        if levels:
            caps.append("thinking")
        if images(model):
            caps.append("image_in")
        if not legacy:
            caps.append("tool_use")
        if caps:
            leaves["capabilities"] = caps
        if not legacy:
            # the new Kimi Code's cap on a reply (max_completion_tokens); kimi-cli has no such key
            was_output = was.get("max_output_size")
            own_was = {"max_output_size": was_output} if _is_int(was_output) else {}
            value, ours = _own(own_was, mine_last, "max_output_size", max_tokens(model) or None)
            if value and ours:
                leaves["max_output_size"] = value
            elif value:
                users.add("max_output_size")
            thinking, ours = _efforts(levels, str(model.get("default_thinking_level") or ""),
                                      was.get("default_effort"), mine_last)
            if "default_effort" in thinking and not ours:
                # the user's stays as it is, never taken for OpenProgram's
                thinking.pop("default_effort")
                users.add("default_effort")
            leaves.update(thinking)
        for leaf, value in leaves.items():
            sets[dotted("models", key, leaf)] = value
        for leaf in ("display_name", "max_output_size", "default_effort", "support_efforts", "capabilities"):
            if leaf not in leaves and leaf not in users and leaf in mine_last and leaf in was:
                deletes.append(dotted("models", key, leaf))
    return sets, deletes


def _wire(at: Wiring) -> list[Write]:
    folder, legacy = kimi_dir()
    path = folder / "config.toml"
    doc = read_doc("toml", path)
    last = wrote_before(AGENT_ID, path)
    sets: dict[str, Any] = {
        f"{PROVIDER_TABLE}.type": "kimi",
        f"{PROVIDER_TABLE}.base_url": at.base("openai-chat"),
        f"{PROVIDER_TABLE}.api_key": at.key,
    }
    tables, deletes = model_tables(at.models, legacy, doc, last)
    sets.update(tables)
    listed = {prefixed(model["id"]) for model in at.models}
    # a model gone from the catalog out, and out of the subagents' pool first:
    # Kimi refuses a pool naming a model it doesn't have
    gone = [dotted("models", key) for key in (doc.get("models") or {})
            if key.startswith(PROVIDER_ID + "/") and key not in listed]
    secondary = doc.get(SECONDARY) if isinstance(doc.get(SECONDARY), dict) else {}
    pool = secondary.get("models") if isinstance(secondary.get("models"), dict) else None
    if not legacy and pool is not None:
        gone += [dotted(SECONDARY, "models", key) for key in pool
                 if key.startswith(PROVIDER_ID + "/") and key not in listed]
    deletes += absent_before(AGENT_ID, path, gone)
    writes = [Write(path, "toml", sets=sets, deletes=deletes, listing=True)]
    if at.syncing:
        return writes  # the picks are the user's to change in Kimi
    ours = {"prefix": PROVIDER_ID + "/"}
    picks: dict[str, Any] = {"default_model": prefixed(at.model)}
    drop: list[str] = []
    _thinking(at.values.get("effort", ""), legacy, last, picks, drop)
    pairs = [Pick(tuple(picks), "default_model", ours)]
    if not legacy:
        subagents: dict[str, Any] = {}
        _secondary(at, pool, secondary, last, subagents, drop)
        held = tuple(key for key in (f"{SECONDARY}.default_model", f"{SECONDARY}.default_effort") if key in subagents)
        if held:
            pairs.append(Pick(held, f"{SECONDARY}.default_model", ours))
        picks.update(subagents)
    writes.append(Write(path, "toml", sets=picks, deletes=drop, picks=tuple(pairs)))
    return writes


def _ours_only(last: dict[str, Any], keys: list[str], drop: list[str]) -> None:
    """A field back on Kimi's own default takes out only what OpenProgram wrote for it."""
    drop.extend(key for key in keys if key in last)


def _thinking(value: str, legacy: bool, last: dict[str, Any], picks: dict[str, Any], drop: list[str]) -> None:
    """The thinking level sessions start at (magpie ``kimiThinkingField``)."""
    if legacy:
        if value:
            picks["default_thinking"] = value != "off"
        else:
            _ours_only(last, ["default_thinking"], drop)
        return
    if value == "off":
        # as Kimi Code's own off: the level stays for the next on
        picks["thinking.enabled"] = False
    elif value:
        picks["thinking.enabled"] = True
        picks["thinking.effort"] = value
    else:
        _ours_only(last, ["thinking.enabled", "thinking.effort"], drop)


#: The levels offered for a model whose own aren't known (magpie ``kimiCommonLevels``).
COMMON_LEVELS = ("off", "low", "medium", "high")


def thinking_choices(values: dict[str, str]) -> list[str]:
    """The levels Kimi offers for the picked model (magpie
    ``kimiThinkingOptions``): off, then its efforts; none for a model that
    doesn't think. kimi-cli has thinking on or off only."""
    if kimi_dir()[1]:
        return ["off", "on"]
    model = picked(values)
    if not model:
        return list(COMMON_LEVELS)
    levels = [level for level in efforts(model) if level not in ("off", "none")]
    if not levels and not model.get("reasoning"):
        return []
    return ["off", *levels]


def pool_names(value: str) -> list[str]:
    """A pool field's value, one model per line, as config keys."""
    names: list[str] = []
    for line in (value or "").splitlines():
        line = line.strip()
        if line and line not in names:
            names.append(line)
    return names


def pool_levels(pool: list[str], models: list[dict[str, Any]]) -> list[str]:
    """The subagent efforts every model of ``pool`` takes, as Kimi checks
    ``default_effort`` (magpie ``kimiPoolLevels``): off, then each level
    every thinking model lists; off alone when one doesn't think. A model the
    catalog doesn't describe (the user's own) is taken at the common levels.
    None for no pool: Kimi ignores the effort then."""
    if not pool:
        return []
    by_key = {prefixed(model["id"]): model for model in models}
    levels: list[str] | None = None
    for alias in pool:
        model = by_key.get(alias)
        own = list(COMMON_LEVELS[1:]) if model is None else efforts(model)
        if not own:
            if model is not None and not model.get("reasoning"):
                levels = []
            continue
        levels = own if levels is None else [level for level in levels if level in own]
    return ["off", *(levels if levels is not None else COMMON_LEVELS[1:])]


def _secondary(at: Wiring, pool: dict[str, Any] | None, secondary: dict[str, Any], last: dict[str, Any],
               picks: dict[str, Any], drop: list[str]) -> None:
    """``[secondary_model]``: the subagents' model, their pool and effort
    (magpie ``kimiSubagentFields``). The default stays one of the pool, as
    Kimi's own ``/secondary-model`` keeps it; an effort the pool can't take
    is left out, since Kimi would refuse to start a session."""
    subagent = at.values.get("subagent_model", "")
    names = pool_names(at.values.get("subagent_pool", ""))
    default = prefixed(subagent) if subagent else ""
    if names:
        for alias in names:
            # a description written stays
            picks[dotted(SECONDARY, "models", alias)] = (pool or {}).get(alias, "")
        if not default:
            current = str(secondary.get("default_model") or "")
            default = current if current in names else names[0]
        elif default not in names:
            picks[dotted(SECONDARY, "models", default)] = ""
    elif default and pool is not None and default not in pool:
        picks[dotted(SECONDARY, "models", default)] = ""
    # the pool is the one picked (magpie's pool Set): an entry of
    # OpenProgram's left out of it goes; the user's own entries stay
    for alias in pool or {}:
        key = dotted(SECONDARY, "models", alias)
        if (str(alias).startswith(PROVIDER_ID + "/") and alias not in names and alias != default
                and key not in picks and (names or key in last)):
            drop.append(key)
    if default:
        picks[f"{SECONDARY}.default_model"] = default
    effort = (at.values.get("subagent_effort") or "").strip().lower()
    members = names or ([default] if default else [])
    if effort and effort in pool_levels(members, at.models):
        picks[f"{SECONDARY}.default_effort"] = effort
    else:
        _ours_only(last, [f"{SECONDARY}.default_effort"], drop)


def _pool_options() -> list[tuple[str, str]]:
    """The pool's choices: every model of the catalog, as Kimi names it."""
    from .. import conversion
    return [(prefixed(model["id"]), name(model) or model["id"]) for model in conversion.enabled_model_refs()]


def _subagent_effort_choices(values: dict[str, str]) -> list[str]:
    from .. import conversion
    names = pool_names(values.get("subagent_pool", ""))
    if not names and values.get("subagent_model"):
        names = [prefixed(values["subagent_model"])]
    return pool_levels(names, conversion.enabled_model_refs())


def _unwire(record: dict[str, Any]) -> None:
    """Before the restore, OpenProgram's own goes from the config (magpie
    ``dropMagpie``, ``kimiPruneSecondary``): its model tables and provider
    table, keys the user added to them or not, and its names in the
    subagents' pool, so no model is left naming a provider Kimi doesn't
    have. A subagent default of OpenProgram's goes back to the user's own,
    as the stash has it, else to the pool's first, else out with its effort."""
    path = config_path()
    doc = read_doc("toml", path)
    if not doc:
        return
    files = record.get("files") or {}
    kept = files.get(str(path)) or files.get(os.path.realpath(path)) or {}
    deletes = [dotted("models", key) for key in (doc.get("models") or {}) if str(key).startswith(PROVIDER_ID + "/")]
    if PROVIDER_ID in (doc.get("providers") or {}):
        deletes.append(PROVIDER_TABLE)
    sets: dict[str, Any] = {}
    secondary = doc.get(SECONDARY) if isinstance(doc.get(SECONDARY), dict) else {}
    pool = secondary.get("models") if isinstance(secondary.get("models"), dict) else None
    left: list[str] = []
    if pool is not None:
        gone = [key for key in pool if str(key).startswith(PROVIDER_ID + "/")]
        left = [key for key in pool if key not in gone]
        if left or f"{SECONDARY}.models" not in (kept.get("parents") or []):
            # a pool that was there before stays, empty or not
            deletes += [dotted(SECONDARY, "models", key) for key in gone]
        else:
            deletes.append(f"{SECONDARY}.models")
    if str(secondary.get("default_model") or "").startswith(PROVIDER_ID + "/"):
        original = (kept.get("originals") or {}).get(f"{SECONDARY}.default_model") or {}
        was = original.get("value") if original.get("had") else ""
        if isinstance(was, str) and was and not was.startswith(PROVIDER_ID + "/"):
            sets[f"{SECONDARY}.default_model"] = was
            if left and was not in left:
                sets[dotted(SECONDARY, "models", was)] = ""
        elif left:
            sets[f"{SECONDARY}.default_model"] = left[0]
        else:
            deletes += [f"{SECONDARY}.default_model", f"{SECONDARY}.default_effort"]
    write_now([Write(path, "toml", sets=sets, deletes=deletes)])


def _notice() -> str:
    """kimi-cli reads config.toml at start-up. Kimi Code 2.x watches it and
    reloads it, but the model picker of its web and desktop app is read again
    only when Settings › Providers is opened again."""
    from .running import any_running
    if not any_running(r"(^|/)kimi( |$)", r"(^|/)kimi-cli( |$)"):
        return ""
    if kimi_dir()[1]:
        return "Kimi Code reads its settings at start-up — restart open kimi sessions to use this."
    return ("Kimi Code reads this as it runs; in its web or desktop app, reopen Settings › Providers "
            "or reload the page to see new models.")


def agent() -> Agent:
    return Agent(
        id=AGENT_ID, name="Kimi Code", icon="kimi-coding", aliases=("kimi-code", "kimi-cli"), kind="cli",
        protocol="openai-chat", bin="kimi", dir=home, path=config_path,
        fields=[Field("model", "Model"),
                Field("effort", "Thinking", quiet=True, options=choice(AGENT_ID, thinking_choices)),
                Field("subagent_model", "Subagent model", quiet=True, follows="model"),
                # magpie's value: one model per line, picked several at once
                Field("subagent_pool", "Subagent pool", quiet=True, options=_pool_options, multi=True),
                Field("subagent_effort", "Subagent effort", quiet=True,
                      options=choice(AGENT_ID, _subagent_effort_choices)),
                telemetry.field()],
        # kimi-cli (Python) says KimiCLI/…; Kimi Code 2.x, kimi-code-cli/…
        ua=("kimicli", "kimi-code-cli"), wire=_wire, unwire=_unwire, notice=_notice,
        # magpie kimiTelemetry: app.py's telemetry_disabled = not config.telemetry
        **telemetry.switch(AGENT_ID, lambda: [Write(config_path(), "toml", sets={"telemetry": False})]),
    )
