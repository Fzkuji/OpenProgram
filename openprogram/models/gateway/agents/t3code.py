"""T3 Code (``$T3CODE_HOME/userdata/settings.json``, default ``~/.t3``).

After yetone/magpie ``internal/agent/t3code.go`` and ``internal/appdir/t3code.go``
(MIT License, Copyright (c) yetone).

T3 Code (pingdotgg/t3code, a GUI that drives Claude Code, Codex, Cursor and
others) keeps its settings in ``~/.t3/userdata/settings.json``, a sparse JSON
file its server watches and reloads on an outside edit. Each provider it
lists is an instance of a driver, and besides the built-in ones (keyed by the
driver, ``claudeAgent``, ``codex``, …) it takes instances of one's own::

    {"providerInstances":{"<id>":{"driver":"claudeAgent","displayName":…,
      "enabled":true,"environment":[{"name":…,"value":…,"sensitive":false}],
      "config":{"customModels":[{"slug":…,"name":…,"capabilities":
        {"optionDescriptors":[{"id":"effort","type":"select",…}]}}]}}}}

A claudeAgent instance runs Claude Code (the user's ``~/.claude`` settings
read as Claude Code reads them) with the instance's environment added, and
lists Claude's models and its custom models, each sent as Claude Code's
model. So OpenProgram is an instance of its own, ``openprogram``: Claude Code
pointed at the gateway (``ANTHROPIC_BASE_URL`` and the gateway key), every
enabled model one of its custom models, with a Reasoning pick of the levels
it takes and a Thinking switch (:func:`capabilities`). It is OpenProgram's
alone: the user's own instances, their custom models and T3's other keys stay
as they are, and turning it off takes only it out. A binary path or Claude
home the user gave T3's own Claude is carried over, so it runs the same
Claude Code. Claude Code applies its own settings' env over the instance env
T3 adds, so the gateway is also put in the instance's launch arguments as an
inline ``--settings`` (flag settings win over settings files,
:func:`claude_args`); a ``--settings`` of the user's naming a file is theirs.

T3 Code runs each thread on the harness of the instance its model is picked
from, so OpenProgram has a Codex instance too, ``openprogram-codex``
(OpenProgram · Codex): a codex instance whose ``launchArgs``, which T3 passes
to ``codex app-server`` and ``codex exec``, are Codex's own ``-c`` overrides
making the gateway its model provider and OpenProgram's model catalog for T3
(``openprogram-codex-models.json`` beside settings.json) its catalog. The
user's ``~/.codex/config.toml`` isn't written. One the user removed in T3
isn't brought back while T3 Code stays connected, only by connecting again.

Each instance's name and whether it is on are the user's once set; OpenProgram
writes them only when the instance is new. Differences from magpie: a model
with a 1M window is not marked ``[1m]`` (the gateway does not read that
mark), and the Codex instance is written only when Codex's catalog can be made
(it needs Codex's base instructions, as the Codex wiring does).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

from .. import edit
from ..edit import ConfigText, ConfigUnreadable
from .base import Agent, Field, Wiring, Write

INSTANCE_ID = "openprogram"
CODEX_INSTANCE_ID = "openprogram-codex"
INSTANCE = f"providerInstances.{INSTANCE_ID}"
CODEX_INSTANCE = f"providerInstances.{CODEX_INSTANCE_ID}"
CATALOG_NAME = "openprogram-codex-models.json"

#: Claude Code's effort levels, in order (magpie ``claudeEfforts``).
CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")
#: T3 Code's names for Claude Code's levels, as its own Claude models show them.
EFFORT_LABELS = {"low": "Low", "medium": "Medium", "high": "High", "xhigh": "Extra High", "max": "Max"}
#: Auto mode's review (magpie ``claudeReviewEnv``) and tool search (``claudeSearchEnv``).
REVIEW_ENV = "CLAUDE_CODE_AUTO_MODE_SERVER"
SEARCH_ENV = "ENABLE_TOOL_SEARCH"
#: The variables of the instance OpenProgram sets; tool search is its only
#: until the user gives it a value there.
OWN_ENV = ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", REVIEW_ENV)
#: Provider switches that would take the instance past the gateway (magpie ``agentenv.ClaudeProviders``).
CLAUDE_PROVIDERS = ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")


def base() -> Path:
    """T3 Code's folder: ``$T3CODE_HOME``, a leading ``~`` being home, else ``~/.t3``."""
    value = os.environ.get("T3CODE_HOME") or ""
    if not value:
        return Path.home() / ".t3"
    if value.startswith("~"):
        return Path(str(Path.home()) + value[1:])
    return Path(value)


