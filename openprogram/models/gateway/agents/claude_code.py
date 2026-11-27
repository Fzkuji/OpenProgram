"""Claude Code (``$CLAUDE_CONFIG_DIR/settings.json``, default ``~/.claude``).

After yetone/magpie ``internal/agent/claude.go``, ``claude_dirs.go``,
``claude_subagent.go`` and ``claude_launch.go`` (MIT License, Copyright (c)
yetone). Connecting sets ``env.ANTHROPIC_BASE_URL``, ``env.ANTHROPIC_AUTH_TOKEN``
(the gateway key), the tier variables and the top-level ``model``;
``CLAUDE_CODE_USE_BEDROCK`` / ``_VERTEX`` / ``_FOUNDRY`` are removed while it is
on. ``ANTHROPIC_MODEL`` is never set: it would override the model picked
with ``/model``. Claude Code's own sign-in is never read or passed on: the
gateway answers it with OpenProgram's own provider keys only.

Subagents (``CLAUDE_CODE_SUBAGENT_MODEL``) get a model of their own only when
one is picked; unset they run on the session's, as Claude Code does by
itself. Claude Code's built-in Explore and Plan declare ``model: inherit``,
which it reads before the variable, so the gateway moves their turns itself
(``gateway/claude_subagent.py``).

Claude Code kept in more than one config folder (``CLAUDE_CONFIG_DIR``, as in
``alias claude-work='CLAUDE_CONFIG_DIR=~/.claude-work claude'``): the one the
variable (or ``~/.claude``) names is the ``claude_code`` row; every other
folder beside it under the home folder that is named ``.claude…`` and holds
Claude Code's own files is a row of its own, ``claude_code#<name>``, with its
own settings.json to connect (:func:`extra_agents`).

OpenProgram's own Claude Code chats run on an OpenProgram model through a
settings file of their own handed to ``claude --settings``
(:func:`launch_settings`), so the user's settings.json is never written for
them.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Callable, Optional

from .base import Agent, Field, Wiring, Write

TIER_VARS = {
    "opus": "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "sonnet": "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "haiku": "ANTHROPIC_DEFAULT_HAIKU_MODEL",
}
TIERS = tuple(TIER_VARS)
SUBAGENT_VAR = "CLAUDE_CODE_SUBAGENT_MODEL"
CLOUD_SWITCHES = ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")
#: What a launch settings file sets to "", each over the user's settings.json
#: and the shell (magpie ``claudeLaunchBlanks``): an API key would go to the
#: gateway beside its own key, and a cloud switch takes Claude Code past it.
LAUNCH_BLANKS = ("ANTHROPIC_API_KEY", *CLOUD_SWITCHES)


def home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")).expanduser()


def config_path() -> Path:
    return home() / "settings.json"


def claude_code_env(model: str, tiers: dict[str, str] | None, *, base_url: str, token: str) -> dict[str, str]:
    """The Claude Code environment that sends its model calls to the gateway.

    ``tiers`` may also name ``subagent``: the variable is set only then
    (magpie writes it only for a model the user picked for subagents)."""
    from .engine import strip_ref
    picked = strip_ref(model)
    env = {"ANTHROPIC_BASE_URL": base_url, "ANTHROPIC_AUTH_TOKEN": token}
    for tier, name in TIER_VARS.items():
        env[name] = strip_ref((tiers or {}).get(tier) or "") or picked
    env["ANTHROPIC_SMALL_FAST_MODEL"] = env["ANTHROPIC_DEFAULT_HAIKU_MODEL"]
    subagent = strip_ref((tiers or {}).get("subagent") or "")
    if subagent:
        env[SUBAGENT_VAR] = subagent
    return env


def _wire_at(path: Callable[[], Path]):
    def wire(at: Wiring) -> list[Write]:
        tiers = {key: at.values.get(key, "") for key in (*TIERS, "subagent")}
        env = claude_code_env(at.model, tiers, base_url=at.base("anthropic"), token=at.key)
        sets: dict[str, Any] = {f"env.{name}": value for name, value in env.items()}
        sets["model"] = at.model
        deletes = [f"env.{name}" for name in CLOUD_SWITCHES]
        if SUBAGENT_VAR not in env:
            # following the main model: the variable goes (an older value
            # comes back on disconnect), as magpie's Subagents field "Same
            # as main model" does
            deletes.append(f"env.{SUBAGENT_VAR}")
        return [Write(path(), "json", sets=sets, deletes=deletes)]
    return wire


def _notice() -> str:
    from .running import any_running
    if any_running(r"(^|/)claude( |$)"):
        return "an open Claude Code session keeps the model it started with — restart it to use this."
    return ""


def _held() -> list[Path]:
    return [home() / "local" / "claude", Path("/Applications/Claude.app")]


def _fields() -> list[Field]:
    return [Field("model", "Model"),
            *(Field(tier, tier.capitalize(), quiet=True, follows="model") for tier in TIERS),
            Field("subagent", "Subagents", quiet=True, follows="model")]


def agent() -> Agent:
    return Agent(
        id="claude_code", name="Claude Code", icon="anthropic", aliases=("claude", "cc", "claude-code"),
        kind="cli", protocol="anthropic", bin="claude", dir=home, path=config_path,
        fields=_fields(), ua=("claude-cli", "claude-code"), harness=True, wire=_wire_at(config_path),
        notice=_notice, held_at=_held,
    )


# More than one config folder (magpie claude_dirs.go)

def dir_name(base: str) -> str:
    """The part of a folder's name after ``.claude`` as a row's name:
    ``.claude-work`` is ``work``, ``.claude2`` is ``2``; ``""`` for a name
    that is no extra Claude Code folder (``~/.claude.json`` and its backups,
    ``.claude`` itself, magpie's launch file's)."""
    lowered = base.lower()
    if not lowered.startswith(".claude"):
        return ""
    rest = lowered[len(".claude"):]
    if not rest or rest.startswith("."):
        return ""
    rest = rest.strip("-_")
    if not rest or rest == "launch":
        return ""
    return re.sub(r"[^a-z0-9_-]", "-", rest)


def dir_is_claude(folder: Path) -> bool:
    """``folder`` holds what Claude Code itself keeps in a config folder: its
    settings.json or its sign-in file, or its projects. Another tool's
    ``.claude-…`` folder (a router's, a launcher's) has none."""
    for name in ("settings.json", ".credentials.json"):
        if (folder / name).is_file():
            return True
    return (folder / "projects").is_dir()


def extra_agents(user_home: Optional[Path] = None) -> list[Agent]:
    """The Claude Code config folders beside the main row's, each an agent of
    its own (magpie ``claudeDirAgents``); none while a folder has no Claude
    Code's files: a leftover is no agent."""
    user_home = Path(user_home) if user_home is not None else Path.home()
    try:
        entries = sorted(os.scandir(user_home), key=lambda e: e.name)
    except OSError:
        return []
    main = home()
    by_name: dict[str, Path] = {}
    for entry in entries:
        name = dir_name(entry.name)
        folder = user_home / entry.name
        if not name or not entry.is_dir(follow_symlinks=False) or folder == main or not dir_is_claude(folder):
            continue
        by_name.setdefault(name, folder)
    # with CLAUDE_CONFIG_DIR elsewhere, the default folder is one of the others
    default = user_home / ".claude"
    if default != main and default.is_dir() and dir_is_claude(default):
        by_name["default"] = default
    found = []
    for name in sorted(by_name):
        folder = by_name[name]
        settings = (lambda folder=folder: folder / "settings.json")
        found.append(Agent(
            id=f"claude_code#{name}", name=f"Claude Code · {name}", icon="anthropic", kind="cli",
            protocol="anthropic", dir=(lambda folder=folder: folder), path=settings,
            fields=_fields(), wire=_wire_at(settings), notice=_notice,
            # the claude_code row keeps the command, the names and the requests
            detect=(lambda folder=folder: dir_is_claude(folder)),
        ))
    return found


