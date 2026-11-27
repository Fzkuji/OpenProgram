"""Antigravity CLI, ``agy`` (``~/.gemini/antigravity-cli``).

After yetone/magpie ``internal/agent/agy.go`` and ``plugged_launch.go`` (MIT
License, Copyright (c) yetone). agy keeps its settings in
``settings.json``. With ``"modelProvider": "gemini"`` it asks a Gemini API
endpoint rather than Google's own backend, and lists the models of
``customModelsConfig.customModels``, each ``{apiProvider, modelName}``, the
one picked being ``model``. The gateway speaks the Gemini API, so connecting
adds one custom model per enabled OpenProgram model, keyed
``openprogram/<provider>/<model>``, and a model that takes two or more of
low, medium and high once more per level (``…:<level>`` with that
``thinkingLevel``): agy turns its ``/effort`` away for every custom model,
and lists them by key in ``/model``, so each level is one of its own there.

None of that goes into ``settings.json``: with ``modelProvider`` gemini in it
every agy runs on an API key, with no Google account, and remote control,
which needs the sign-in, is gone. The provider, the model and the custom
models (the user's own too, since agy takes the key whole) are in a file of
OpenProgram's beside it, ``openprogram-settings.json``, written whole and
rewritten as the model list changes, which the launch command hands agy as
``--settings`` for that one session. ``settings.json`` stays the user's, and
a plain ``agy``, and ``agy remote-control``, run on their Google sign-in.

agy takes the endpoint and key only from its environment
(``GOOGLE_GEMINI_BASE_URL``, ``GEMINI_API_KEY``; no ``.env`` file), so the
agent's row gives the command that starts it with them (:attr:`Agent.launch`).
That names the model with ``--model`` too: agy asks for a custom model picked
in settings or its ``/model`` picker with no model name ("model is empty");
only one given on the command line resolves. Its requests go out as Google's
Gemini SDK's, with nothing of agy's own, so the gateway knows them by the
key alone: agy is given a key of its own.

So that typing ``agy`` just works, OpenProgram puts a function named
``agy`` in the profile of each shell the user has, between ``# >>>
openprogram agy >>>`` and ``# <<< openprogram agy <<<`` (``shellfn.py``),
while agy is on one of OpenProgram's models, and takes it out on
disconnect, after a ``/model`` pick agy wrote into ``settings.json``
is taken out of it (``unwire``). It runs ``openprogram agy run``, which starts the real agy with
the two variables, ``--settings`` and ``--model`` while OpenProgram's file
names one of its models, and as it is otherwise (and plain for
``remote-control``, which needs agy's Google sign-in); PowerShell's asks
``openprogram agy env`` for them, since the app it would run is a GUI
program PowerShell doesn't wait for. A terminal open before takes the
command from the row.

Two choices sit beside the model in its row: the effort the gateway asks
agy's requests for (``agent_effort.py``: agy's ``/effort`` is turned away
for custom models and its settings have no key for it), and "fast tools",
OpenProgram's plugin for Antigravity (``fasttools/``), written by the
``after`` hook while it is picked and kept starting this OpenProgram by
``keep``. Both are the user's own settings, not wiring (``kept`` fields,
``kept_picks.py``): they outlive disconnecting and come back with the next
connect, and fast tools is set on the row while disconnected too. The
WSL distro variant of the function waits for WSL support in the registry.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .base import Agent, Field, Wiring, Write
from .shellfn import posix_quote, ps_quote

PREFIX = "openprogram/"
MODELS_KEY = "customModelsConfig.customModels"
OVERRIDE = "openprogram-settings.json"
API_PROVIDER = "API_PROVIDER_GOOGLE_GEMINI"
#: The levels a custom model of agy's takes as its ``thinkingLevel``, weakest first.
LEVEL_NAMES = ("low", "medium", "high")
#: What asks agy for its remote control, which only a Google sign-in has.
PLAIN = ("remote-control", "--remote-control", "-remote-control")


def home() -> Path:
    return Path.home() / ".gemini" / "antigravity-cli"


def config_path() -> Path:
    return home() / "settings.json"


def override_path() -> Path:
    return home() / OVERRIDE


def model_key(model: str) -> str:
    """The custom model's key for an OpenProgram model ``provider/model``."""
    return PREFIX + model


