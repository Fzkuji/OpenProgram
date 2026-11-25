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

    from openprogram.state.paths import get_config_path, get_sessions_dir
    cfg = json.loads(get_config_path().read_text())

Do NOT cache the returned paths in module-level constants — tests
switch profiles via env var and the ``--profile`` flag changes the
active profile after import.
"""

from __future__ import annotations

import logging
import os
import stat
import sys
from pathlib import Path
from typing import Optional

_log = logging.getLogger(__name__)

_ENV_PROFILE = "OPENPROGRAM_PROFILE"
# An exported application (interfaces/standalone) keeps all of its state
# in one folder of its own, named by its launcher; it wins over a profile.
_ENV_STATE_DIR = "OPENPROGRAM_STATE_DIR"

# pytest sets this in its own process for the length of each test, and every
# child that process starts inherits it.
_ENV_UNDER_TEST = "PYTEST_CURRENT_TEST"
# A run that means to use the person's own state says so.
_ENV_TEST_REAL_HOME = "OPENPROGRAM_TEST_REAL_HOME"


class RealStateUnderTest(PermissionError):
    """A test resolved the state folder of the person's own account."""

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
    explicit = os.environ.get(_ENV_STATE_DIR)
    if explicit:
        canonical = Path(explicit).expanduser()
        _refuse_real_state_under_test(canonical)
        _restrict_profile_root_once(canonical)
        return canonical
    profile = get_active_profile()
    if profile:
        canonical = Path.home() / f"{_CANONICAL_BASENAME}-{profile}"
        legacy = Path.home() / f"{_LEGACY_BASENAME}-{profile}"
    else:
        canonical = Path.home() / _CANONICAL_BASENAME
        legacy = Path.home() / _LEGACY_BASENAME
    _refuse_real_state_under_test(canonical)
    _maybe_migrate_legacy_state(legacy, canonical)
    # Every path in this module funnels through here, so this is the one
    # place that can promise the profile root is owner-only however it
    # came to exist — including the directories created before the
    # promise existed. Guarded by a process-level flag so the common
    # case is a single boolean test, not a stat per path lookup.
    _restrict_profile_root_once(canonical)
    return canonical


_account_home_cache: list[Optional[str]] = []


def _account_home() -> Optional[str]:
    """The home folder of the account, whatever ``HOME`` says now."""
    if _account_home_cache:
        return _account_home_cache[0]
    home: Optional[str] = None
    try:
        if sys.platform == "win32":
            import ctypes

            buffer = ctypes.create_unicode_buffer(260)
            # CSIDL_PROFILE; read from the account, not from USERPROFILE.
            if ctypes.windll.shell32.SHGetFolderPathW(None, 40, None, 0, buffer) == 0:
                home = buffer.value or None
        else:
            import pwd

            home = pwd.getpwuid(os.getuid()).pw_dir or None
    except Exception:
        home = None
    if home:
        home = os.path.realpath(home)
        # Only an answer is kept. A lookup that failed once (the account
        # database unreadable for a moment) is asked again; kept, it left
        # the process without its account home for good, and with it the
        # check that keeps a test out of the real state folder.
        _account_home_cache.append(home)
    return home


def _refuse_real_state_under_test(canonical: Path) -> None:
    """Refuse the account's own state folder while a test is running.

    The suite's ``tests/conftest.py`` points ``HOME`` at a throwaway folder,
    but a test file run without it (a copy in a scratch folder, ``pytest
    --noconftest``, a child process handed the real ``HOME``) would resolve
    ``~/.openprogram`` itself and register its fake projects and
    conversations there, or find and use the instance running from it. Every
    state path and the Worker's endpoint files come through
    ``get_state_dir``, so refusing here covers all of them.
    """
    if not os.environ.get(_ENV_UNDER_TEST) or os.environ.get(_ENV_TEST_REAL_HOME) == "1":
        return
    home = _account_home()
    if home is None:
        return
    if os.path.realpath(str(canonical.parent)) != home:
        return
    raise RealStateUnderTest(
        f"a test resolved the real state folder {canonical}: run it from the repository's "
        "tests/ tree, where conftest.py gives the run its own home, or point HOME at a "
        f"temporary folder ({_ENV_TEST_REAL_HOME}=1 allows the real one)")


