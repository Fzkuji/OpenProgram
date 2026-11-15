"""Reading and writing the user's ``config.json``.

Every layer that reads or changes the user config goes through these four
functions; the file lives at ``openprogram.state.paths.get_config_path()``
(profile-aware). Writes are private (0o600), atomic and file-locked.

- ``read_config()``: the whole file as a dict, ``{}`` when it is missing.
  For read-only use. A file that is not valid JSON is unknown, not empty:
  it is set aside and its last good copy (``config.json.bak``) is read
  instead (``openprogram.state.last_good``).
- ``read_config_with_revision()``: a snapshot plus its exact-byte revision,
  for a later ``write_config`` / ``update_config`` that must refuse to
  clobber a concurrent change.
- ``write_config(cfg, expected_revision=...)``: full replace.
- ``update_config(mutator, expected_revision=...)``: the only correct way
  to change *part* of the config.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Callable

from openprogram.state.paths import get_config_path


def _document(path, raw: bytes | None) -> dict[str, Any]:
    """The config in ``raw``; an unreadable file reads as its last good copy."""
    from openprogram.state.last_good import load_json

    config = load_json(path, raw)
    return config if isinstance(config, dict) else {}


def read_config() -> dict[str, Any]:
    """The whole config file as a dict, or ``{}`` if it is missing."""
    path = get_config_path()
    from openprogram.host._platform.filesystem import retry_sharing_violation

    try:
        # Unlocked, so on Windows it can meet a writer's replace mid-flight.
        raw = retry_sharing_violation(path.read_bytes)
    except FileNotFoundError:
        raw = None
    return _document(path, raw)


def read_config_with_revision() -> tuple[dict[str, Any], str]:
    """Read a prompt snapshot and its exact-byte revision under the file lock."""
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    from openprogram.models.auth.credentials import (
        _private_file_lock,
        _read_private_bytes,
        _revision,
    )

    with _private_file_lock(path, root=path.parent):
        raw = _read_private_bytes(path, root=path.parent)
        revision = _revision(raw)
    return _document(path, raw), revision


def write_config(cfg: dict[str, Any], *, expected_revision: str | None = None):
    """Replace the whole config file; refuse if ``expected_revision`` is stale."""
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(cfg, indent=2) + "\n"
    from openprogram.models.auth.credentials import _private_atomic_write

    return _private_atomic_write(
        path,
        lambda handle: handle.write(data.encode("utf-8")),
        root=path.parent,
        expected_revision=expected_revision,
    )


# In-process lock for the read-modify-write critical section. Cross-process
# safety comes from the file lock acquired inside ``update_config``.
_config_write_lock = threading.Lock()


def update_config(
    mutator: Callable[[dict], Any], *, expected_revision: str | None = None
) -> dict:
    """Atomic read-modify-write of config.json.

    Holds an in-process lock (the worker's threads) AND a cross-process file
    lock (``config.json.lock``: the worker vs the ``openprogram config`` /
    ``openprogram setup`` processes), reads the current config, applies
    ``mutator(cfg)`` in place, writes it back (0o600) and returns it.

    This is the ONLY correct way to change *part* of the config: a bare
    ``read_config()`` ... ``write_config()`` pair races (the later write
    clobbers a concurrent one). ``read_config`` / ``write_config`` remain for
    read-only and full-replace.
    """
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    updated: dict[str, Any] = {}

    def update(raw: bytes | None) -> bytes:
        nonlocal updated
        cfg = _document(path, raw)
        mutator(cfg)
        updated = cfg
        return (json.dumps(cfg, indent=2) + "\n").encode("utf-8")

    with _config_write_lock:
        from openprogram.models.auth.credentials import _private_atomic_update

        _private_atomic_update(
            path,
            update,
            root=path.parent,
            expected_revision=expected_revision,
        )
    return updated