def levels(efforts: list[str]) -> list[str]:
    """The levels of ``efforts`` agy's custom models can ask, weakest first:
    none unless two or more, as one is no choice (magpie ``agyLevels``)."""
    lowered = {str(e).lower() for e in efforts or []}
    found = [level for level in LEVEL_NAMES if level in lowered]
    return found if len(found) >= 2 else []


def level_key(key: str, level: str) -> str:
    return f"{key}:{level}"


def own_models() -> dict[str, Any]:
    """The user's own custom models, as ``settings.json`` has them."""
    from ..edit import ConfigText, ConfigUnreadable, get_path, load
    try:
        text = ConfigText.read(config_path())
        if not text.exists:
            return {}
        present, models = get_path(load("jsonc", text.text, str(config_path())), MODELS_KEY)
    except (ConfigUnreadable, OSError):
        return {}
    if not present or not isinstance(models, dict):
        return {}
    return {key: value for key, value in models.items() if not str(key).startswith(PREFIX)}


def override_text(model: str, served: list[dict[str, Any]]) -> str:
    """OpenProgram's settings file: the provider, the model and every custom
    model, the user's own too, as agy takes the key whole."""
    models: dict[str, Any] = dict(own_models())
    for row in served:
        key = model_key(row["id"])
        models[key] = {"apiProvider": API_PROVIDER, "modelName": row["id"]}
        for level in levels(row.get("thinking_levels") or []):
            models[level_key(key, level)] = {"apiProvider": API_PROVIDER, "modelName": row["id"], "thinkingLevel": level}
    body = {"modelProvider": "gemini", "model": model_key(model), "customModelsConfig": {"customModels": models}}
    return json.dumps(body, indent=2, ensure_ascii=False) + "\n"


def _json_get(path: Path, key: str) -> str:
    from ..edit import ConfigText, ConfigUnreadable, get_path, load
    try:
        text = ConfigText.read(path)
        if not text.exists:
            return ""
        present, value = get_path(load("jsonc", text.text, str(path)), key)
    except (ConfigUnreadable, OSError):
        return ""
    return value if present and isinstance(value, str) else ""


def _was() -> str:
    """The model agy was on when OpenProgram was switched on for it, when it was the user's own."""
    from .. import stash
    try:
        was = str((stash.entry("agy") or {}).get("was") or "")
    except stash.StashUnreadable:
        return ""
    return "" if was.startswith(PREFIX) else was


def migrate() -> str:
    """While OpenProgram's file is on one of its models, a model of
    OpenProgram's in ``settings.json`` is agy's ``/model``, which writes the
    pick into ``settings.json``, ``--settings`` or not (magpie ``migrate``).
    That pick is kept in OpenProgram's file (it is what agy starts on next)
    and ``settings.json``'s model goes back to what it was when OpenProgram
    was switched on, or out of it. The pick, "" for none."""
    from ..edit import ConfigText, ConfigUnreadable, edit
    picked = _json_get(config_path(), "model")
    if not picked.startswith(PREFIX) or not _json_get(override_path(), "model").startswith(PREFIX):
        return ""
    try:
        text = ConfigText.read(config_path())
        was = _was()
        new = edit("jsonc", text.text, sets={"model": was} if was else None,
                   deletes=None if was else ["model"], where=str(config_path()))
        if new != text.text:
            text.write(new)
    except (ConfigUnreadable, OSError):
        pass
    return picked


