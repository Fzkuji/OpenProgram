"""What an update is doing, in plain words, for the ``self_update`` tool's output.

An update shows in a conversation as an ordinary tool call, so its progress is
the tool's result. Every result that names a phase gains ``progress``, one
sentence the owner can read, and ``notes`` when something else applies: messages
held during a switch, updates waiting in line, and on Windows that the trial
runs without system-level isolation.

Phases are compared by value, so a phase this module does not know adds nothing.
Free-form failure text stays out: it belongs to the evidence the update stores.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Mapping

WINDOWS_TRIAL_NOTE = (
    "On Windows this test has no system-level isolation. It runs on a copy of your data, "
    "but the system does not stop it from reaching other files on this computer."
)
WINDOWS_RESULT_NOTE = (
    "On Windows the test had no system-level isolation. It ran on a copy of your data, "
    "but the system did not stop it from reaching other files on this computer."
)
QUEUED = "queued"
_AFTER_TRIAL = frozenset({
    "ready", "quiescing", "activating", "verifying", "succeeded", "rolled_back",
    "needs_manual_recovery",
})
_HELD_TEXT_KEYS = ("user_text", "text", "message", "prompt")
_PREVIEW_CHARACTERS = 120


def _waiting_line(ahead: int | None) -> str:
    if ahead:
        return f"Waiting for another update to finish… {ahead} ahead, starts by itself."
    return "Waiting for another update to finish… Starts by itself."


def _sentence(phase: str, *, candidate: str, previous: str, ahead: int | None, keeps_versions: bool) -> str | None:
    if not keeps_versions and phase == "succeeded":
        return f"Updated to {candidate}."
    if not keeps_versions and phase == "needs_manual_recovery":
        return f"{candidate.capitalize()} didn't work and the version before it failed its checks too. It needs a manual repair."
    return {
        QUEUED: _waiting_line(ahead),
        "preparing": "Getting ready to build the new version… You can keep chatting.",
        "staging": "Building the new version… You can keep chatting.",
        "building": "Building the new version… You can keep chatting.",
        "trial": "Testing the new version on a separate copy… Your work isn't touched.",
        "ready": "The new version passed its tests and is next in line to be switched in.",
        "quiescing": "Waiting for running tasks to reach a safe point before switching.",
        "activating": "Switching to the new version… OpenProgram restarts for about a minute.",
        "verifying": "Checking that the new version works on your real setup.",
        "succeeded": f"Updated to {candidate}. To go back, open Settings, Versions.",
        "aborted": "The update stopped before anything changed. You are still on the same version.",
        "rolled_back": (
            f"{candidate.capitalize()} didn't work, "
            f"back on {previous}. Nothing was lost. The Agent is looking into why it failed and will report here."
        ),
        "needs_manual_recovery": (
            f"{candidate.capitalize()} didn't work and "
            "the version before it failed its checks too. Pick a version in Settings, Versions, "
            "or run `openprogram versions` in a terminal."
        ),
    }.get(phase)


def _windows_note(phase: str, platform: str, reached_trial: bool) -> str | None:
    if platform != "win32":
        return None
    if phase == "trial":
        return WINDOWS_TRIAL_NOTE
    if phase in _AFTER_TRIAL or (phase == "aborted" and reached_trial):
        return WINDOWS_RESULT_NOTE
    return None


def _held_lines(held: list[str]) -> list[str]:
    if not held:
        return []
    count = "1 message" if len(held) == 1 else f"{len(held)} messages"
    return [f"{count} held. Sends when the switch finishes.", *(f"Held: {text}" for text in held if text)]


def describe(
    phase: str,
    *,
    candidate: str = "the new version",
    previous: str = "the previous version",
    ahead: int | None = None,
    held: list[str] | tuple[str, ...] = (),
    platform: str | None = None,
    reached_trial: bool = False,
    keeps_versions: bool = True,
) -> dict[str, Any]:
    """``progress`` and ``notes`` for one phase; empty for an unknown phase.

    ``keeps_versions`` is false on an install with no Settings, Versions page to send the owner to.
    """
    sentence = _sentence(phase, candidate=candidate, previous=previous, ahead=ahead, keeps_versions=keeps_versions)
    if sentence is None:
        return {}
    notes = _held_lines(list(held))
    windows = _windows_note(phase, sys.platform if platform is None else platform, reached_trial)
    if windows:
        notes.append(windows)
    return {"progress": sentence, **({"notes": notes} if notes else {})}


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def queued_count(store_root: Path) -> int:
    """How many requests wait in ``queue.json``."""
    value = _read_json(store_root / "queue.json")
    if isinstance(value, Mapping):
        value = next((value[key] for key in ("entries", "queue", "items") if isinstance(value.get(key), list)), None)
    return len(value) if isinstance(value, list) else 0


def _held_text(entry: Any) -> str:
    if not isinstance(entry, Mapping):
        return ""
    nested = entry.get("request")
    for source in (entry, nested if isinstance(nested, Mapping) else {}):
        for key in _HELD_TEXT_KEYS:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                line = " ".join(value.split())
                return line if len(line) <= _PREVIEW_CHARACTERS else f"{line[:_PREVIEW_CHARACTERS - 1]}…"
    return ""


def held_messages(store_root: Path) -> list[str]:
    """One preview per held arrival, oldest first; empty text when it has none."""
    directory = store_root / "held"
    if not directory.is_dir():
        return []
    return [_held_text(_read_json(path)) for path in sorted(directory.glob("*.json"))]


def _reached_trial(store: Any, update_id: str) -> bool:
    try:
        return '"trial"' in (store.root / update_id / "events.jsonl").read_text(encoding="utf-8")
    except OSError:
        return False


def _version_labels(record: Any) -> tuple[str, str]:
    """``(candidate, previous)`` by number when this install keeps versions."""
    from ..versions import layout as paths
    from ..versions import overview

    candidate_id = getattr(record.request, "candidate_version", None) or record.state.detail.get("candidate_version")
    runtime = paths.current_runtime()
    if runtime is None:
        return "the new version", "the previous version"
    candidate = overview.version_label(runtime.layout, candidate_id) if candidate_id else None
    return candidate or "the new version", overview.current_label(runtime.layout) or "the previous version"


def annotate(result: Any, store: Any, *, platform: str | None = None) -> Any:
    """``result`` with its progress in front. Never fails the call it describes."""
    if not isinstance(result, dict):
        return result
    waiting = result.get("queued") is True or (bool(result.get("queued")) and "phase" not in result)
    phase = QUEUED if waiting else result.get("phase") or result.get("status")
    if not isinstance(phase, str):
        return result
    try:
        candidate, previous, reached_trial, keeps_versions = "the new version", "the previous version", False, True
        update_id = result.get("update_id")
        # A queued request has no update directory yet, nor has one cancelled while it waited.
        cancelled_waiting = result.get("status") == "stopped" and not (store.root / str(update_id)).is_dir()
        if isinstance(update_id, str) and phase != QUEUED and not cancelled_waiting:
            from .switch import owns

            record = store.load(update_id)
            candidate, previous = _version_labels(record)
            reached_trial = _reached_trial(store, update_id)
            keeps_versions = owns(record)
        position = result.get("position")
        described = describe(
            phase, candidate=candidate, previous=previous,
            ahead=position if type(position) is int else queued_count(store.root) or None,
            held=held_messages(store.root), platform=platform, reached_trial=reached_trial,
            keeps_versions=keeps_versions,
        )
    except Exception:
        return result
    return {**described, **result} if described else result


__all__ = [
    "QUEUED", "WINDOWS_RESULT_NOTE", "WINDOWS_TRIAL_NOTE", "annotate", "describe",
    "held_messages", "queued_count",
]
