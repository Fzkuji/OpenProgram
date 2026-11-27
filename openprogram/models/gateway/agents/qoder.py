"""Qoder's CLI and Qoder CN (``$QODER_CONFIG_DIR`` / ``$QODERCN_CONFIG_DIR``,
default ``~/.qoder`` / ``~/.qoder-cn``, ``settings.json``).

After yetone/magpie ``internal/agent/qoder.go`` (MIT License, Copyright (c)
yetone). A provider of one's own is an entry of ``providers``, its models
picked as ``<provider>/<model>``::

    {"providers": {"openprogram": {"displayName", "protocol": "openai",
       "baseUrl": ".../v1", "apiKey", "model", "models": [{"model",
         "displayName", "contextWindow", "maxOutputTokens", "capabilities":
         {"tools": true, "vision", "thinking": {"modes": ["enabled"],
           "supportsEffort": true, "supportedEffortLevels": [...]}}}]}},
     "model": {"name": "openprogram/<model>", "reasoningEffort",
       "preferences": {"openprogram/<model>": {"reasoning": {"effort"}}}}}

Each model's effort is its own, in ``model.preferences`` (1.1.6x); the one in
``model.reasoningEffort`` is asked for only by a model with none there, and
Qoder moves it into the preferences of the model it starts on. The effort in
use goes with a pick to a model with none of its own: its preference is set,
and ``reasoningEffort`` kept for the Qoders before preferences.

An openai provider is asked at ``baseUrl + /chat/completions``. Qoder offers
custom providers only to a signed-in account whose plan has BYOK. Its
requests say only undici (Bun's User-Agent), so the key names the agent.
What the user set on OpenProgram's provider in Qoder (its routing, timeouts,
type, authType, a name of theirs) stays while it is connected; its address,
key, model and model list are OpenProgram's, and the whole entry goes on
disconnect, as magpie deletes its own.

Qoder CN (``qoderclicn``) is the same CLI for the China site, its own
accounts and settings.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import Agent, Wiring, Write

PROVIDER_ID = "openprogram"
SLOT = f"providers.{PROVIDER_ID}"
#: The efforts Qoder can ask for.
LEVELS = ("low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True)
class Build:
    """What tells Qoder's two builds apart."""

    id: str
    name: str
    env: str
    dir: str
    bin: str
    aliases: tuple[str, ...]
    procs: tuple[str, ...]


GLOBAL = Build("qoder", "Qoder", "QODER_CONFIG_DIR", ".qoder", "qodercli", ("qodercli", "qoder-cli"),
               (r"(^|/)qodercli( |$)", r"(^|/)qoder( |$)"))
CHINA = Build("qoder-cn", "Qoder CN", "QODERCN_CONFIG_DIR", ".qoder-cn", "qoderclicn",
              ("qoderclicn", "qodercn", "qodercn-cli"), (r"(^|/)qoderclicn( |$)", r"(^|/)qodercn( |$)"))


def home(build: Build) -> Path:
    folder = os.environ.get(build.env)
    return Path(folder).expanduser() if folder else Path.home() / build.dir


def provider_entry(models: list[dict[str, Any]], model: str, v1: str, key: str) -> dict[str, Any]:
    """OpenProgram's entry in Qoder's providers, every model in it."""
    listed = []
    for row in models:
        caps: dict[str, Any] = {"tools": True, "vision": bool(row.get("vision"))}
        levels = [level for level in dict.fromkeys(row.get("thinking_levels") or []) if level in LEVELS]
        if levels:
            caps["thinking"] = {"modes": ["enabled"], "supportsEffort": True, "supportedEffortLevels": levels,
                                "requiresBudgetForEnabled": False}
        entry: dict[str, Any] = {"model": row["id"], "displayName": row.get("name") or row["id"], "capabilities": caps}
        window, output = int(row.get("context_window") or 0), int(row.get("max_tokens") or 0)
        if window:
            entry["contextWindow"] = window
        if output:
            entry["maxOutputTokens"] = min(output, window) if window else output
        listed.append(entry)
    return {"displayName": "OpenProgram", "protocol": "openai", "baseUrl": v1, "apiKey": key, "model": model,
            "models": listed}


def effort_of(prefs: Any, model: str) -> str:
    """The effort ``model``'s preference asks for, from its reasoning or an
    older generation.reasoning."""
    pref = prefs.get(model) if isinstance(prefs, dict) and model else None
    if not isinstance(pref, dict):
        return ""
    for holder in (pref, pref.get("generation") if isinstance(pref.get("generation"), dict) else {}):
        reasoning = holder.get("reasoning")
        if isinstance(reasoning, dict):
            return str(reasoning.get("effort") or "")
    return ""


