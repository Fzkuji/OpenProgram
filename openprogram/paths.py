"""Centralized filesystem paths for OpenProgram state.

Single source of truth so ``--profile <name>`` / ``OPENPROGRAM_PROFILE``
reroute every config / sessions / logs read to an isolated dir. Before
this module existed, ~8 sites hard-coded ``~/.agentic/...`` which meant
profile isolation was impossible without touching each one.

Profile resolution order (checked lazily on each call, so CLI argparse
can set the env var before any getter runs):

    1. ``OPENPROGRAM_PROFILE`` env var — wins.
    2. Default profile = writes to ``~/.openprogram/`` (legacy
       ``~/.agentic/`` data is migrated in once; see ``get_state_dir``).

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

# Canonical state dir basename. Everything lives under
# ``~/.openprogram/`` — one hidden directory, matching the project
# name. Earlier builds split state across two dirs (``~/.agentic/``
# for sessions/memory/config and ``~/.openprogram/`` for
# auth/cache/logs/plugins), which was confusing and even let logs
# land in both. We consolidated on ``~/.openprogram/``; existing
# ``~/.agentic/`` data is migrated into it once, automatically, on
# first ``get_state_dir`` call (see ``_migrate_legacy_state``).
_CANONICAL_BASENAME = ".openprogram"

# Old basename, kept only so the one-time migration knows where to
# look. No new writes ever target this.
_LEGACY_BASENAME = ".agentic"

# Process-level guard so the migration probe runs at most once per
# process (the marker file makes it once per machine).
_migration_checked = False


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
    """Root dir for all per-profile state (config + sessions + logs + ...).

    Canonical location is ``~/.openprogram/`` (or
    ``~/.openprogram-<profile>/`` under a named profile). On the first
    call for the default profile we migrate any legacy ``~/.agentic/``
    data into it — once, guarded by a marker file.
    """
    profile = get_active_profile()
    if profile:
        canonical = Path.home() / f"{_CANONICAL_BASENAME}-{profile}"
        legacy = Path.home() / f"{_LEGACY_BASENAME}-{profile}"
    else:
        canonical = Path.home() / _CANONICAL_BASENAME
        legacy = Path.home() / _LEGACY_BASENAME
    _maybe_migrate_legacy_state(legacy, canonical)
    return canonical


def _maybe_migrate_legacy_state(legacy: Path, canonical: Path) -> None:
    """One-time move of ``~/.agentic/*`` → ``~/.openprogram/*``.

    Best-effort and idempotent: a marker file in ``canonical`` records
    that the migration ran, so subsequent calls cost one ``exists()``
    check. Never raises — a half-migrated or unmigrated state is still
    usable (canonical wins; anything left in legacy is just orphaned).

    Move semantics: per-item, skip-if-destination-exists (so we never
    clobber data already under the canonical dir, e.g. ``auth`` /
    ``cache`` / ``logs`` that always lived there). Ephemeral worker
    lock/pid/port files are skipped — they're re-created by the next
    worker and moving a stale one would mislead ``worker status``.
    """
    global _migration_checked
    if _migration_checked:
        return
    _migration_checked = True

    marker = canonical / ".migrated_from_agentic"
    try:
        if marker.exists() or not legacy.exists():
            return
        canonical.mkdir(parents=True, exist_ok=True)
        import shutil
        _skip = {"worker.lock", "worker.pid", "worker.port"}
        for item in legacy.iterdir():
            if item.name in _skip:
                continue
            dest = canonical / item.name
            if dest.exists():
                # Already present under canonical (e.g. a dir that
                # exists in both) — leave the legacy copy orphaned
                # rather than risk a merge/clobber.
                continue
            try:
                shutil.move(str(item), str(dest))
            except (OSError, shutil.Error):
                # Skip this item; keep going. Partial migration is fine.
                continue
        marker.write_text(
            "Migrated from ~/.agentic on first run. Safe to delete the "
            "(now mostly-empty) ~/.agentic dir.\n",
            encoding="utf-8",
        )
    except Exception:
        # Migration is a convenience, never a hard requirement.
        pass


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
      2. ``default_workdir`` key in ``~/.openprogram/config.json``.
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
        cfg = json.loads(get_config_path().read_text(encoding="utf-8"))
        wd = cfg.get("default_workdir")
        if isinstance(wd, str) and wd.strip() and os.path.isdir(wd):
            return wd
    except Exception:
        pass
    return os.getcwd()