# OpenProgram's own Claude Code chats on the gateway (magpie claude_launch.go)

#: The header each chat's launch file has Claude Code send, naming the file:
#: the gateway reads that chat's subagent model from it, never the user's
#: settings.json (:func:`openprogram.models.gateway.claude_subagent.subagent_model`).
LAUNCH_HEADER = "x-openprogram-launch"
#: A launch file left by a chat not run for this long is removed by the next write.
LAUNCH_KEEP_SECONDS = 14 * 24 * 3600
_LAUNCH_ID = re.compile(r"[0-9a-f]{24}")


def launch_id(chat: str) -> str:
    """The name of a chat's launch file: a digest of the chat's key (its session and branch)."""
    import hashlib
    return hashlib.sha256(chat.encode("utf-8")).hexdigest()[:24]


def launch_path(launch: str) -> Path:
    """The settings file OpenProgram hands ``claude --settings`` for one chat
    on one of its models. magpie keeps one file; OpenProgram runs many chats
    at once, each on a model and tiers of its own, so each has its own."""
    from ..keys import gateway_dir
    if not _LAUNCH_ID.fullmatch(launch or ""):
        raise ValueError(f"not a launch id: {launch!r}")
    return gateway_dir() / "claude-launch" / launch / "settings.json"


def _prune_launches(keep: Path) -> None:
    import shutil
    import time
    cutoff = time.time() - LAUNCH_KEEP_SECONDS
    try:
        folders = list(keep.parent.parent.iterdir())
    except OSError:
        return
    for folder in folders:
        if folder == keep.parent or not _LAUNCH_ID.fullmatch(folder.name):
            continue
        try:
            if (folder / "settings.json").stat().st_mtime < cutoff:
                shutil.rmtree(folder, ignore_errors=True)
        except OSError:
            continue


