"""OpenChamber, a desktop, web and VS Code front end for OpenCode.

After yetone/magpie ``internal/agent/openchamber.go`` (MIT License, Copyright
(c) yetone). OpenChamber runs an OpenCode of its own — the desktop app
bundles OpenCode 2 — on the user's OpenCode config (``opencode.py``). Its own
settings are under ``$OPENCHAMBER_DATA_DIR``, ``~/.config/openchamber`` by
default (not moved by ``XDG_CONFIG_HOME``)::

    preferences.json  {"version":1,"fields":{"<key>":{"value":…,"updatedAt":<ms>}}}: the profile
    settings.json     the instance's facts, plus a copy of the profile's values for older builds

Each new session is sent the model it picks, in this order: the project's
``defaultModel`` (settings.json ``projects[]``), the profile's, the agent's
own, and only then OpenCode's ``model`` — so OpenProgram sets the profile's
``defaultModel``. Its small model (titles, commit messages) is
``smallModelOverride`` once ``smallModelUseDefault`` is false. A model is
``provider/model`` as OpenCode names it, so OpenProgram's provider goes into
the OpenCode config too, shared with OpenCode while both are connected.
Its model requests are its OpenCode's, and counted as OpenCode's.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from . import opencode
from .base import Agent, Field, Wiring, Write
from .opencode_format import cleared, discovers, is_two, opencode_writes, pick, read_doc, unreadable

AGENT_ID = "openchamber"


def home() -> Path:
    moved = os.environ.get("OPENCHAMBER_DATA_DIR", "").strip()
    if moved:
        return Path(os.path.abspath(os.path.expanduser(moved)))
    return Path.home() / ".config" / "openchamber"


def prefs_path() -> Path:
    return home() / "preferences.json"


def settings_path() -> Path:
    return home() / "settings.json"


def _has_prefs() -> bool:
    """Whether preferences.json is there; one that isn't a version-1
    document OpenChamber doesn't write to either, so it is left alone."""
    path = prefs_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    try:
        doc = json.loads(raw)
    except ValueError:
        doc = None
    if not isinstance(doc, dict) or doc.get("version") != 1 or not isinstance(doc.get("fields"), dict):
        raise unreadable(path, "isn't a preferences file OpenChamber can read; fix or remove it first")
    return True


def _profile(values: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """The profile's keys: set (``None`` is taken out)."""
    sets = {key: value for key, value in values.items() if value is not None}
    return sets, [key for key, value in values.items() if value is None]


def _read(path: Path) -> dict[str, Any]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _current_model(has: bool) -> Any:
    """The profile's ``defaultModel`` now: preferences.json's, else settings.json's."""
    if has:
        field = (_read(prefs_path()).get("fields") or {}).get("defaultModel")
        if isinstance(field, dict) and "value" in field:
            return field["value"]
    return _read(settings_path()).get("defaultModel")


SMALL = ("smallModelUseDefault", "smallModelOverride")


def _wire(at: Wiring) -> list[Write]:
    oc_path = opencode.config_path()
    doc, v1 = read_doc(oc_path), at.base("openai-chat")
    model, own = pick(doc, at.model, v1)
    has = _has_prefs()
    profile: dict[str, Any] = {"defaultModel": model}
    if model != _current_model(has):
        # the variant was the old model's, as OpenChamber's own picker has it
        profile["defaultVariant"] = None
    needs = not own
    small = at.values.get("small")
    if small:
        small, own = pick(doc, small, v1)
        needs = needs or not own
        profile.update(smallModelUseDefault=False, smallModelOverride=small)
    sets, deletes = _profile(profile)
    found = opencode.version()
    # the OpenCode config as the OpenCode on PATH reads it, as OpenCode's own
    # connect writes it: the two never write it in each other's words
    writes = opencode_writes(oc_path, at, two=is_two(found), discover=discovers(found), picks=False) if needs else []
    files = at.previous.get("files") or {}
    if has:
        now = int(time.time() * 1000)
        prefs: dict[str, Any] = {}
        for key, value in sets.items():
            prefs[f"fields.{key}.value"] = value
            prefs[f"fields.{key}.updatedAt"] = now
        gone = [f"fields.{key}" for key in deletes]
        if not small:
            # the agent's own small model again: OpenProgram's goes (magpie set(""))
            gone += cleared(read_doc(prefs_path()), files.get(str(prefs_path())) or {},
                            [(f"fields.{key}.value", f"fields.{key}.updatedAt") for key in SMALL])
        writes.append(Write(prefs_path(), "json", sets=prefs, deletes=gone))
    # the copy in settings.json, where that file is or the profile has
    # nowhere else to go
    if not has or settings_path().is_file():
        gone = list(deletes)
        if not small and settings_path().is_file():
            gone += cleared(read_doc(settings_path()), files.get(str(settings_path())) or {},
                            [(key,) for key in SMALL])
        writes.append(Write(settings_path(), "json", sets=sets, deletes=gone))
    return writes


def _project_models() -> dict[str, str]:
    """The projects that pick a default model of their own, name → model."""
    try:
        projects = json.loads(settings_path().read_text(encoding="utf-8")).get("projects") or []
    except (OSError, ValueError, AttributeError):
        return {}
    out = {}
    for project in projects if isinstance(projects, list) else []:
        if isinstance(project, dict) and project.get("defaultModel"):
            name = project.get("label") or Path(str(project.get("path") or "")).name
            out[name] = project["defaultModel"]
    return out


def _notice() -> str:
    from .running import any_running
    notes = [f"OpenChamber's project {name} has a default model of its own ({model}), which it uses there instead."
             for name, model in sorted(_project_models().items()) if not str(model).startswith("openprogram/")]
    if any_running(r"OpenChamber\.app/", r"(^|/)openchamber( |$)"):
        notes.append("OpenChamber takes the new model for new sessions; sessions already open keep theirs.")
    return " ".join(notes)


def agent() -> Agent:
    return Agent(
        id=AGENT_ID, name="OpenChamber", icon="openchamber", aliases=("chamber",), kind="app",
        protocol="openai-chat", bin="openchamber", dir=home, path=prefs_path,
        fields=[Field("model", "Model"), Field("small", "Small model", quiet=True)],
        wire=_wire, notice=_notice,
        # the provider it names is OpenCode's, in a file both write: one key
        key_of="opencode",
    )