def _wire(at: Wiring) -> list[Write]:
    # the model agy runs on is its pick once made (its /model, moved here by
    # migrate): a sync, or a connect that keeps the row's model, leaves it; a
    # model picked on the row wins over it (magpie's Set drops migrate's pick)
    picked = migrate()
    if (at.previous.get("values") or {}).get("model") != at.model:
        picked = ""
    elif not picked:
        picked = override_model()
    text = override_text(at.model, at.models)
    if picked and picked != model_key(at.model):
        body = json.loads(text)
        if picked in body["customModelsConfig"]["customModels"]:
            body["model"] = picked
            text = json.dumps(body, indent=2, ensure_ascii=False) + "\n"
    return [Write(override_path(), "file", content=text, listing=True)]


def _unwire(record: dict[str, Any]) -> None:
    # a /model pick agy wrote into settings.json goes out of it before the
    # stashed values go back (magpie Disconnect runs migrate first)
    migrate()


def override_model() -> str:
    """The model OpenProgram's file names while it is one of OpenProgram's, else ""."""
    model = _json_get(override_path(), "model")
    return model if model.startswith(PREFIX) else ""


def _on_route(_: Path, connected: bool, record: dict[str, Any]) -> None:
    from .. import stash
    if connected:
        own = _json_get(config_path(), "model")
        try:
            stash.update("agy", was=own if not own.startswith(PREFIX) else "")
        except (OSError, stash.StashUnreadable):
            pass
    shell(connected)


def launch_command(gateway: str, key: str, settings: str, model: str, powershell: bool) -> str:
    """The command that starts agy on the gateway at ``gateway`` with ``key``
    and OpenProgram's settings file, in PowerShell or a POSIX shell (magpie ``agyLaunchAt``)."""
    if powershell:
        return (f'$env:GEMINI_API_KEY="{key}"; $env:GOOGLE_GEMINI_BASE_URL="{gateway}"; '
                f"agy --settings {ps_quote(settings)} --model {ps_quote(model)}")
    return (f"GEMINI_API_KEY={key} GOOGLE_GEMINI_BASE_URL={gateway} "
            f"agy --settings {posix_quote(settings)} --model {posix_quote(model)}")


def _launch(at: Wiring) -> str:
    return launch_command(at.base("gemini"), at.key, str(override_path()), model_key(at.model),
                          sys.platform == "win32")


def _values() -> dict[str, str]:
    from .. import stash
    try:
        return dict((stash.entry("agy") or {}).get("values") or {})
    except stash.StashUnreadable:
        return {}


def _wiring() -> Wiring | None:
    from . import find
    from .engine import wiring
    values = _values()
    return wiring(find("agy"), values) if values.get("model") else None


# Starting it (magpie Agent.Start, plugged_launch.go Starting, cli/start_cli.go)

def starting() -> dict[str, Any]:
    """What starts agy: the command, the variables, the flags put before the
    user's arguments, and the model after ``--model``; ``model`` "" while it
    isn't on OpenProgram's, and it starts as it is."""
    out: dict[str, Any] = {"bin": "agy", "flag": "--model", "plain": list(PLAIN), "env": {}, "args": [], "model": ""}
    model = override_model()
    at = _wiring()
    if model and at is not None:
        out.update(env={"GEMINI_API_KEY": at.key, "GOOGLE_GEMINI_BASE_URL": at.base("gemini")},
                   args=["--settings", str(override_path())], model=model)
    return out


def run(args: list[str]) -> int:
    """``openprogram agy run [args…]``: the agy on PATH, given OpenProgram's
    variables and model while it is on one of OpenProgram's models, and as
    it is otherwise (and with ``remote-control``, which needs agy's Google
    sign-in). Every argument is agy's, ``--help`` too."""
    from .shellfn import run_launch
    return run_launch(starting(), args)


def env_lines() -> list[str]:
    """``openprogram agy env``: the variables, each flag's value
    (``settings=<file>``) and the model, a ``NAME=value`` a line; nothing
    while agy isn't on OpenProgram's models. For PowerShell's function."""
    from .shellfn import launch_env_lines
    return launch_env_lines(starting())


