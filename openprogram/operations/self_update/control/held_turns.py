"""Turns that arrive during a version switch wait here and run after it.

From step 3 of activation until step 11 no new turn starts. A message, a
scheduled turn or an always-on wake that reaches admission in that window is
saved under ``self-updates/held/`` with what is needed to resubmit it, and the
sender is told it will run. The worker of whichever version comes up runs them
when admission reopens, whether the switch succeeded or rolled back.

A saved turn is deleted only once it has run or its reply is already there. One
that fails stays claimed and the watchdog's next pass tries again; after
``MAX_ATTEMPTS`` it moves to ``held/failed/`` and the owner is told.
"""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import asdict, fields, is_dataclass
import json
import logging
import os
from pathlib import Path
import threading
import time
import uuid
from typing import Any, Callable

from ..store import SelfUpdateStore
from ..types import PROTOCOL_VERSION
from .maintenance import closes_admission, holds_turns, load_maintenance
from openprogram.host._platform import filesystem

MAX_ATTEMPTS = 3
_PREVIEW_CHARACTERS = 80
# A component of a running turn cannot wait for itself to finish.
_NOT_HELD = frozenset({"agent_spawn", "self_update_diagnose", "self_update_repair", "self_update_continue"})
_INTERNAL = frozenset({"history_override"})  # Reloaded at replay, unless it says the run starts clean.
_log = logging.getLogger(__name__)
_running: set[str] = set()
_running_lock = threading.Lock()


def _directory(store: SelfUpdateStore) -> Path:
    return store.root / "held"


def _claimed(store: SelfUpdateStore) -> Path:
    return _directory(store) / "running"


def _failed(store: SelfUpdateStore) -> Path:
    return _directory(store) / "failed"


def _encode_rules(rules: Any) -> Any:
    return asdict(rules) if is_dataclass(rules) else rules