def launch_settings(model_ref: str, tiers: Optional[dict[str, str]], chat: str) -> Path:
    """Write the launch file of the chat ``chat`` for ``model_ref`` and answer its path.

    Claude Code puts a ``--settings`` file over the user's settings.json, its
    env key by key over the user's env and the shell's, so the user's own
    file is never written and a plain ``claude`` stays on the user's own
    account. What could still take a request past the gateway, an
    ``ANTHROPIC_API_KEY`` or a cloud switch from the user's file or the
    shell, is blanked in the file (:data:`LAUNCH_BLANKS`). Claude Code sends
    :data:`LAUNCH_HEADER` with each request, so the gateway knows the chat's
    file. The file holds a key that names Claude Code, never the gateway key,
    and only its owner may read it."""
    import json

    from ..address import anthropic_base_url
    from ..edit import write_atomic
    from ..keys import token_for
    from .engine import strip_ref
    if "/" not in strip_ref(model_ref):
        raise ValueError(f"not an OpenProgram model ref: {model_ref!r}")
    launch = launch_id(chat)
    env = claude_code_env(model_ref, tiers, base_url=anthropic_base_url(), token=token_for("claude_code"))
    env.update({name: "" for name in LAUNCH_BLANKS})
    env["ANTHROPIC_CUSTOM_HEADERS"] = f"{LAUNCH_HEADER}: {launch}"
    path = launch_path(launch)
    text = json.dumps({"env": env, "model": strip_ref(model_ref)}, indent=2) + "\n"
    write_atomic(path, text, keep_mode=False)
    _prune_launches(path)
    return path


# What a launch session's /model leaves in the user's file (magpie
# ClaudeLaunchBefore / ClaudeLaunchAfter)

def _user_doc() -> Optional[dict]:
    import json
    try:
        doc = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def launch_before() -> tuple[Any, bool]:
    """What the user's settings.json says of its model before a launch session
    (magpie ``ClaudeLaunchBefore``), for :func:`launch_after`."""
    doc = _user_doc() or {}
    return doc.get("model"), "model" in doc


def _rewrote(was: tuple[Any, bool], now: Any) -> bool:
    """``now`` is Claude Code's own rewrite of the user's model at a launch
    start, not a pick: with the launch file's opus tier on a 1M model,
    Claude Code turns a user's ``"opus"`` into ``"opus[1m]"`` in their
    settings.json once (its opus-to-1M migration). Plain claude, on the
    user's own account, would then ask for 1M context it may not have."""
    model, has = was
    return (has and isinstance(model, str) and not model.endswith("[1m]")
            and now == model + "[1m]")


def _served(model: Any) -> bool:
    from ..conversion import enabled_model_refs
    from .engine import strip_ref
    if not isinstance(model, str):
        return False
    model = strip_ref(model.removesuffix("[1m]"))
    if "/" not in model:
        return False
    try:
        return model in {row["id"] for row in enabled_model_refs()}
    except Exception:  # noqa: BLE001 - no catalog: nothing of OpenProgram's to tell apart
        return False


def launch_after(was: tuple[Any, bool]) -> None:
    """Undo what a launch session left in the user's settings.json (magpie
    ``ClaudeLaunchAfter``). Claude Code saves a ``/model`` pick to the user's
    settings.json, never to a ``--settings`` file, so a pick of one of
    OpenProgram's models would leave a plain ``claude`` asking Anthropic for
    a model only the gateway serves, and its opus-to-1M rewrite would ask for
    a context the user's account may not have. The user's model comes back
    then, while the gateway isn't wired into their file; a Claude model of
    their own (``sonnet``) is one plain claude can run, and stays. The chat's
    model is OpenProgram's setting, so a pick of the gateway's goes nowhere
    else."""
    import json

    from ..edit import write_atomic
    from .lifecycle import on_gateway
    doc = _user_doc()
    if doc is None:
        return
    now, has = doc.get("model"), "model" in doc
    if (now, has) == tuple(was):
        return
    env = doc.get("env") if isinstance(doc.get("env"), dict) else {}
    if on_gateway(str(env.get("ANTHROPIC_BASE_URL") or "")):
        return
    if not _rewrote(was, now) and not _served(now):
        return
    model, had = was
    if had:
        doc["model"] = model
    else:
        doc.pop("model", None)
    write_atomic(config_path(), json.dumps(doc, indent=2) + "\n")