def config_path() -> Path:
    return base() / "userdata" / "settings.json"


def catalog_path(path: Path) -> Path:
    """The model catalog of OpenProgram's Codex instance, beside settings.json."""
    return path.parent / CATALOG_NAME


def t3_app() -> bool:
    """T3 Code's app is in a Mac's Applications, by the names its builds take."""
    if sys.platform != "darwin":
        return False
    for folder in (Path("/Applications"), Path.home() / "Applications"):
        for name in ("T3 Code (Alpha).app", "T3 Code.app"):
            if (folder / name).exists():
                return True
    return False


# T3 Code's tokenizeCliArgs (packages/shared/src/cliArgs.ts)

def split_args(text: str) -> list[tuple[str, int, int]]:
    """T3 Code's tokenizer: each argument of ``text`` and where it is (from, to)."""
    out: list[tuple[str, int, int]] = []
    cur, quote, quoted, start = "", "", False, -1
    i = 0
    while i < len(text):
        c = text[i]
        if quote:
            if c == quote:
                quote, quoted = "", True
            elif c == "\\" and quote == '"' and i + 1 < len(text) and text[i + 1] in '"\\$`':
                cur += text[i + 1]
                i += 1
            else:
                cur += c
            i += 1
            continue
        if start < 0 and not c.isspace():
            start = i
        if c in "'\"":
            quote, quoted = c, True
        elif c.isspace():
            if cur or quoted:
                out.append((cur, start, i))
            cur, quoted, start = "", False, -1
        elif c == "\\" and i + 1 < len(text) and (text[i + 1].isspace() or text[i + 1] in "'\""):
            cur += text[i + 1]
            i += 1
        else:
            cur += c
        i += 1
    if cur or quoted:
        out.append((cur, start, len(text)))
    return out


def settings_of(args: str) -> tuple[bool, int, int, Optional[dict[str, Any]]]:
    """The ``--settings`` T3 Code reads from a Claude instance's launchArgs:
    ``(there, from, to, inline JSON)`` — the last one, ``--append-system-prompt``
    taking the argument after it whatever it is, nothing after a bare ``--``
    read; inline is ``None`` when it names a file (the user's own)."""
    toks = split_args(args)
    found: tuple[bool, int, int, Optional[dict[str, Any]]] = (False, 0, 0, None)
    i = 0
    while i < len(toks):
        value = toks[i][0]
        if value == "--":
            break
        if not value.startswith("--"):
            i += 1
            continue
        start = toks[i][1]
        name, eq, val = value[2:].partition("=")
        end, has = toks[i][2], bool(eq)
        if not eq and i + 1 < len(toks) and (name == "append-system-prompt" or not toks[i + 1][0].startswith("--")):
            val, end, has = toks[i + 1][0], toks[i + 1][2], True
            i += 1
        if name == "settings":
            inline = None
            stripped = val.strip()
            if has and stripped.startswith("{"):
                try:
                    parsed = json.loads(stripped)
                    inline = parsed if isinstance(parsed, dict) else None
                except ValueError:
                    inline = None
            found = (True, start, end, inline)
        i += 1
    return found


def quote(value: str) -> str:
    """``value`` as one argument for T3 Code's tokenizer: single quotes unless it has one."""
    if "'" not in value:
        return f"'{value}'"
    for a, b in (("\\", "\\\\"), ('"', '\\"'), ("$", "\\$"), ("`", "\\`")):
        value = value.replace(a, b)
    return f'"{value}"'


