"""Cross-process steering inbox shared by normal chat and programs.

Each message is one file under ``<session>/steering``.  The research
application's historical ``research_harness.steering`` module uses the same
layout, so either consumer can read messages written by either surface.
"""
from __future__ import annotations

import threading
import uuid
from pathlib import Path


_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_accepting: set[str] = set()


def _lock_for(session_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(session_id, threading.Lock())


def _steer_dir(session_id: str) -> Path | None:
    if not session_id:
        return None
    try:
        from openprogram.agent.session_db import default_db

        path = Path(default_db()._session_dir(session_id)) / "steering"
        path.mkdir(parents=True, exist_ok=True)
        return path
    except Exception:
        return None


def push(session_id: str, message: str) -> bool:
    """Append one non-empty message. Return whether the file was written."""
    text = (message or "").strip()
    path = _steer_dir(session_id)
    if not text or path is None:
        return False
    with _lock_for(session_id):
        return _push_unlocked(path, text)


def _push_unlocked(path: Path, text: str) -> bool:
    try:
        index = len(list(path.glob("*.txt")))
        target = path / f"{index:06d}-{uuid.uuid4().hex[:6]}.txt"
        target.write_text(text, encoding="utf-8")
        return True
    except OSError:
        return False


def begin_accepting(session_id: str) -> None:
    """Mark this process's normal chat turn as able to consume steer."""
    with _lock_for(session_id):
        _accepting.add(session_id)


def end_accepting(session_id: str) -> None:
    with _lock_for(session_id):
        _accepting.discard(session_id)


def push_if_accepting(session_id: str, message: str) -> bool | None:
    """Push for a local normal chat, or return None when none is accepting."""
    text = (message or "").strip()
    path = _steer_dir(session_id)
    if not text or path is None:
        return False
    with _lock_for(session_id):
        if session_id not in _accepting:
            return None
        return _push_unlocked(path, text)


def _pop_unlocked(path: Path) -> str | None:
    try:
        files = sorted(path.glob("*.txt"))
    except OSError:
        return None
    for item in files:
        try:
            text = item.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        try:
            item.unlink()
        except OSError:
            continue
        if text:
            return text
    return None


def pop(session_id: str) -> str | None:
    """Take one oldest message for one-at-a-time agent steering."""
    path = _steer_dir(session_id)
    if path is None:
        return None
    with _lock_for(session_id):
        return _pop_unlocked(path)


def drain(session_id: str) -> list[str]:
    """Take all remaining messages in FIFO order."""
    path = _steer_dir(session_id)
    if path is None:
        return []
    messages: list[str] = []
    with _lock_for(session_id):
        while True:
            message = _pop_unlocked(path)
            if message is None:
                return messages
            messages.append(message)


def close_and_drain(session_id: str) -> list[str]:
    """Stop local acceptance and atomically take the final inbox remainder."""
    path = _steer_dir(session_id)
    if path is None:
        end_accepting(session_id)
        return []
    messages: list[str] = []
    with _lock_for(session_id):
        _accepting.discard(session_id)
        while True:
            message = _pop_unlocked(path)
            if message is None:
                return messages
            messages.append(message)


def pending(session_id: str) -> bool:
    path = _steer_dir(session_id)
    if path is None:
        return False
    try:
        return any(path.glob("*.txt"))
    except OSError:
        return False


def clear(session_id: str) -> None:
    drain(session_id)
