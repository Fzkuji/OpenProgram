"""Top-level orchestration for auto-update.

Dispatches to ``git`` / ``binary`` modules based on detected install
method, throttles upstream queries (default: at most every 6 hours),
and records the staged-update notice that the next ``openprogram``
launch surfaces to the user.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .detect import InstallMethod, detect_install_method, repo_root


CHECK_INTERVAL_SECONDS = 6 * 3600  # 6 hours

# ── kill switch ──────────────────────────────────────────────────────────────


def is_disabled() -> bool:
    """Honor OPENPROGRAM_NO_AUTO_UPDATE=1 (or any non-empty truthy value)."""
    raw = os.environ.get("OPENPROGRAM_NO_AUTO_UPDATE", "")
    return raw not in ("", "0", "false", "False", "no")


# ── state files ──────────────────────────────────────────────────────────────


def _state_dir() -> Path:
    from openprogram.paths import get_state_dir
    d = get_state_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _last_check_path() -> Path:
    return _state_dir() / "update.last_check"


def _staged_path() -> Path:
    return _state_dir() / "update.staged"


def _read_last_check() -> float:
    try:
        return float(_last_check_path().read_text().strip())
    except (OSError, ValueError):
        return 0.0


def _write_last_check(ts: float) -> None:
    try:
        _last_check_path().write_text(f"{ts:.0f}\n")
    except OSError:
        pass


def _write_staged_notice(version: str, summary: str = "") -> None:
    payload = {
        "version": version,
        "summary": summary,
        "applied_at": int(time.time()),
    }
    try:
        _staged_path().write_text(json.dumps(payload))
    except OSError:
        pass


def pop_staged_notice() -> Optional[dict]:
    """Read + delete the staged-update notice. Caller prints a banner."""
    p = _staged_path()
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    try:
        p.unlink()
    except OSError:
        pass
    return data


# ── public API ───────────────────────────────────────────────────────────────


@dataclass
class UpdateInfo:
    method: InstallMethod
    available: bool
    current: str  # short ref (commit SHA / version)
    target: str   # short ref of the version we'd upgrade to
    summary: str  # human-readable one-line description


def check_for_update(*, force: bool = False) -> Optional[UpdateInfo]:
    """Query upstream for a new version. Returns None on errors / no-op installs.

    Throttled by ``CHECK_INTERVAL_SECONDS`` unless ``force=True``.
    """
    if is_disabled() and not force:
        return None
    now = time.time()
    if not force and now - _read_last_check() < CHECK_INTERVAL_SECONDS:
        return None

    method = detect_install_method()

    if method == InstallMethod.GIT_CHECKOUT:
        info = _check_git()
        _write_last_check(now)
        return info

    if method == InstallMethod.BINARY:
        from . import binary as _bin
        manifest = _bin.check_for_update()
        _write_last_check(now)
        if manifest is None:
            return None
        return UpdateInfo(
            method=method,
            available=True,
            current=manifest.get("current", "?"),
            target=manifest.get("target", "?"),
            summary=manifest.get("summary", "binary update available"),
        )

    if method == InstallMethod.PIP_WHEEL:
        # OpenProgram is not on PyPI yet. When it is, this branch will
        # query https://pypi.org/pypi/openprogram/json and compare
        # versions, then run `pip install --upgrade openprogram`.
        return None

    return None


def apply_update(info: UpdateInfo) -> tuple[bool, str]:
    """Apply a previously-detected update. Returns (ok, message)."""
    if info.method == InstallMethod.GIT_CHECKOUT:
        ok, msg = _apply_git()
        if ok:
            _write_staged_notice(info.target, info.summary)
        return ok, msg

    if info.method == InstallMethod.BINARY:
        from . import binary as _bin
        ok, msg = _bin.apply_update({"target": info.target})
        if ok:
            _write_staged_notice(info.target, info.summary)
        return ok, msg

    return False, f"auto-update for {info.method.value} not implemented"


def background_check_and_apply() -> threading.Thread | None:
    """Fire a daemon thread that checks + applies. Returns the thread (or None).

    Failures are silent — auto-update must not interrupt the worker.
    Exceptions are caught and dropped so a transient git/network glitch
    can't crash the worker on startup.
    """
    if is_disabled():
        return None

    def _run() -> None:
        try:
            info = check_for_update()
            if info is None or not info.available:
                return
            apply_update(info)
        except Exception:  # noqa: BLE001
            # Auto-update is best-effort. Don't surface the traceback
            # in worker logs unless explicitly debugging.
            return

    t = threading.Thread(target=_run, daemon=True, name="openprogram-updater")
    t.start()
    return t


# ── git path ─────────────────────────────────────────────────────────────────


def _check_git() -> Optional[UpdateInfo]:
    from . import git as _git
    repo = repo_root()
    if repo is None:
        return None
    head = _git.head_commit(repo) or ""
    head_short = head[:7]
    if not _git.working_tree_clean(repo):
        return UpdateInfo(
            method=InstallMethod.GIT_CHECKOUT,
            available=False,
            current=head_short,
            target=head_short,
            summary="working tree dirty — auto-update skipped",
        )
    upstream = _git.upstream_ref(repo)
    if upstream is None:
        return UpdateInfo(
            method=InstallMethod.GIT_CHECKOUT,
            available=False,
            current=head_short,
            target=head_short,
            summary="no upstream configured",
        )
    if not _git.fetch(repo):
        return UpdateInfo(
            method=InstallMethod.GIT_CHECKOUT,
            available=False,
            current=head_short,
            target=head_short,
            summary="fetch failed",
        )
    counts = _git.commits_behind_ahead(repo, upstream)
    if counts is None:
        return None
    behind, _ahead = counts
    if behind == 0:
        return UpdateInfo(
            method=InstallMethod.GIT_CHECKOUT,
            available=False,
            current=head_short,
            target=head_short,
            summary="up to date",
        )
    rc, target_sha = _git._git(repo, "rev-parse", upstream)
    target_short = (target_sha or "")[:7] if rc == 0 else "?"
    return UpdateInfo(
        method=InstallMethod.GIT_CHECKOUT,
        available=True,
        current=head_short,
        target=target_short,
        summary=f"{behind} commit(s) behind {upstream}",
    )


def _apply_git() -> tuple[bool, str]:
    from . import git as _git
    repo = repo_root()
    if repo is None:
        return False, "no git checkout"
    if not _git.working_tree_clean(repo):
        return False, "working tree dirty — skipping pull"
    return _git.pull(repo)