def claude_args(args: str, gw: str, key: str) -> tuple[str, bool]:
    """``args``, the launch arguments of OpenProgram's Claude instance, with the
    gateway and its key in the env of their ``--settings``, so Claude Code takes
    them as flag settings, which win over its own settings files (where another
    router's ``ANTHROPIC_BASE_URL`` beat the instance env T3 adds). An inline
    ``--settings`` of the user's keeps all the rest of it; one that names a file
    is theirs, left as it is, and False says the gateway isn't there."""
    there, start, end, inline = settings_of(args)
    if there and inline is None:
        return args, False
    settings = dict(inline or {})
    env = dict(settings.get("env") or {}) if isinstance(settings.get("env"), dict) else {}
    env["ANTHROPIC_BASE_URL"] = gw
    env["ANTHROPIC_AUTH_TOKEN"] = key
    # an API key or a cloud switch from the user's files or the shell would
    # take the instance past the gateway: blanked, as a launch file does
    from .claude_code import LAUNCH_BLANKS
    env.update({name: "" for name in LAUNCH_BLANKS})
    settings["env"] = env
    arg = "--settings " + quote(json.dumps(settings, separators=(",", ":"), ensure_ascii=False))
    if not there:
        return (args.strip() + " " + arg).strip(), True
    return args[:start] + arg + args[end:], True


def capabilities(levels: list[str]) -> Optional[dict[str, Any]]:
    """The picks T3 Code shows beside a custom model: without them it shows
    none and sends no effort. Reasoning is the levels of Claude Code's the
    model takes, medium chosen first as on T3's own Claude models (else the
    lowest); Thinking, on at first, is the switch for a model that thinks at
    some level. ``None`` for a model with neither."""
    descriptors = []
    options = [{"id": level, "label": EFFORT_LABELS[level]} for level in CLAUDE_EFFORTS if level in levels]
    if options:
        default = next((o for o in options if o["id"] == "medium"), options[0])
        default["isDefault"] = True
        descriptors.append({"id": "effort", "label": "Reasoning", "type": "select", "options": options})
    if any(level not in ("none", "off") for level in levels):
        descriptors.append({"id": "thinking", "label": "Thinking", "type": "boolean", "currentValue": True})
    return {"optionDescriptors": descriptors} if descriptors else None


def _custom_models(at: Wiring) -> list[dict[str, Any]]:
    rows = sorted(at.models, key=lambda row: row["id"] != at.model)
    if at.model and at.model not in {row["id"] for row in rows}:
        rows.insert(0, {"id": at.model, "name": at.model})
    out = []
    for row in rows:
        entry: dict[str, Any] = {"slug": row["id"], "name": str(row.get("name") or row["id"])}
        caps = capabilities([str(level) for level in row.get("thinking_levels") or []])
        if caps:
            entry["capabilities"] = caps
        out.append(entry)
    return out


def _reviewed(at: Wiring) -> bool:
    """Every model listed is a Claude model on Anthropic's own API, whose auto
    mode review comes back (magpie ``claudeReviewed``)."""
    return bool(at.models) and all(row.get("provider") == "anthropic" for row in at.models)


def _environment(at: Wiring, theirs: Any) -> list[dict[str, Any]]:
    env: list[dict[str, Any]] = [
        {"name": "ANTHROPIC_BASE_URL", "value": at.base("anthropic"), "sensitive": False},
        {"name": "ANTHROPIC_AUTH_TOKEN", "value": at.key, "sensitive": True},
    ]
    if not _reviewed(at):
        env.append({"name": REVIEW_ENV, "value": "0", "sensitive": False})
    env.append({"name": SEARCH_ENV, "value": "true", "sensitive": False})
    for item in theirs if isinstance(theirs, list) else []:
        name = item.get("name") if isinstance(item, dict) else None
        if name == SEARCH_ENV:
            env = [e for e in env if e["name"] != SEARCH_ENV]
        # a provider switch would take the instance past the gateway
        if name and name not in OWN_ENV and name not in CLAUDE_PROVIDERS:
            env.append(item)
    return env


def _carried(doc: dict[str, Any], driver: str) -> dict[str, str]:
    """The binary path and home the user gave T3's own instance of ``driver``,
    else the setting it was before T3 had instances."""
    out = {}
    for key in ("binaryPath", "homePath"):
        value = edit.get_path(doc, f"providerInstances.{driver}.config.{key}")[1]
        if not value:
            value = edit.get_path(doc, f"providers.{driver}.{key}")[1]
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()
    return out


def toml_string(value: str) -> str:
    """``value`` as a TOML basic string (JSON's quoting, which TOML reads alike)."""
    return json.dumps(value, ensure_ascii=False)


