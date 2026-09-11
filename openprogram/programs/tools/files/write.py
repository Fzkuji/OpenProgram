"""write function — create a new file or overwrite an existing one."""

from __future__ import annotations

import os
import stat

from openprogram.programs._runtime import function
from openprogram.store.snapshot.checkpoint.helpers import (
    checkpoint_abort_edit,
    checkpoint_after_edit,
    checkpoint_before_edit,
)
from openprogram.worktree.path_resolve import resolve_path


_DESCRIPTION = (
    "Write content or publish a staged local file to a file on disk, creating it (and any missing "
    "parent directories) if it doesn't exist, or overwriting it if it does.\n"
    "\n"
    "- Paths MUST be absolute.\n"
    "- Prefer the `edit` tool for modifying existing files — it sends only the diff "
    "and is safer for concurrent edits. Use `write` for new files or full rewrites."
    "\n- For binary documents, stage the completed ordinary local file and pass `source_path`; "
    "`content` and `source_path` are mutually exclusive. Sources are limited to 64 MiB."
)

_MAX_SOURCE_BYTES = 64 * 1024 * 1024


def _read_staged_source(source_path: str, target_path: str) -> tuple[bytes, dict]:
    from openprogram._compat import is_link_metadata
    from openprogram.agent.permissions.file_state import check_current, fingerprint
    from openprogram.sandbox import validate_read_path

    violation = validate_read_path(source_path)
    if violation:
        raise ValueError(f"sandbox policy: {violation}")
    check_current(source_path)
    info = os.lstat(source_path)
    if not stat.S_ISREG(info.st_mode) or is_link_metadata(info) or info.st_nlink != 1:
        raise ValueError("source_path must be an ordinary non-linked file")
    if os.path.realpath(source_path) == os.path.realpath(target_path):
        raise ValueError("source_path and file_path must be different files")
    if info.st_size > _MAX_SOURCE_BYTES:
        raise ValueError("source_path exceeds the 64 MiB limit")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(source_path, flags)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError("source_path changed before reading")
        payload = stream.read(_MAX_SOURCE_BYTES + 1)
        after = os.fstat(stream.fileno())
    if len(payload) > _MAX_SOURCE_BYTES:
        raise ValueError("source_path exceeds the 64 MiB limit")
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_size, after.st_mtime_ns, after.st_ctime_ns,
    ) or len(payload) != after.st_size:
        raise ValueError("source_path changed while reading")
    state = fingerprint(source_path)
    import hashlib
    if (state.get("device"), state.get("inode"), state.get("sha256")) != (
        info.st_dev, info.st_ino, hashlib.sha256(payload).hexdigest(),
    ):
        raise ValueError("source_path changed while reading")
    check_current(source_path)
    return payload, state


def execute(file_path: str, content: str | None = None,
            source_path: str | None = None) -> str:
    """Write `content` to `file_path`, creating parents if needed.

    Args:
        file_path: Absolute path of the file to write.
        content: Full UTF-8 file contents to write.
        source_path: Existing ordinary local file to publish byte-for-byte.
    """
    if (content is None) == (source_path is None):
        return "Error: provide exactly one of content or source_path"
    # Worktree-aware resolution: relative paths bind to the active
    # worktree root when one is set; absolute paths outside the
    # worktree get a soft warning but still proceed (D6).
    resolved_path, outside_warning = resolve_path(file_path)
    file_path = resolved_path
    if not os.path.isabs(file_path):
        return f"Error: file_path must be absolute, got {file_path!r}"
    from openprogram.sandbox import validate_write_path
    violation = validate_write_path(file_path)
    if violation:
        return f"Error: sandbox policy: {violation}"
    payload: str | bytes
    source_state: dict[str, dict] = {}
    target_state = None
    if source_path is not None:
        source_path, _source_warning = resolve_path(source_path)
        if not os.path.isabs(source_path):
            return f"Error: source_path must be absolute, got {source_path!r}"
        try:
            from openprogram.agent.permissions.file_state import fingerprint
            target_state = fingerprint(file_path)
            payload, source_fingerprint = _read_staged_source(source_path, file_path)
            source_state[source_path] = source_fingerprint
        except (OSError, ValueError) as exc:
            return f"Error reading staged file: {exc}"
    else:
        assert content is not None
        payload = content

    # Read-before-edit gate — ONLY for overwriting an EXISTING file
    # (Claude-Code contract: a Write to a new file needs no prior read,
    # but overwriting one the agent never read / that changed on disk is
    # refused so a concurrent user change isn't clobbered). No-op outside
    # a turn.
    if os.path.exists(file_path) and not os.path.isdir(file_path):
        try:
            from openprogram.store.snapshot import read_tracking as _rt
            _fresh = _rt.check_fresh(file_path)
            if _fresh in (_rt.NEVER_READ, _rt.STALE):
                return _rt.stale_message(file_path, _fresh)
        except Exception:
            pass

    parent = os.path.dirname(file_path)
    if parent and not os.path.exists(parent):
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError as e:
            return f"Error creating directory {parent}: {e}"
    # Back up pre-edit state for turn-scoped revert. Safe to call
    # even when the file doesn't exist yet (records pre_existing=False
    # so restore_turn knows to delete-on-restore).
    try:
        prepared = checkpoint_before_edit(file_path)
    except Exception as e:
        return f"Error: mutation journal preparation failed for {file_path}: {e}"
    try:
        from openprogram.agent.permissions.file_state import write_checked
        if source_path is not None:
            from openprogram.agent.permissions.file_state import write_checked_atomic
            assert isinstance(payload, bytes) and target_state is not None
            write_checked_atomic(file_path, payload, expected_state=target_state,
                                 source_state=source_state)
        else:
            assert isinstance(payload, str)
            write_checked(file_path, payload)
    except Exception as e:
        if prepared:
            checkpoint_abort_edit(file_path, str(e))
        return f"Error writing {file_path}: {type(e).__name__}: {e}"
    try:
        checkpoint_after_edit(file_path, "write")
    except Exception as e:
        return f"Error: mutation journal commit failed for {file_path}: {e}"

    # Baseline the freshly-written content so the agent can edit/rewrite
    # this file again without re-reading.
    try:
        from openprogram.store.snapshot import read_tracking as _rt
        _rt.mark_seen(file_path)
    except Exception:
        pass

    # 事件层 tap：写成功才发。懒 import，照 mark_seen 的防循环模式。
    try:
        from openprogram.events import emit_safe
        emit_safe("file.changed", "tool", {"path": file_path, "op": "write"})
    except Exception:
        pass

    msg = f"Wrote {len(payload)} bytes to {file_path}"
    if outside_warning:
        msg = f"{outside_warning}\n{msg}"
    return msg


# Keep the Python-callable implementation separate from the registered tool.
# Workflows import ``execute`` directly; model tool dispatch uses ``write``.
write = function(
    name="write",
    accept_edits_safe=True,
    description=_DESCRIPTION,
    toolset=["core"],
    unsafe_in=["wechat", "telegram", "plan"],
    path_params={"file_path": "write", "source_path": "read"},
)(execute)


__all__ = ["execute", "write"]
