"""What the Versions page and its notices read from the version store.

Everything here reads files the launcher, the capture script and the controller
already write, so it holds for a version the worker did not start itself:
the pointer, ``version.json``, ``unapplied/<stamp>/manifest.json`` and the
guard's lines in ``recovery.log``.

Two small files are written here, both under ``<root>``. ``version-numbers.json``
gives each version the number the owner sees, once, so a number never changes
and is never reused when older versions are deleted. ``notices-seen.json``
remembers which notices were shown, so each is shown once.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any

from . import layout as paths
from . import pointer as pointers
from . import store as sealed
from .layout import Layout
from openprogram.host._platform import filesystem

NUMBERS_FILE = "version-numbers.json"
SEEN_FILE = "notices-seen.json"
UNAPPLIED_DIRECTORY = "unapplied"
NOTICE_WINDOW_SECONDS = 7 * 86400
_SEEN_LIMIT = 200
_LOG_TAIL_BYTES = 256 * 1024
_GUARD_LINE = re.compile(r"^(\S+) launcher: moved from (\S+) to (\S+): (.*)$")
# A move during probation belongs to an update, which reports it in its own conversation.
_UPDATE_OWNED = "probation"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        filesystem.replace_file(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _timestamp(text: Any, pattern: str) -> float | None:
    try:
        return datetime.strptime(str(text), pattern).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def numbers(layout: Layout, *, manifests: list[dict] | None = None) -> dict[str, int]:
    """Each sealed version's number, handing out new ones oldest first."""
    found = sealed.list_versions(layout) if manifests is None else manifests
    recorded = _read_json(layout.root / NUMBERS_FILE)
    known = dict(recorded.get("numbers") or {}) if isinstance(recorded, dict) else {}
    known = {key: value for key, value in known.items() if type(value) is int and value > 0}
    following = recorded.get("next") if isinstance(recorded, dict) else None
    following = max(following if type(following) is int else 1, max(known.values(), default=0) + 1)
    changed = False
    for manifest in sorted(found, key=lambda item: str(item["id"])):
        identifier = str(manifest["id"])
        if identifier not in known:
            # The build stamps a number; the shell's list shows that same one.
            stamped = manifest.get("number")
            if type(stamped) is int and stamped > 0 and stamped not in known.values():
                known[identifier], following, changed = stamped, max(following, stamped + 1), True
            else:
                known[identifier], following, changed = following, following + 1, True
    if changed:
        try:
            _write_json(layout.root / NUMBERS_FILE, {"next": following, "numbers": known})
        except OSError:
            pass  # The numbers still hold for this read; the next read writes them.
    return known


def version_label(layout: Layout, version_id: str) -> str | None:
    number = numbers(layout).get(version_id)
    return None if number is None else f"Version {number}"


def current_label(layout: Layout) -> str | None:
    try:
        return version_label(layout, pointers.read(layout).version)
    except (pointers.PointerError, OSError):
        return None


