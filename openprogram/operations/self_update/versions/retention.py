"""What stays on disk under ``<root>``, and removing the rest.

Kept: ``current``, ``last_good``, ``previous_good``, the 5 most recent committed
versions, any version an unfinished or waiting update still needs, the version
an open App has loaded, any version whose start record moved in the last 10
minutes, and the frontend zips a kept version names. A candidate that was never committed goes after 7 days. Nothing is
removed while an update holds the slot.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import subprocess
import time

from . import pointer as pointers
from . import store
from .layout import Layout, valid_id
from .removal import remove_tree

_log = logging.getLogger(__name__)

KEEP_COMMITTED = 5
FAILED_CANDIDATE_SECONDS = 7 * 86400
ABORTED_WORK_SECONDS = 14 * 86400
RECENT_ATTEMPT_SECONDS = 600
_TERMINAL = frozenset({"succeeded", "aborted", "rolled_back", "needs_manual_recovery"})


@dataclass
class Plan:
    held: bool = False
    versions: list[str] = field(default_factory=list)
    bases: list[str] = field(default_factory=list)
    frontends: list[str] = field(default_factory=list)
    work: list[str] = field(default_factory=list)
    # What was decided and is still on disk after the pass, as paths under ``<root>``.
    remaining: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Updates:
    """What ``<state>/self-updates`` says about retention."""

    slot_held: bool
    pinned: frozenset[str]
    work_done: frozenset[str]
    work_aborted: dict[str, float]


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _named_by(request: dict) -> set[str]:
    return {name for name in (request.get("controller_version"), request.get("candidate_version"))
            if isinstance(name, str)}


def _queued(root: Path) -> set[str]:
    """Versions that requests waiting in line will need."""
    entries = (_read_json(root / "queue.json") or {}).get("entries")
    named: set[str] = set()
    for entry in entries if isinstance(entries, list) else ():
        request = entry.get("request") if isinstance(entry, dict) else None
        if isinstance(request, dict):
            named |= _named_by(request)
    return named


def _read_updates(state_dir: Path) -> _Updates:
    root = state_dir / "self-updates"
    done: set[str] = set()
    aborted: dict[str, float] = {}
    slot = False
    if not root.is_dir():
        return _Updates(False, frozenset(), frozenset(), {})
    pinned = _queued(root)
    for entry in root.iterdir():
        request = _read_json(entry / "request.json") if entry.is_dir() else None
        state = _read_json(entry / "state.json") if request is not None else None
        if request is None or state is None:
            continue
        phase = state.get("phase")
        task = request.get("task")
        if phase not in _TERMINAL:
            slot = True
            pinned |= _named_by(request)
            continue
        if not isinstance(task, str) or not valid_id(task):
            continue
        if phase == "succeeded":
            done.add(task)
        else:
            aborted[task] = max(aborted.get(task, 0.0), (entry / "state.json").stat().st_mtime)
    return _Updates(slot, frozenset(pinned), frozenset(done), aborted)


def _open_shell_version(layout: Layout) -> str | None:
    """The version whose main process an open App has loaded: it still reads files from there."""
    from openprogram.host._platform.processes import process_alive

    shell = _read_json(layout.root / "shell.json") or {}
    name = shell.get("versionId")
    return name if isinstance(name, str) and isinstance(shell.get("pid"), int) and process_alive(shell["pid"]) else None


def plan(layout: Layout, state_dir: Path, *, now: float | None = None) -> Plan:
    """Decide what would go. Touches nothing."""
    moment = time.time() if now is None else now
    updates = _read_updates(Path(state_dir))
    if updates.slot_held:
        return Plan(held=True)
    try:
        current = pointers.read(layout)
    except pointers.PointerError:
        return Plan(held=True)  # Nothing can be judged without a pointer.
    manifests = store.list_versions(layout)
    committed = store.committed_versions(layout)
    if committed is None:
        return Plan(held=True)  # Unknown history: deleting could lose a good version.

    shell = _open_shell_version(layout)
    keep = {current.version, current.last_good, current.previous_good, *updates.pinned, *([shell] if shell else [])} - {""}
    keep.update(item["id"] for item in [m for m in manifests if m["id"] in committed][:KEEP_COMMITTED])
    for manifest in manifests:
        try:
            touched = layout.attempts(manifest["id"]).stat().st_mtime
        except OSError:
            continue
        if moment - touched < RECENT_ATTEMPT_SECONDS:
            keep.add(manifest["id"])

    result = Plan()
    for manifest in manifests:
        name = manifest["id"]
        if name in keep:
            continue
        if name not in committed and moment - layout.version(name).stat().st_mtime < FAILED_CANDIDATE_SECONDS:
            keep.add(name)
            continue
        result.versions.append(name)

    kept = [manifest for manifest in manifests if manifest["id"] in keep]
    used_bases = {manifest["base"] for manifest in kept} | {
        current.base, current.last_good_base, current.previous_good_base,
    }
    if layout.bases.is_dir():
        result.bases = sorted(
            entry.name for entry in layout.bases.iterdir()
            if not entry.name.startswith(".") and entry.is_dir() and entry.name not in used_bases
        )
    used_frontends = {f"{manifest.get('frontend_sha')}.zip" for manifest in kept}
    if layout.frontends.is_dir():
        result.frontends = sorted(
            entry.name for entry in layout.frontends.iterdir()
            if entry.suffix == ".zip" and entry.name not in used_frontends
        )
    if layout.work.is_dir():
        for entry in sorted(layout.work.iterdir()):
            aborted_at = updates.work_aborted.get(entry.name)
            if entry.name in updates.work_done or (
                aborted_at is not None and moment - aborted_at >= ABORTED_WORK_SECONDS
            ):
                result.work.append(entry.name)
    return result


def _discard(layout: Layout, path: Path) -> bool:
    """Rename into ``trash``, so a half-deleted tree is never read as a version; the sweep deletes it."""
    layout.trash.mkdir(parents=True, exist_ok=True)
    target = layout.trash / f"{path.name}.{os.getpid()}.{time.time_ns()}"
    try:
        os.replace(path, target)
    except FileNotFoundError:
        return True
    except OSError:
        return False  # On Windows a running process blocks this; the next pass retries.
    return True


def _sweep_trash(layout: Layout) -> list[str]:
    """Delete what is in ``trash``, this pass's and any earlier one's; return what would not go."""
    left: list[str] = []
    if layout.trash.is_dir():
        for entry in sorted(layout.trash.iterdir()):
            left += remove_tree(entry)
    return left


def _under_root(layout: Layout, paths: list[str]) -> list[str]:
    named = []
    for path in paths:
        try:
            named.append(Path(path).relative_to(layout.root).as_posix())
        except ValueError:
            named.append(Path(path).as_posix())
    return named


def _remove_work(layout: Layout, path: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(layout.repo), "worktree", "remove", "--force", str(path)],
            capture_output=True, timeout=120,
        )
        removed = result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        removed = False
    if not removed:
        removed = _discard(layout, path)
        subprocess.run(["git", "-C", str(layout.repo), "worktree", "prune"], capture_output=True, timeout=60)
    return removed


def run(layout: Layout, state_dir: Path, *, now: float | None = None) -> Plan:
    """Remove what :func:`plan` decides, and finish any interrupted deletion.

    ``remaining`` on the result names what is still on disk: a tree a running
    process holds, or a file that stayed read-only or locked. The next pass
    tries those again.
    """
    decided = plan(layout, state_dir, now=now)
    if decided.held:
        return decided
    blocked: list[str] = []
    for name in decided.versions:
        if not _discard(layout, layout.version(name)):
            blocked.append(str(layout.version(name)))
    for name in decided.bases:
        if not _discard(layout, layout.base(name)):
            blocked.append(str(layout.base(name)))
    for name in decided.frontends:
        try:
            (layout.frontends / name).unlink(missing_ok=True)
        except OSError:
            blocked.append(str(layout.frontends / name))
    for name in decided.work:
        if not _remove_work(layout, layout.work / name):
            blocked.append(str(layout.work / name))
    decided.remaining = _under_root(layout, [*blocked, *_sweep_trash(layout)])
    if decided.remaining:
        _log.warning("retention left %d entries for the next pass: %s", len(decided.remaining),
                     ", ".join(decided.remaining[:5]))
    from ..trial.instance import prune_trials

    prune_trials(layout, now=now)
    return decided