def codex_arg(key: str, value: str) -> str:
    """Codex's ``-c`` override of ``key`` to TOML ``value``, quoted as T3 Code's tokenizer reads it."""
    text = f"{key}={value}"
    for a, b in (("\\", "\\\\"), ('"', '\\"'), ("$", "\\$"), ("`", "\\`")):
        text = text.replace(a, b)
    return f'-c "{text}"'


def codex_args(at: Wiring, path: Path) -> str:
    """The launch arguments of OpenProgram's Codex instance: the gateway as
    Codex's provider, as Codex's own ``[model_providers.openprogram]`` is when
    connected (``codex.py``), and its catalog."""
    table = f"model_providers.{INSTANCE_ID}."
    return " ".join([
        codex_arg("model_provider", toml_string(INSTANCE_ID)),
        codex_arg(table + "name", toml_string("OpenProgram")),
        codex_arg(table + "base_url", toml_string(at.base("openai-responses"))),
        codex_arg(table + "wire_api", toml_string("responses")),
        codex_arg(table + "experimental_bearer_token", toml_string(at.key)),
        codex_arg("model_catalog_json", toml_string(str(catalog_path(path)))),
    ])


def _codex_catalog(at: Wiring) -> Optional[str]:
    from ..codex_app import catalog_text
    versions = list(at.options.get("codex_versions") or [])
    if not versions:
        from .lifecycle import options_for
        versions = list(options_for("codex").get("codex_versions") or [])
    return catalog_text(at.model, versions, at.models)


def _removed_codex(at: Wiring, doc: dict[str, Any], path: Path) -> bool:
    """OpenProgram wrote its Codex instance and the user removed it in T3 since."""
    kept = ((at.previous.get("files") or {}).get(str(path)) or {})
    wrote = kept.get("wrote") or {}
    return f"{CODEX_INSTANCE}.driver" in wrote and not edit.get_path(doc, CODEX_INSTANCE)[0]


def _wire(at: Wiring) -> list[Write]:
    path = config_path()
    try:
        doc = edit.load("json", ConfigText.read(path).text, str(path))
    except ConfigUnreadable:
        doc = {}  # the engine reads it again and refuses
    sets: dict[str, Any] = {
        f"{INSTANCE}.driver": "claudeAgent",
        f"{INSTANCE}.environment": _environment(at, edit.get_path(doc, f"{INSTANCE}.environment")[1]),
        f"{INSTANCE}.config.customModels": _custom_models(at),
    }
    if not edit.get_path(doc, INSTANCE)[0]:
        sets[f"{INSTANCE}.displayName"] = "OpenProgram"
        sets[f"{INSTANCE}.enabled"] = True
    for key, value in _carried(doc, "claudeAgent").items():
        sets[f"{INSTANCE}.config.{key}"] = value
    theirs = edit.get_path(doc, f"{INSTANCE}.config.launchArgs")[1]
    args, ok = claude_args(theirs if isinstance(theirs, str) else "", at.base("anthropic"), at.key)
    if ok:
        sets[f"{INSTANCE}.config.launchArgs"] = args
    writes = []
    catalog = None if _removed_codex(at, doc, path) else _codex_catalog(at)
    if catalog is not None:
        writes.append(Write(catalog_path(path), "file", content=catalog, listing=True))
        sets[f"{CODEX_INSTANCE}.driver"] = "codex"
        sets[f"{CODEX_INSTANCE}.config.launchArgs"] = codex_args(at, path)
        if not edit.get_path(doc, CODEX_INSTANCE)[0]:
            sets[f"{CODEX_INSTANCE}.displayName"] = "OpenProgram · Codex"
            sets[f"{CODEX_INSTANCE}.enabled"] = True
        for key, value in _carried(doc, "codex").items():
            sets[f"{CODEX_INSTANCE}.config.{key}"] = value
    writes.insert(0, Write(path, "json", sets=sets, listing=True))
    return writes


def agent() -> Agent:
    # ~/.t3 is made at its first start; a Mac app never opened yet is found
    # by its bundle. No command: `t3` is a common name.
    return Agent(
        id="t3code", name="T3 Code", icon="t3code", aliases=("t3", "t3-code"), kind="app",
        protocol="anthropic", dir=base, path=config_path, fields=[Field("model", "Model")],
        detect=t3_app, wire=_wire,
        # the key lands in T3 Code's settings and in argv: this agent's own
        # HMAC key, never the gateway key itself
        named_key=True,
    )