def _decode_rules(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    from openprogram.runtime.permissions.modes import PermissionRules

    return PermissionRules(**value)


def encode(request: Any) -> dict[str, Any]:
    """The fields of a turn request as JSON, without what replay reloads."""
    from openprogram.runtime.agent.turn_request import INHERIT_PARENT

    values: dict[str, Any] = {}
    for item in fields(request):
        value = getattr(request, item.name)
        if (item.name in _INTERNAL and value != []) or value is INHERIT_PARENT:
            continue
        values[item.name] = _encode_rules(value) if item.name == "permission_rules" else value
    json.dumps(values, allow_nan=False)  # A value that cannot be saved must fail here.
    return values


def decode(values: dict[str, Any]) -> Any:
    from openprogram.runtime.agent.turn_request import TurnRequest

    names = {item.name for item in fields(TurnRequest)}
    arguments = {key: value for key, value in values.items() if key in names}
    if "permission_rules" in arguments:
        arguments["permission_rules"] = _decode_rules(arguments["permission_rules"])
    return TurnRequest(**arguments)


def hold(request: Any, *, store: SelfUpdateStore | None = None, locked: bool = False) -> bool:
    """Save a turn that arrived during a switch; ``False`` means it was not held.

    ``locked`` says the caller already holds the store lock, as the admission
    check does, so the marker cannot be lifted between its check and this save.
    """
    if request.source in _NOT_HELD:
        return False
    target = store or SelfUpdateStore()
    try:
        values = encode(request)
    except (TypeError, ValueError):
        _log.warning("turn could not be saved during the switch", exc_info=True)
        return False
    with nullcontext() if locked else target._locked():
        if not holds_turns(load_maintenance(target)):
            return False
        directory = _directory(target)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        name = f"{time.time_ns():020d}-{uuid.uuid4().hex[:12]}.json"
        target._write_json(directory / name, {"schema": 1, "protocol": PROTOCOL_VERSION, "request": values})
    return True


def count(store: SelfUpdateStore) -> int:
    directory = _directory(store)
    if not directory.is_dir():
        return 0
    return sum(1 for path in directory.glob("*.json") if path.is_file())


def _already_answered(values: dict[str, Any]) -> bool:
    from openprogram.runtime.context.session_db import default_db

    message = values.get("user_msg_id")
    return bool(message) and default_db().message_exists(values["session_id"], f"{message}_reply")


def _publish(envelope: dict) -> None:
    from openprogram.runtime.jobs.runner.shared import _broadcast

    _broadcast(envelope)


def _run_one(values: dict[str, Any]) -> None:
    """Run one saved turn as the turn it would have been."""
    from openprogram.runtime.conversation.turn_runtime import process_user_turn

    request = decode(values)
    # The channel's inbound call returned the waiting notice, so no one is
    # left to send this reply unless the turn sends it itself.
    result = process_user_turn(request, on_event=_publish, execution_context={"held_replay": True})
    from openprogram.runtime.scheduler import run_finish

    run_finish.finish_held(request, result)


def _tell_owner(saved: Path, values: dict[str, Any] | None) -> None:
    from ..delivery.owner_notice import tell

    words = " ".join(str((values or {}).get("user_text") or "").split())[:_PREVIEW_CHARACTERS]
    quoted = f' ("{words}")' if words else ""
    text = (f"A message held during a version switch{quoted} could not be run, so it did not get a reply. "
            f"Please send it again. It is saved at {saved}.")
    try:
        tell(f"held-turn-failed:{saved.name}", text)
    except Exception:  # noqa: BLE001 - the turn is safe on disk; the log says where.
        _log.exception("the owner could not be told about held turn %s", saved.name)


def _give_up(store: SelfUpdateStore, path: Path, values: dict[str, Any] | None) -> None:
    """Move a turn that cannot run out of the way, keeping it, and say so."""
    directory = _failed(store)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    saved = directory / path.name
    filesystem.replace_file(path, saved)
    _log.error("held turn %s was given up on; it is saved at %s", path.name, saved)
    _tell_owner(saved, values)


def _run_saved(store: SelfUpdateStore, path: Path, run: Callable[[dict], None]) -> bool:
    """Run one saved turn; ``False`` leaves it claimed for a later pass."""
    try:
        envelope = store._read_json(path)
        values = envelope["request"]
        attempts = int(envelope.get("attempts") or 0)
    except Exception:  # noqa: BLE001 - a file nothing can read will not get better.
        _log.exception("held turn %s cannot be read", path.name)
        _give_up(store, path, None)
        return True
    if attempts >= MAX_ATTEMPTS:
        _give_up(store, path, values)
        return True
    # Counted before the run, so a turn that takes the worker down is bounded too.
    store._write_json(path, {**envelope, "attempts": attempts + 1})
    try:
        if not _already_answered(values):
            run(values)
    except Exception:  # noqa: BLE001 - the file stays; a stop of the worker passes through.
        _log.exception("held turn %s did not run (attempt %d)", path.name, attempts + 1)
        if attempts + 1 >= MAX_ATTEMPTS:
            _give_up(store, path, values)
            return True
        return False
    path.unlink(missing_ok=True)
    return True


def _run_session(store: SelfUpdateStore, paths: list[Path], run: Callable[[dict], None]) -> None:
    """Run one conversation's held turns in the order they arrived."""
    try:
        for path in paths:
            if not _run_saved(store, path, run):
                return  # The turns after it wait, so the conversation stays in order.
    finally:
        with _running_lock:
            _running.difference_update(path.name for path in paths)


def _start_thread(target: Callable[[], None]) -> None:
    threading.Thread(target=target, daemon=True, name="self-update-held-turns").start()


def _session_of(store: SelfUpdateStore, path: Path) -> str:
    """Its conversation; a file that cannot be read goes through alone and is given up on."""
    try:
        return str(store._read_json(path)["request"]["session_id"])
    except Exception:  # noqa: BLE001
        return f"unreadable:{path.name}"


def drain(*, store: SelfUpdateStore | None = None, run: Callable[[dict], None] = _run_one,
          start: Callable[[Callable[[], None]], None] = _start_thread) -> int:
    """Start every held turn once admission is open; how many were started."""
    target = store or SelfUpdateStore()
    directory = _directory(target)
    if not directory.is_dir():
        return 0
    by_session: dict[str, list[Path]] = {}
    with target._locked():
        if closes_admission(load_maintenance(target)):
            return 0
        claimed = _claimed(target)
        claimed.mkdir(mode=0o700, exist_ok=True)
        with _running_lock:
            # A claim no thread of this worker owns was left by a crash.
            orphans = [path for path in claimed.glob("*.json") if path.name not in _running]
            arrivals = sorted(directory.glob("*.json"))
            for path in orphans:
                by_session.setdefault(_session_of(target, path), []).append(path)
                _running.add(path.name)
            for path in arrivals:
                session_id = _session_of(target, path)
                moved = claimed / path.name
                filesystem.replace_file(path, moved)
                by_session.setdefault(session_id, []).append(moved)
                _running.add(moved.name)
    for paths in by_session.values():
        paths.sort(key=lambda item: item.name)
        start(lambda paths=paths: _run_session(target, paths, run))
    return sum(len(paths) for paths in by_session.values())


__all__ = ["MAX_ATTEMPTS", "count", "decode", "drain", "encode", "hold"]
