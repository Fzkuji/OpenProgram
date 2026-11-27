"""Devin (``~/.config/devin/config.json``, ``%APPDATA%\\devin`` on Windows): its own models only.

After yetone/magpie ``internal/agent/devin.go`` and the model-list half of
``internal/provider/devin.go`` (MIT License, Copyright (c) yetone). Devin has
no endpoint the gateway can stand in for — its models are its own — so its
row sets only ``agent.model``, and the options are the families
``devin models list --format json`` reports, offered as one model each (and
a family's fast run one more, ``claude-opus-5-5-fast``), not the 600-odd
variant ids that are mostly one family at an effort each.

Devin's config has no effort of its own: a variant id is the effort it runs
at. So the row has an effort beside the model (not a key of Devin's), the
levels the model picked has, and the pair is written as the one id: the
family's variant at the effort picked (``claude-opus-5-5-high``,
``claude-opus-5-5-high-fast``), the nearest level it has. With no effort
picked, a family keeps the effort the old id was at, as near as the family
has it (``swe-2-max`` → ``swe-2`` at max → ``swe-2-max``); with none, its
own id, which follows the family's newest; an id that is no family's (one
typed in) goes as it is. Going back to Devin's own default puts back
what was there. ``devin models list`` spawns a process that takes seconds,
so the list is kept a few minutes and read again behind the row, never
waited on by it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .base import Agent, Field, Wiring, Write, picked

#: Effort levels, weakest first (magpie ``cursorLevelRank``).
LEVEL_RANK = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
#: The effort words Devin's variant ids end in, and the level each is.
_LEVELS = (("none", "none"), ("xhigh", "xhigh"), ("minimal", "minimal"), ("min", "minimal"),
           ("low", "low"), ("medium", "medium"), ("high", "high"), ("max", "max"))
#: Words a variant id may end in after its effort, for a quicker run of the same model.
_TIERS = (("fast", "Fast"), ("priority", "Priority"))
FRESH_SECONDS = 300.0
RETRY_SECONDS = 60.0


def home() -> Path:
    if sys.platform == "win32" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "devin"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "devin"


def config_path() -> Path:
    return home() / "config.json"


# Devin's model list (magpie provider/devin.go)

@dataclass
class Variant:
    id: str
    name: str


@dataclass
class Family:
    """One entry of ``devin models list``: a name that follows the family's
    newest model, and the pinned variants under it."""

    uid: str
    label: str
    aliases: list[str] = field(default_factory=list)
    models: list[Variant] = field(default_factory=list)

    def named(self, model: str) -> bool:
        return self.uid == model or model in self.aliases

    def by_level(self, tier: str) -> dict[str, str]:
        """The family's variant at each effort its ids say, in ``tier`` ("" for none), the first at each."""
        out: dict[str, str] = {}
        for variant in self.models:
            t, level = tier_of(variant.id)
            if t == tier and level and variant.id != self.uid and level not in out:
                out[level] = variant.id
        return out

    def efforts(self, tier: str = "") -> list[str]:
        by = self.by_level(tier)
        return [level for level in LEVEL_RANK if level in by]

    def tiers(self) -> list[str]:
        return [word for word, _ in _TIERS if self.by_level(word)]

    def default_level(self) -> str:
        if self.models:
            tier, level = tier_of(self.models[0].id)
            if not tier and level:
                return level
        return "medium"


def parse_models(raw: bytes | str) -> list[Family]:
    """``devin models list --format json``: ``{"families": [{"family_uid",
    "family_label", "aliases", "variants": [{"model_uid", "label"}]}]}``."""
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    out = []
    for entry in (data.get("families") if isinstance(data, dict) else None) or []:
        if not isinstance(entry, dict) or not entry.get("family_uid"):
            continue
        family = Family(uid=str(entry["family_uid"]), label=str(entry.get("family_label") or entry["family_uid"]),
                        aliases=[str(a) for a in entry.get("aliases") or []])
        for variant in entry.get("variants") or []:
            if isinstance(variant, dict) and variant.get("model_uid"):
                uid = str(variant["model_uid"])
                family.models.append(Variant(uid, str(variant.get("label") or uid)))
        out.append(family)
    return out


def _level(model: str) -> str:
    """The effort a Devin id says it runs at, "" when it says none."""
    low = model.lower()
    for word, level in _LEVELS:
        if low.endswith("-" + word) or low.endswith("_" + word):
            return level
    return ""


def tier_of(model: str) -> tuple[str, str]:
    """The tier a Devin id is in and the effort it runs at:
    ``claude-opus-5-5-high-fast`` is ("fast", "high"), ``swe-2-high``
    ("", "high"). A tier word with no effort before it (``swe-1-6-fast``)
    is no tier: that id is a model of its own, at none."""
    low = model.lower()
    for word, _ in _TIERS:
        for sep in ("-", "_"):
            if low.endswith(sep + word):
                level = _level(low[:-len(word) - 1])
                if level:
                    return word, level
    return "", _level(model)


def nearest(want: str, levels: list[str]) -> str:
    """The one of ``levels`` nearest ``want``, a tie going up."""
    if want not in LEVEL_RANK or not levels or want in levels:
        return want
    at = LEVEL_RANK.index(want)
    best, dist = want, len(LEVEL_RANK)
    for level in levels:
        i = LEVEL_RANK.index(level)
        d = abs(i - at)
        if d < dist or (d == dist and i > at):
            best, dist = level, d
    return best


def _router(model: str) -> bool:
    # Adaptive and Fusion route between models inside Devin's own agent
    return model.strip().lower() in ("adaptive", "fusion")


