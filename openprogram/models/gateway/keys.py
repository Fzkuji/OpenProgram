"""The gateway key: what a harness config authenticates to ``/gateway/`` with.

The Worker's owner token changes with every start, so a config written into
``~/.codex/config.toml`` or ``~/.claude/settings.json`` could not use it.
The gateway key is random, kept in ``<state>/gateway/key`` (owner-only) and
survives restarts. It is accepted only on ``/gateway/`` paths
(``interfaces/server/owner_auth.py``), never for the rest of the API.
"""
from __future__ import annotations

import hmac
import os
import secrets
import threading
from pathlib import Path

#: The state directory entry the gateway keeps its key and stash in.
STATE_ENTRY = "gateway"
_PREFIX = "opgw-"
_lock = threading.Lock()


def gateway_dir() -> Path:
    from openprogram.state.paths import get_state_dir
    return get_state_dir() / STATE_ENTRY


def _key_path() -> Path:
    return gateway_dir() / "key"


def gateway_key() -> str:
    """This profile's gateway key, created on first use."""
    path = _key_path()
    with _lock:
        try:
            value = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            value = ""
        if value.startswith(_PREFIX):
            return value
        value = _PREFIX + secrets.token_urlsafe(32)
        path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
        tmp = path.with_name(f".key.{secrets.token_hex(6)}.tmp")
        fd = os.open(tmp, flags, 0o600)
        try:
            os.write(fd, value.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        from openprogram.host._platform.filesystem import replace_file, restrict_to_user
        restrict_to_user(tmp)
        replace_file(tmp, path)
        return value


def verify_gateway_key(value: str | None) -> bool:
    """Whether ``value`` is this profile's gateway key (constant time)."""
    if not value or not value.startswith(_PREFIX):
        return False
    try:
        stored = _key_path().read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return bool(stored) and hmac.compare_digest(stored.encode("utf-8"), value.encode("utf-8"))
