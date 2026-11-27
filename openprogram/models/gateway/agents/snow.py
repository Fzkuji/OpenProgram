"""Snow CLI (``snow``, npm snow-ai, MayDay-wpf/snow-cli; ``$SNOW_CONFIG_DIR``, default ``~/.snow``).

After yetone/magpie ``internal/agent/snow.go`` (MIT License, Copyright (c)
yetone). Each API setup of Snow's is a profile, ``profiles/<name>.json``, the
whole of its config (``{"snowcfg": {baseUrl, baseUrlMode, apiKey,
requestMethod, advancedModel, basicModel, maxContextTokens, maxTokens, …},
…}``); ``active-profile.json`` (``{"activeProfile": "<name>"}``) names the one
in use, "default" when it is missing, and ``config.json`` is a copy of that
one, which Snow writes again from the profile at every start
(utils/config/configManager.ts initializeProfiles, switchProfile).

Connecting adds a profile of OpenProgram's own, ``profiles/openprogram.json``:
a copy of the profile the user was on — their other settings kept, a
subscription sign-in of theirs (``snowcfg.oauth``) left out — with the
gateway's ``/v1`` as its base URL, a key naming Snow CLI, Chat Completions as
its protocol and the model picked as its main (``advancedModel``) and light
(``basicModel``) model, and the model's window, output and image support
from the catalog. It makes that profile the active one and copies it to
``config.json``, as Snow's own profile switch does. Disconnecting makes the
profile the user was on active again (or takes ``active-profile.json`` away
where there was none), puts back their ``config.json`` and a profile of
theirs that was named ``openprogram``, and removes OpenProgram's.

A vision model of the user's profile comes along only with an endpoint and a
key of its own: one on the profile's would go to the gateway, which doesn't
know it, or carry OpenProgram's key to the user's endpoint. Snow's requests
carry no User-Agent of its own (an ``x-snow: <version>`` header), so the key
is what tells them apart.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from .base import ROOT, Agent, Field, Wiring, Write

PROFILE = "openprogram"
#: What Snow is told of a model whose limits aren't known: its own defaults.
DEFAULT_CONTEXT = 200000
DEFAULT_OUTPUT = 64000
#: The settings of Snow's own vision model, which describes images to a main
#: model that takes none (supportsVision false).
VISION = ("visionModel", "visionBaseUrl", "visionBaseUrlMode", "visionApiKey", "visionRequestMethod")


def home() -> Path:
    folder = (os.environ.get("SNOW_CONFIG_DIR") or "").strip()
    if folder and os.path.isabs(folder):
        return Path(os.path.normpath(folder))
    return Path.home() / ".snow"


def profile_path(name: str = PROFILE) -> Path:
    return home() / "profiles" / f"{name}.json"


def _read(path: Path) -> Optional[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    from ..edit.jsonc import to_json
    try:
        data = json.loads(to_json(text) or "")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def active() -> str:
    """The profile Snow starts on, as it reads it."""
    data = _read(home() / "active-profile.json")
    if data is not None and isinstance(data.get("activeProfile"), str):
        return data["activeProfile"]
    try:
        legacy = (home() / "active-profile.txt").read_text(encoding="utf-8").strip()
    except OSError:
        legacy = ""
    return legacy or "default"


def own_vision(cfg: dict[str, Any]) -> None:
    """Keep a vision model of the user's profile with an endpoint and key of
    its own, on the protocol and URL mode it was asked with there; any other goes."""
    if not all(isinstance(cfg.get(k), str) and cfg[k].strip() for k in ("visionModel", "visionBaseUrl", "visionApiKey")):
        for key in VISION:
            cfg.pop(key, None)
        return
    for own, profile in (("visionRequestMethod", "requestMethod"), ("visionBaseUrlMode", "baseUrlMode")):
        if not cfg.get(own) and cfg.get(profile):
            cfg[own] = cfg[profile]


def profile_for(base: Optional[dict[str, Any]], ours: bool, at: Wiring) -> dict[str, Any]:
    """The profile for the pick: from ``base`` (a profile of the user's, or
    OpenProgram's own already when ``ours``) with the gateway set in and the
    user's sign-in left out (magpie ``snowProfileFor``)."""
    data = json.loads(json.dumps(base or {}))
    cfg = data.get("snowcfg") if isinstance(data.get("snowcfg"), dict) else {}
    cfg.pop("oauth", None)
    if not ours:
        own_vision(cfg)
    ref = at.model
    context, output, sees = DEFAULT_CONTEXT, DEFAULT_OUTPUT, False
    for model in at.models:
        if model["id"] == ref:
            context = int(model.get("context_window") or 0) or context
            output = int(model.get("max_tokens") or 0) or output
            sees = bool(model.get("vision"))
            break
    # an output above the window Snow is told is cut to it
    cfg.update({"maxContextTokens": context, "maxTokens": min(output, context), "supportsVision": sees,
                "baseUrl": at.base("openai-chat"), "baseUrlMode": "base", "apiKey": at.key,
                "requestMethod": "chat", "advancedModel": ref,
                # the light model follows the main one unless picked apart from it
                "basicModel": at.values.get("small") or ref})
    data["snowcfg"] = cfg
    return data


def _text(data: dict[str, Any]) -> str:
    # as Go's json.MarshalIndent writes a map: keys sorted
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False)


def _wire(at: Wiring) -> list[Write]:
    folder = home()
    ours_path, config = profile_path(), folder / "config.json"
    existing = _read(ours_path)
    mine = existing is not None and (existing.get("snowcfg") or {}).get("apiKey") == at.key
    writes: list[Write] = []
    name = active()
    if mine:
        base = existing
    else:
        base = _read(profile_path(name)) if name != PROFILE else None
        base = base if base is not None else _read(config)
        # a Snow from before profiles copies config.json into a new default
        # profile at its next start and makes that the active one: done
        # here, so it is the user's config that is copied, not ours
        default = profile_path("default")
        if not default.exists() and config.is_file():
            writes.append(Write(default, "file", content=config.read_text(encoding="utf-8")))
    text = _text(profile_for(base, mine, at))
    # the profile is OpenProgram's whole: it goes on disconnect even when
    # edited since (magpie removes magpie.json); what the user changed in it
    # is kept across syncs, as it is the base the next one starts from
    writes.append(Write(ours_path, "file", content=text, listing=True, owns=(ROOT,)))
    writes.append(Write(folder / "active-profile.json", "json", sets={"activeProfile": PROFILE}))
    # config.json is the active profile's copy, as Snow's profile switch
    # makes it; a sync writes it only while Snow is still on OpenProgram's
    writes.append(Write(config, "file", content=text, listing=not mine or name == PROFILE))
    return writes


def _notice() -> str:
    from .running import any_running
    if any_running(r"(^|/)snow( |$)"):
        return "Snow CLI reads its profile at start-up — restart open snow sessions to use this."
    return ""


def agent() -> Agent:
    return Agent(
        id="snow", name="Snow CLI", icon="snow", aliases=("snow-cli", "snow-ai", "snow-app"), kind="cli",
        protocol="openai-chat", bin="snow", dir=home, path=profile_path,
        # basicModel, the light model Snow's summaries, compaction and file
        # search ask (utils/config/apiConfig.ts); it follows the main model
        fields=[Field("model", "Model"), Field("small", "Small", quiet=True, follows="model")],
        named_key=True, wire=_wire, notice=_notice,
    )