def guard_moves(layout: Layout) -> list[dict[str, Any]]:
    """The launcher's returns to a working version, oldest first."""
    try:
        with open(layout.recovery_log, "rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(stream.tell() - _LOG_TAIL_BYTES, 0))
            text = stream.read().decode("utf-8", "replace")
    except OSError:
        return []
    moves = []
    for line in text.splitlines():
        matched = _GUARD_LINE.match(line)
        if matched is None:
            continue
        at = _timestamp(matched[1], "%Y-%m-%dT%H:%M:%SZ")
        if at is not None:
            moves.append({"line": line, "at": at, "from": matched[2], "to": matched[3], "reason": matched[4]})
    return moves


def unapplied(layout: Layout) -> list[dict[str, Any]]:
    """Direct edits set aside by the capture script, newest first."""
    directory = layout.root / UNAPPLIED_DIRECTORY
    if not directory.is_dir():
        return []
    found = []
    for entry in directory.iterdir():
        manifest = _read_json(entry / "manifest.json")
        if not isinstance(manifest, dict) or not paths.valid_id(entry.name):
            continue
        files = sum(len(manifest.get(key) or ()) for key in ("changed", "added", "removed"))
        found.append({
            "stamp": entry.name,
            "files": files,
            "found_at": _timestamp(entry.name, "%Y%m%dT%H%M%S%fZ"),
            "path": str(entry),
            "version": manifest.get("version"),
        })
    return sorted(found, key=lambda item: item["stamp"], reverse=True)


def _kept(layout: Layout, manifests: list[dict], pointer: pointers.Pointer) -> list[dict]:
    """Versions that ran as a good version; a failed candidate is not one to go back to."""
    committed = sealed.committed_versions(layout)
    if committed is None:
        return manifests
    named = {pointer.version, pointer.last_good, pointer.previous_good}
    return [item for item in manifests if item["id"] in committed or item["id"] in named]


def _on_local_line(layout: Layout) -> set[str] | None:
    """Every commit of the code in use; ``None`` when the repository cannot say."""
    try:
        result = subprocess.run(["git", "-C", str(layout.repo), "rev-list", "refs/heads/local"],
                                capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return set(result.stdout.split()) if result.returncode == 0 else None


def read(layout: Layout) -> dict[str, Any]:
    """The whole Versions page."""
    pointer = pointers.read(layout)
    manifests = sealed.list_versions(layout)
    number = numbers(layout, manifests=manifests)
    kept = _kept(layout, manifests, pointer)
    in_use = _on_local_line(layout)
    fallback = (pointer.fallback() or ("",))[0]
    moves = [move for move in guard_moves(layout) if move["to"] == pointer.version]
    latest = moves[-1] if moves and time.time() - moves[-1]["at"] < NOTICE_WINDOW_SECONDS else None
    try:
        since = layout.pointer.stat().st_mtime
    except OSError:
        since = None
    return {
        "managed": True,
        "current": {
            "id": pointer.version,
            "number": number.get(pointer.version),
            "since": since,
            "on_probation": bool(pointer.probation),
            "returned_from": None if latest is None else {
                "id": latest["from"], "number": number.get(latest["from"]), "at": latest["at"],
            },
        },
        "fallback": None if not fallback else {"id": fallback, "number": number.get(fallback)},
        "versions": [
            {
                "id": item["id"],
                "number": number.get(str(item["id"])),
                "summary": str(item.get("summary") or "").split("\n", 1)[0],
                "built_at": _timestamp(item.get("built_at"), "%Y-%m-%dT%H:%M:%SZ"),
                "commit": item.get("commit"),
                "current": item["id"] == pointer.version,
                # After a go-back a later version's commit is no longer in the code to undo.
                "can_undo_change": bool(item.get("commit") and item.get("parent")
                                        and (in_use is None or item["commit"] in in_use)),
            }
            for item in kept
        ],
        "unapplied": [{key: entry[key] for key in ("stamp", "files", "found_at")} for entry in unapplied(layout)],
    }


def find_version(layout: Layout, version_id: str) -> dict | None:
    if not paths.valid_id(version_id):
        return None
    try:
        return sealed.read_manifest(layout, version_id)
    except sealed.SealError:
        return None


def find_unapplied(layout: Layout, stamp: str) -> dict[str, Any] | None:
    return next((entry for entry in unapplied(layout) if entry["stamp"] == stamp), None)


def _seen(layout: Layout) -> list[str]:
    recorded = _read_json(layout.root / SEEN_FILE)
    return [value for value in recorded if isinstance(value, str)] if isinstance(recorded, list) else []


def acknowledge(layout: Layout, identifiers: list[str]) -> None:
    """Remember notices as shown."""
    merged = [*_seen(layout), *(value for value in identifiers if isinstance(value, str))]
    _write_json(layout.root / SEEN_FILE, list(dict.fromkeys(merged))[-_SEEN_LIMIT:])


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _rolled_back(store: Any, number: dict[str, int], since: float) -> list[dict[str, Any]]:
    """Updates that returned to the previous version; each says why in its conversation."""
    from ..control.projection import _exists, _records
    from ..types import UpdatePhase, SelfUpdateError

    try:
        if not _exists(store):
            return []
        with store._locked(read_only=True):
            records = _records(store)
    except (SelfUpdateError, OSError, ValueError):
        return []
    notices = []
    for record in records:
        if record.state.phase is not UpdatePhase.ROLLED_BACK or record.state.updated_at < since:
            continue
        candidate = getattr(record.request, "candidate_version", None) or record.state.detail.get("candidate_version")
        notices.append({
            "id": f"rolled_back:{record.request.update_id}",
            "kind": "returned",
            "at": record.state.updated_at,
            "number": number.get(candidate) if candidate else None,
            # An official release was asked for outside any conversation.
            "session_id": None if getattr(record.request, "kind", "") == "official" else record.request.session_id,
        })
    return notices


def pending_notices(layout: Layout, store: Any, *, now: float | None = None) -> list[dict[str, Any]]:
    """Notices not yet shown, oldest first."""
    moment = time.time() if now is None else now
    since = moment - NOTICE_WINDOW_SECONDS
    number = numbers(layout)
    notices = _rolled_back(store, number, since)
    for move in guard_moves(layout):
        if move["at"] >= since and _UPDATE_OWNED not in move["reason"]:
            notices.append({
                "id": f"returned:{_digest(move['line'])}",
                "kind": "returned",
                "at": move["at"],
                "number": number.get(move["from"]),
                "session_id": None,
            })
    for entry in unapplied(layout):
        if entry["found_at"] is not None and entry["found_at"] >= since:
            notices.append({
                "id": f"direct_edits:{entry['stamp']}",
                "kind": "direct_edits",
                "at": entry["found_at"],
                "files": entry["files"],
            })
    seen = set(_seen(layout))
    return sorted((notice for notice in notices if notice["id"] not in seen), key=lambda notice: notice["at"])


__all__ = [
    "NOTICE_WINDOW_SECONDS", "acknowledge", "current_label", "find_unapplied", "find_version",
    "guard_moves", "numbers", "pending_notices", "read", "unapplied", "version_label",
]