START_USAGE = """openprogram agy run [args…]   start agy, on OpenProgram while its model is one of OpenProgram's

The agy on your PATH starts with GEMINI_API_KEY, GOOGLE_GEMINI_BASE_URL,
--settings <OpenProgram's settings file> and --model <its model> (not when
you give --model yourself); every argument is agy's. Off OpenProgram's
models, or with remote-control, it starts as it is. The function agy
OpenProgram puts in your shell's profile runs this.

openprogram agy env           the variables and model it starts agy with, a
                              NAME=value a line; nothing while it isn't on OpenProgram
"""


# The function agy in the user's shells (magpie agyFunction, agyShell)

def function(kind: str, prog: list[str]) -> str:
    """The function agy in a shell of ``kind``, which runs ``prog`` (this
    OpenProgram) to start it; the agy on PATH when ``prog`` is gone."""
    from .shellfn import launch_function
    return launch_function(kind, prog, "agy", {
        "bin": "agy", "flag": "--model", "plain": list(PLAIN), "args": ["--settings", ""],
        "env": dict.fromkeys(("GEMINI_API_KEY", "GOOGLE_GEMINI_BASE_URL"), "")})


def shell(on: bool) -> list[str]:
    """Give the user's shells the function agy, or take it out of them; the
    profiles that have it. What fails leaves the setting as it is: the
    row's command still starts it."""
    from . import shellfn
    if not on:
        shellfn.drop_shell_function("agy")
        return []
    return shellfn.shell_function("agy", function)


def _notice() -> str:
    from . import shellfn
    at = _wiring()
    if at is None:
        return ""
    command = _launch(at)
    held = shellfn.shell_function_in("agy")
    if held:
        profiles = ", ".join(shellfn.tilde_home(p) for p in held)
        return (f"Type agy in a new terminal: OpenProgram added it to {profiles}, to start agy on "
                f"OpenProgram's gateway and model (in a terminal open now: {command}).")
    return f"agy takes OpenProgram's gateway and model only from how it is started — start it with {command}"


# The choices beside the model: the effort, fast tools (magpie agyEffortField, agyFastTools)

FAST_TOOLS = "fasttools"


def _effort_options() -> list[tuple[str, str]]:
    # offered only while agy is on OpenProgram's models
    if not _values().get("model"):
        return []
    from ..agent_effort import picked_efforts
    from ..conversion import enabled_model_refs
    return picked_efforts(enabled_model_refs())


def _fast_tools(on: bool) -> None:
    from .fasttools import NotOursError, is_on, remove, write
    from .shellfn import cli_program
    try:
        if on:
            write(Path.home(), cli_program())
        elif is_on(Path.home()):
            remove(Path.home())
    except (NotOursError, OSError, ValueError):
        pass  # a plugin of that name of someone else's stays theirs


def _fast_tools_on() -> bool:
    from ..kept_picks import kept
    return kept("agy").get(FAST_TOOLS) == "on"


def _apply_kept(picks: dict[str, str]) -> None:
    # fast tools set on the row, connected or not (magpie agyFastTools Set)
    _fast_tools(picks.get(FAST_TOOLS) == "on")


def _after(written: bool, record: dict[str, Any]) -> None:
    # the user's own setting: picked with a connect, left by a disconnect
    if written:
        _fast_tools(_fast_tools_on())


def _keep() -> None:
    # the plugin starts this OpenProgram, which may have moved (magpie fasttools.Keep)
    if _fast_tools_on():
        from .fasttools import keep
        from .shellfn import cli_program
        try:
            keep(Path.home(), cli_program())
        except (OSError, ValueError):
            pass


def agent() -> Agent:
    return Agent(
        id="agy", name="Antigravity CLI", icon="antigravity", aliases=("antigravity-cli",), kind="cli",
        protocol="gemini", bin="agy", dir=home, path=config_path,
        fields=[Field("model", "Model"),
                Field("effort", "Effort", quiet=True, options=_effort_options, kept=True),
                Field(FAST_TOOLS, "Fast Tools", quiet=True, options=lambda: [("on", "On")], kept=True)],
        named_key=True, wire=_wire, unwire=_unwire, launch=_launch, notice=_notice, on_route=_on_route,
        after=_after, keep=_keep, apply_kept=_apply_kept,
    )