def offered(families: list[Family]) -> list[dict[str, str]]:
    """Devin's list as the picker offers it (magpie ``DevinOffered``): each
    family one model, its fast run another, and the variants at no effort;
    Adaptive and Fusion as the one model each."""
    out: list[dict[str, str]] = []
    for family in families:
        if _router(family.uid):
            out.append({"value": family.uid, "label": family.label})
            continue
        out.append({"value": family.uid, "label": family.label})
        for tier in family.tiers():
            run = f"{family.uid}-{tier}"
            if not any(other.named(run) for other in families):
                out.append({"value": run, "label": f"{family.label} {dict(_TIERS)[tier]}"})
        for variant in family.models:
            if variant.id == family.uid or tier_of(variant.id)[1] or len(family.models) == 1:
                continue
            out.append({"value": variant.id, "label": variant.name})
    return out


def _variant_in(families: list[Family], model: str, effort: str) -> str:
    for family in families:
        if not family.named(model) or not family.models:
            continue
        if effort:
            found = family.by_level("").get(nearest(effort, family.efforts("")))
            if found:
                return found
        return family.models[0].id
    for family in families:
        for tier in family.tiers():
            suffix = "-" + tier
            if not model.endswith(suffix) or not family.named(model[:-len(suffix)]):
                continue
            effort = effort or family.default_level()
            by, levels = family.by_level(tier), family.efforts(tier)
            return by.get(nearest(effort, levels)) or by[levels[0]]
    return model


def split(families: list[Family], model: str) -> tuple[str, str]:
    """An id Devin's config holds as the picker shows it: the model (a family,
    or a family's fast run) and the effort the id is at."""
    for family in families:
        for variant in family.models:
            if variant.id != model or variant.id == family.uid:
                continue
            tier, level = tier_of(model)
            if not level:
                return model, ""
            return (f"{family.uid}-{tier}" if tier else family.uid), level
    return model, ""


def efforts_of(families: list[Family], model: str) -> list[str]:
    """The levels the model the picker shows has (magpie ``devinEfforts``):
    a family's, or its fast run's; none for one at no effort or one Devin's
    list doesn't have."""
    for family in families:
        if family.named(model):
            return family.efforts("")
    for family in families:
        for tier in family.tiers():
            suffix = "-" + tier
            if model.endswith(suffix) and family.named(model[:-len(suffix)]):
                return family.efforts(tier)
    return []


def pick(families: list[Family], model: str, effort: str) -> str:
    """The id to give Devin's config for a model and effort (``split`` the other way round)."""
    if not effort and any(family.named(model) for family in families):
        return model
    return _variant_in(families, model, effort)


class _Cache:
    lock = threading.Lock()
    at = 0.0
    failed = 0.0
    families: list[Family] = []
    asking = False


def _executable() -> str:
    from .base import find_tool
    return find_tool("devin")


def _ask() -> list[Family]:
    path = _executable()
    if not path:
        return []
    try:
        out = subprocess.run([path, "models", "list", "--format", "json"], capture_output=True,
                             timeout=30, stdin=subprocess.DEVNULL, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_models(out)


def _refresh() -> None:
    families = _ask()
    with _Cache.lock:
        if families:
            _Cache.families, _Cache.at, _Cache.failed = families, time.monotonic(), 0.0
        else:
            _Cache.failed = time.monotonic()
        _Cache.asking = False


def families(wait: bool = False) -> list[Family]:
    """Devin's model list as last read, read again behind the caller once it
    is old (magpie ``DevinFamilies``); only ``wait`` with nothing known yet
    waits for the read (a pick being written)."""
    with _Cache.lock:
        now = time.monotonic()
        fresh = bool(_Cache.families) and now - _Cache.at < FRESH_SECONDS
        start = not fresh and not _Cache.asking and (not _Cache.failed or now - _Cache.failed >= RETRY_SECONDS)
        if start:
            _Cache.asking = True
        known = list(_Cache.families)
    if start and wait and not known:
        _refresh()
        with _Cache.lock:
            return list(_Cache.families)
    if start:
        threading.Thread(target=_refresh, daemon=True, name="devin-models").start()
    return known


def _current() -> str:
    from ..edit import ConfigText, ConfigUnreadable, get_path, load
    try:
        text = ConfigText.read(config_path())
        if not text.exists:
            return ""
        present, value = get_path(load("jsonc", text.text, str(config_path())), "agent.model")
    except (ConfigUnreadable, OSError):
        return ""
    return value if present and isinstance(value, str) else ""


def _wire(at: Wiring) -> list[Write]:
    listed = families(wait=True)
    model = at.model
    if listed:
        effort = at.values.get("effort") or split(listed, _current())[1]
        model = pick(listed, model, effort)
    return [Write(config_path(), "jsonc", sets={"agent.model": model})]


def _options() -> list[tuple[str, str]]:
    current = picked("devin").get("model", "")
    out = [(option["value"], option["label"]) for option in offered(families())]
    if current and not any(value == current for value, _ in out):
        out.insert(0, (current, current))
    return out


def _effort_options() -> list[tuple[str, str]]:
    return [(level, level) for level in efforts_of(families(), picked("devin").get("model", ""))]


def _notice() -> str:
    from .running import any_running
    if any_running(r"(^|/)devin( |$)"):
        return "Devin reads its model at start-up — restart open Devin sessions to use this."
    return ""


def agent() -> Agent:
    return Agent(
        id="devin", name="Devin", icon="devin", kind="cli", bin="devin", dir=home, path=config_path,
        fields=[Field("model", "Model", options=_options), Field("effort", "Effort", quiet=True, options=_effort_options)],
        wire=_wire, notice=_notice,
    )