_root_mode_checked: set[Path] = set()


def _restrict_profile_root_once(canonical: Path) -> None:
    if canonical in _root_mode_checked:
        return
    _root_mode_checked.add(canonical)
    if canonical.is_dir():
        _restrict_to_owner(canonical, 0o700)


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
        _log.debug("~/.agentic migration failed", exc_info=True)


def get_config_path() -> Path:
    return get_state_dir() / "config.json"


def program_sources_path() -> Path:
    """The owner's record of installed Program directories."""
    return get_state_dir() / "program-sources.json"


def get_sessions_dir() -> Path:
    """Root of the conversation store, ``<state>/projects``.

    Conversations sit in ``<project-id>/chats/`` below it; see
    :mod:`openprogram.state.store.project.layout`.
    """
    from openprogram.state.store.project.layout import projects_root
    return projects_root(get_state_dir())


def get_logs_dir() -> Path:
    return get_state_dir() / "logs"


def get_recordings_dir() -> Path:
    """Owner-private directory for managed provider recordings."""
    directory = get_state_dir() / "recordings"
    if directory.is_symlink():
        raise PermissionError(
            f"recordings directory must not be a symlink: {directory}"
        )
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    _restrict_to_owner(directory, 0o700)
    if sys.platform != "win32" and stat.S_IMODE(directory.stat().st_mode) != 0o700:
        raise PermissionError(f"recordings directory must have mode 0700: {directory}")
    return directory


def get_memory_dir() -> Path:
    """The personal memory: the home project's memory workspace."""
    from openprogram.state.store.project.layout import DEFAULT_PROJECT_ID, memory_dir
    return memory_dir(DEFAULT_PROJECT_ID, get_state_dir())


def get_usage_db_path() -> Path:
    """SQLite ledger of LLM token usage (see metering/ledger.py).

    Profile-aware like every other state path, so ``--profile`` keeps a
    separate usage history. Lives at the state-dir root rather than under
    a subdir — it's a single global file, not a per-entity tree.
    """
    return get_state_dir() / "usage.db"


def get_run_state_path() -> Path:
    """Where a profile's run state is rooted; its rows live in session logs."""
    return get_state_dir() / "run-state"


def get_user_errors_path() -> Path:
    """Bounded operational error records for the active profile."""
    return get_state_dir() / "user_errors.jsonl"


def ensure_state_dir() -> Path:
    """Create the profile root and keep it readable only by its owner.

    Everything a session ever produced lives under here — transcripts,
    memory, credentials — and the default 0755 a ``mkdir`` inherits from
    the umask hands all of it to every other account on the machine. On a
    shared host that is the whole conversation history readable by anyone
    in the ``staff`` group.

    Applied on every call rather than only at creation, because the
    directory this protects mostly already exists: an installation that
    predates this line is exactly the one that needs the mode fixed, and
    it would never be created again. ``chmod`` on the resolved directory
    only, so a symlink planted at the profile path cannot redirect the
    mode change onto something else.
    """
    d = get_state_dir()
    d.mkdir(parents=True, exist_ok=True)
    _restrict_to_owner(d, 0o700)
    return d


def _restrict_to_owner(path: Path, mode: int) -> None:
    """Best-effort ``chmod``, skipping symlinks and foreign filesystems.

    Never raises: a state directory whose mode cannot be tightened —
    a mounted share, a read-only parent, Windows — is still a usable
    state directory, and refusing to start over it would trade a
    privacy weakness for an outage.
    """
    try:
        if path.is_symlink():
            return
        if (path.stat().st_mode & 0o777) != mode:
            os.chmod(path, mode)
    except OSError:
        pass


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
    return configured_workdir() or os.getcwd()


def configured_workdir() -> Optional[str]:
    """The working folder the person configured (``OPENPROGRAM_WORKDIR``,
    then ``default_workdir`` in the config), or ``None``.

    Unlike :func:`get_default_workdir` this never answers with the
    process's own working directory, so code that acts on a repository —
    creating worktrees and branches in it — uses it: a worker started from
    a checkout (or a test run from one) must not treat that checkout as
    the person's project, and an installed app has no checkout at all."""
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
        # Missing or malformed config: nothing configured.
        _log.debug("default_workdir from config unreadable", exc_info=True)
    return None
