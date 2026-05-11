"""Centralized filesystem paths for OpenProgram state.

Single source of truth so ``--profile <name>`` / ``OPENPROGRAM_PROFILE``
reroute every config / sessions / logs read to an isolated dir. Before
this module existed, ~8 sites hard-coded ``~/.agentic/...`` which meant
profile isolation was impossible without touching each one.

Profile resolution order (checked lazily on each call, so CLI argparse
can set the env var before any getter runs):

    1. ``OPENPROGRAM_PROFILE`` env var — wins.
    2. Default profile = writes to ``~/.agentic/`` for back-compat with
       every existing install.

Usage:

    from openprogram.paths import get_config_path, get_sessions_dir
    cfg = json.loads(get_config_path().read_text())

Do NOT cache the returned paths in module-level constants — tests
switch profiles via env var and the ``--profile`` flag changes the
active profile after import.
"""
from __future__ import annotations

import os
from pathlib import Path

_ENV_PROFILE = "OPENPROGRAM_PROFILE"

# Legacy state dir. Everything lands here for the default (unnamed)
# profile so existing users see zero path changes.
_LEGACY_BASENAME = ".agentic"


def get_active_profile() -> str | None:
    """Return the current profile name, or None for default.

    Only reads env for now; the config.json ``profile`` field is used
    as a display hint but doesn't reroute paths (you'd need the env
    var to influence imports consistently).
    """
    name = os.environ.get(_ENV_PROFILE)
    return name.strip() or None if name else None


def set_active_profile(name: str | None) -> None:
    """Pin the active profile via env var so subsequent getters see it.

    CLI entry points call this as soon as argparse resolves ``--profile``
    so every later import sees the right dir.
    """
    if not name:
        os.environ.pop(_ENV_PROFILE, None)
    else:
        os.environ[_ENV_PROFILE] = name


def get_state_dir() -> Path:
    """Root dir for all per-profile state (config + sessions + logs + ...)."""
    profile = get_active_profile()
    if profile:
        return Path.home() / f"{_LEGACY_BASENAME}-{profile}"
    return Path.home() / _LEGACY_BASENAME


def get_config_path() -> Path:
    return get_state_dir() / "config.json"


def get_sessions_dir() -> Path:
    return get_state_dir() / "sessions"


def get_logs_dir() -> Path:
    return get_state_dir() / "logs"


def get_memory_dir() -> Path:
    return get_state_dir() / "memory"


def ensure_state_dir() -> Path:
    d = get_state_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_default_workdir() -> str:
    """Project / working-directory the agent should treat as "where the
    user is right now".

    Resolution order:
      1. ``OPENPROGRAM_WORKDIR`` env var.
      2. ``default_workdir`` key in ``~/.agentic/config.json``.
      3. ``os.getcwd()`` — the worker process's launch cwd.

    Why a separate concept from ``os.getcwd()``: the worker is a
    long-running daemon usually started from ``$HOME``. If the agent's
    system prompt simply tells the LLM "cwd is /Users/<user>" the
    model will happily run ``glob '**/*.py'`` over the entire home
    directory, which takes minutes and yields garbage. Claude Code
    works because its cwd is whichever terminal directory the user
    launched it from; our worker can't replicate that automatically,
    so we expose a config switch the user (or per-session UI) can
    point at the real project root.
    """
    import json
    env_v = os.environ.get("OPENPROGRAM_WORKDIR")
    if env_v and env_v.strip() and os.path.isdir(env_v):
        return env_v
    try:
        cfg = json.loads(get_config_path().read_text())
        wd = cfg.get("default_workdir")
        if isinstance(wd, str) and wd.strip() and os.path.isdir(wd):
            return wd
    except Exception:
        pass
    return os.getcwd()
