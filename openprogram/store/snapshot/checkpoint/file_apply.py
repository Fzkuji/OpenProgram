"""Publish and roll back files under verified parent directories."""
from __future__ import annotations

import os
from pathlib import Path
from . import file_state


def _apply_state(
    path: str,
    state: dict,
    backup_dir: Path,
    transaction_id: str,
    expected_current: dict | None = None,
) -> str | None:
    target = Path(path)
    tmp_name = f".{target.name}.{transaction_id}.tmp"
    guard_name = f".{target.name}.{transaction_id}.guard"
    expected = expected_current or file_state._inspect_state(path)
    chain = expected.get("parent_chain") or file_state._capture_parent_chain(path)
    if not file_state._DIR_FD_APPLY_SUPPORTED:
        return _apply_state_without_dir_fd(
            target,
            state,
            backup_dir,
            expected,
            chain,
            tmp_name,
            guard_name,
        )
    parent_descriptor = file_state._open_verified_parent(path, chain)
    try:
        if state.get("kind") == "regular":
            blob = Path(str(state.get("blob_path"))) \
                if state.get("blob_path") else (
                    backup_dir / str(state.get("blob_ref") or "")
                )
            if not blob.is_file():
                raise OSError(f"missing recovery blob for {path}")
            if state.get("digest") and file_state._digest(blob) != state.get("digest"):
                raise OSError(f"recovery blob digest mismatch for {path}")
            tmp_descriptor = os.open(
                tmp_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=parent_descriptor,
            )
            try:
                with blob.open("rb") as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        view = memoryview(chunk)
                        while view:
                            written = os.write(tmp_descriptor, view)
                            view = view[written:]
                os.fchmod(
                    tmp_descriptor,
                    int(str(state.get("mode") or "0644"), 8),
                )
                os.fsync(tmp_descriptor)
            finally:
                os.close(tmp_descriptor)

        if expected.get("kind") == "regular":
            os.rename(
                target.name, guard_name,
                src_dir_fd=parent_descriptor, dst_dir_fd=parent_descriptor,
            )
            moved = file_state._inspect_state_at(parent_descriptor, guard_name)
            if not file_state._state_matches(moved, expected):
                try:
                    os.rename(
                        guard_name, target.name,
                        src_dir_fd=parent_descriptor,
                        dst_dir_fd=parent_descriptor,
                    )
                except FileExistsError:
                    pass
                raise OSError(f"stale current state for {path}")
        elif expected.get("kind") != "absent":
            raise OSError(f"unsafe current state for {path}")

        if state.get("kind") == "regular":
            try:
                os.link(
                    tmp_name, target.name,
                    src_dir_fd=parent_descriptor,
                    dst_dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
            except FileExistsError as exc:
                raise OSError(f"external writer created {path}") from exc
            os.unlink(tmp_name, dir_fd=parent_descriptor)
        elif state.get("kind") != "absent":
            raise OSError(f"unsupported target state for {path}")

        os.fsync(parent_descriptor)
        guard_exists = file_state._inspect_state_at(
            parent_descriptor, guard_name,
        ).get("kind") != "absent"
        return str(target.parent / guard_name) if guard_exists else None
    finally:
        try:
            try:
                os.unlink(tmp_name, dir_fd=parent_descriptor)
            except FileNotFoundError:
                pass
        finally:
            os.close(parent_descriptor)


def _apply_state_without_dir_fd(
    target: Path,
    state: dict,
    backup_dir: Path,
    expected: dict,
    chain: dict,
    tmp_name: str,
    guard_name: str,
) -> str | None:
    parent = file_state._verify_parent_path(str(target), chain)
    tmp_path = parent / tmp_name
    guard_path = parent / guard_name
    try:
        if state.get("kind") == "regular":
            blob = Path(str(state.get("blob_path"))) \
                if state.get("blob_path") else (
                    backup_dir / str(state.get("blob_ref") or "")
                )
            if not blob.is_file():
                raise OSError(f"missing recovery blob for {target}")
            if state.get("digest") and file_state._digest(blob) != state.get("digest"):
                raise OSError(f"recovery blob digest mismatch for {target}")
            tmp_descriptor = os.open(
                tmp_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
                0o600,
            )
            try:
                with blob.open("rb") as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        view = memoryview(chunk)
                        while view:
                            written = os.write(tmp_descriptor, view)
                            view = view[written:]
                os.chmod(tmp_path, int(str(state.get("mode") or "0644"), 8))
                os.fsync(tmp_descriptor)
            finally:
                os.close(tmp_descriptor)

        if expected.get("kind") == "regular":
            os.rename(target, guard_path)
            moved = file_state._inspect_state_path(guard_path)
            if not file_state._state_matches(moved, expected):
                try:
                    os.rename(guard_path, target)
                except FileExistsError:
                    pass
                raise OSError(f"stale current state for {target}")
        elif expected.get("kind") != "absent":
            raise OSError(f"unsafe current state for {target}")

        if state.get("kind") == "regular":
            try:
                os.link(tmp_path, target)
            except FileExistsError as exc:
                raise OSError(f"external writer created {target}") from exc
            os.unlink(tmp_path)
        elif state.get("kind") != "absent":
            raise OSError(f"unsupported target state for {target}")

        _fsync_directory(parent)
        guard_exists = file_state._inspect_state_path(guard_path).get("kind") != "absent"
        return str(guard_path) if guard_exists else None
    finally:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass


def _restore_changed_guard(
    action: dict,
    guard_path: str,
    transaction_id: str,
) -> None:
    target = Path(action["path"])
    guard = Path(guard_path)
    applied_name = f".{target.name}.{transaction_id}.applied"
    chain = action["expected_current"].get("parent_chain") \
        or file_state._capture_parent_chain(action["path"])
    if not file_state._DIR_FD_APPLY_SUPPORTED:
        _restore_changed_guard_without_dir_fd(
            action, guard, applied_name, chain,
        )
        return
    descriptor = file_state._open_verified_parent(action["path"], chain)
    try:
        if file_state._inspect_state_at(descriptor, target.name).get("kind") != "absent":
            os.rename(
                target.name, applied_name,
                src_dir_fd=descriptor, dst_dir_fd=descriptor,
            )
            action["recovery_artifact"] = str(target.parent / applied_name)
        if file_state._inspect_state_at(descriptor, target.name).get("kind") != "absent":
            raise OSError(f"external writer recreated {target}")
        os.rename(
            guard.name, target.name,
            src_dir_fd=descriptor, dst_dir_fd=descriptor,
        )
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _restore_changed_guard_without_dir_fd(
    action: dict,
    guard: Path,
    applied_name: str,
    chain: dict,
) -> None:
    target = Path(action["path"])
    parent = file_state._verify_parent_path(action["path"], chain)
    applied_path = parent / applied_name
    guard_path = parent / guard.name
    if file_state._inspect_state_path(target).get("kind") != "absent":
        os.rename(target, applied_path)
        action["recovery_artifact"] = str(applied_path)
    if file_state._inspect_state_path(target).get("kind") != "absent":
        raise OSError(f"external writer recreated {target}")
    os.rename(guard_path, target)
    _fsync_directory(parent)


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        pass