def preferences(prefs: Any, model: str, effort: str) -> dict[str, Any]:
    """``prefs`` with ``model``'s effort set (magpie ``qoderPreferences``)."""
    out = json.loads(json.dumps(prefs)) if isinstance(prefs, dict) else {}
    pref = out.get(model) if isinstance(out.get(model), dict) else {}
    generation = pref.get("generation")
    if isinstance(generation, dict):
        # Qoder's own migration lifts generation's fields up; the effort set here is the one to keep
        generation.pop("reasoning", None)
        if not generation:
            pref.pop("generation")
    reasoning = pref.get("reasoning") if isinstance(pref.get("reasoning"), dict) else {}
    reasoning["effort"] = effort
    if reasoning.get("enabled") is False:
        reasoning.pop("enabled")
    pref["reasoning"] = reasoning
    out[model] = pref
    return out


def kept(have: Any, want: dict[str, Any]) -> dict[str, Any]:
    """``want`` with a display name of the user's kept (magpie ``qoderKept``);
    the keys the user added are kept by the engine (``Write.owns``), and
    ``models`` is OpenProgram's."""
    if isinstance(have, dict) and isinstance(have.get("displayName"), str) and have["displayName"].strip():
        return {**want, "displayName": have["displayName"]}
    return want


def _read(path: Path) -> dict[str, Any]:
    from .. import edit
    from ..edit import ConfigText
    text = ConfigText.read(path)
    return edit.load("json", text.text, str(path)) if text.exists else {}


def plugin_only(path: Path) -> bool:
    """The settings file is the one Qoder's JetBrains plugin writes: an object
    holding ``enabledPlugins`` and nothing else (#1495)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and bool(data) and set(data) == {"enabledPlugins"}


def app_status(folder: Path) -> bool:
    """The Qoder app keeps its status in ``folder`` as it signs in, and runs
    the CLI as its agent with that folder's settings (#2138)."""
    return any((folder / name).is_file() for name in
               (".qoder-app-status.json", ".qoder-app-canary-status.json", ".qoder-app-dev-status.json"))


def make(build: Build) -> Agent:
    def config_path() -> Path:
        return home(build) / "settings.json"

    def detect() -> bool:
        # the folder is shared with Qoder's JetBrains plugin and app: neither
        # its indexer nor a settings.json of enabledPlugins alone is a sign of
        # the CLI (magpie dirShared, pathOwn)
        from .base import find_tool
        path = config_path()
        if path.is_file() and not plugin_only(path):
            return True
        return bool(find_tool(build.bin)) or sign()

    def sign() -> bool:
        # the Qoder app runs the CLI as its agent on this folder (magpie sign)
        return app_status(home(build))

    def wire(at: Wiring) -> list[Write]:
        from ..edit import get_path
        path, v1 = config_path(), at.base("openai-chat")
        doc = _read(path)
        picked = f"{PROVIDER_ID}/{at.model}"
        sets: dict[str, Any] = {"model.name": picked}
        prefs = get_path(doc, "model.preferences")[1]
        current = get_path(doc, "model.name")[1]
        was = effort_of(prefs, current) or str(get_path(doc, "model.reasoningEffort")[1] or "")
        if was and not effort_of(prefs, picked):
            # the effort in use goes with it to a model with none of its own
            sets["model.preferences"] = preferences(prefs, picked, was)
            sets["model.reasoningEffort"] = was
        entry = kept(get_path(doc, SLOT)[1], provider_entry(at.models, at.model, v1, at.key))
        # the entry is OpenProgram's whole: what the user adds inside it
        # (routing, timeouts, type, authType) stays while connected, and it
        # goes whole on disconnect; a display name of theirs is no drift
        return [Write(path, "json", sets={SLOT: entry}, owns=(SLOT,), loose=(f"{SLOT}.displayName",),
                      listing=True),
                Write(path, "json", sets=sets)]

    def notice() -> str:
        from .running import any_running
        if any_running(*build.procs):
            return (f"{build.name} reads its settings as a session starts — open sessions keep the model they "
                    "have; new ones use this.")
        return ""

    return Agent(
        id=build.id, name=build.name, icon="qoder", aliases=build.aliases, kind="cli", protocol="openai-chat",
        bin=build.bin, path=config_path, wire=wire, notice=notice, named_key=True, detect=detect, detect_only=True, sign=sign,
    )


def agent() -> Agent:
    return make(GLOBAL)


def extra_agents() -> list[Agent]:
    """Qoder CN, a row of its own."""
    return [make(CHINA)]
