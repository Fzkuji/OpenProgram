"""Per-session exact file-mutation journal and recovery snapshots."""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import shutil
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import manifest
from .paths import (
    path_basename,
    session_backup_root,
    turn_backup_dir,
    turn_manifest_path,
)


_STATS_MAX_BYTES = 1024 * 1024


class MutationJournalError(RuntimeError):
    """A trusted mutation could not be recorded safely."""


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return f"sha256:{value.hexdigest()}"


def _digest_fd(descriptor: int) -> str:
    value = hashlib.sha256()
    os.lseek(descriptor, 0, os.SEEK_SET)
    for chunk in iter(lambda: os.read(descriptor, 1024 * 1024), b""):
        value.update(chunk)
    return f"sha256:{value.hexdigest()}"


def _file_kind(mode: int) -> str:
    if stat.S_ISREG(mode):
        return "regular"
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISDIR(mode):
        return "directory"
    return "special"


def _has_nul(path: Path) -> bool:
    with path.open("rb") as handle:
        return b"\0" in handle.read(8192)


def _line_stats(before: Path | None, after: Path | None) -> tuple[dict, str]:
    paths = [path for path in (before, after) if path is not None]
    if any(path.stat().st_size > _STATS_MAX_BYTES for path in paths):
        binary = any(_has_nul(path) for path in paths)
        return {"added": None, "removed": None, "binary": binary}, (
            "binary" if binary else "large"
        )
    raw_before = before.read_bytes() if before is not None else b""
    raw_after = after.read_bytes() if after is not None else b""
    if b"\0" in raw_before or b"\0" in raw_after:
        return {"added": None, "removed": None, "binary": True}, "binary"
    old = raw_before.decode("utf-8", errors="replace").splitlines()
    new = raw_after.decode("utf-8", errors="replace").splitlines()
    added = 0
    removed = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
        a=old, b=new, autojunk=False,
    ).get_opcodes():
        if tag in {"insert", "replace"}:
            added += j2 - j1
        if tag in {"delete", "replace"}:
            removed += i2 - i1
    return {"added": added, "removed": removed, "binary": False}, "available"


class CheckpointStore:
    def __init__(self, session_dir: Path):
        self.session_dir = Path(session_dir)

    def _capture_regular(self, source: Path, destination: Path) -> dict:
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(source, destination, follow_symlinks=False)
            info = destination.stat()
            return {
                "kind": "regular",
                "digest": _digest(destination),
                "blob_ref": destination.name,
                "mode": f"{stat.S_IMODE(info.st_mode):04o}",
                "size": info.st_size,
            }
        except OSError as exc:
            raise MutationJournalError(f"cannot snapshot {source}: {exc}") from exc

    def backup_before_edit(
        self,
        turn_id: str,
        abs_path: str,
        *,
        content_src: str | Path | None = None,
    ) -> None:
        if not turn_id or not abs_path:
            return
        backup_name = path_basename(abs_path)
        manifest_path = turn_manifest_path(self.session_dir, turn_id)
        target = Path(abs_path)
        try:
            target_stat = os.lstat(target)
        except FileNotFoundError:
            target_stat = None
        except OSError as exc:
            raise MutationJournalError(f"cannot inspect {target}: {exc}") from exc

        if target_stat is not None and not stat.S_ISREG(target_stat.st_mode):
            raise MutationJournalError(
                f"unsafe file type for exact mutation: {_file_kind(target_stat.st_mode)}",
            )
        if target_stat is not None and target_stat.st_nlink != 1:
            raise MutationJournalError(
                f"hardlinked file has {target_stat.st_nlink} links",
            )
        if manifest.has(manifest_path, backup_name):
            return
        backup_dir = turn_backup_dir(self.session_dir, turn_id)
        backup_dir.mkdir(parents=True, exist_ok=True)

        pre_existing = target_stat is not None
        recoverability = "exact"
        unavailable_reason = None
        if not pre_existing:
            before = {"kind": "absent"}
        else:
            source = Path(content_src) if content_src is not None else target
            try:
                source_stat = os.lstat(source)
            except FileNotFoundError:
                source_stat = None
            if source_stat is None or not stat.S_ISREG(source_stat.st_mode):
                before = {"kind": "unavailable"}
                recoverability = "unavailable"
                unavailable_reason = "missing_preimage"
            else:
                before = self._capture_regular(source, backup_dir / backup_name)

        manifest.record_prepared(
            manifest_path,
            backup_name,
            abs_path,
            pre_existing=pre_existing,
            before=before,
            recoverability=recoverability,
            unavailable_reason=unavailable_reason,
        )

    def commit_after_edit(
        self, turn_id: str, abs_path: str, *, operation: str | None = None,
    ) -> None:
        if not turn_id or not abs_path:
            return
        backup_name = path_basename(abs_path)
        manifest_path = turn_manifest_path(self.session_dir, turn_id)
        value = manifest.load(manifest_path)
        entry = value.get("files", {}).get(backup_name)
        if not entry:
            raise MutationJournalError(f"no prepared mutation for {abs_path}")
        backup_dir = turn_backup_dir(self.session_dir, turn_id)
        target = Path(abs_path)
        try:
            target_stat = os.lstat(target)
        except FileNotFoundError:
            target_stat = None
        except OSError as exc:
            raise MutationJournalError(f"cannot inspect {target}: {exc}") from exc

        after_blob: Path | None = None
        if target_stat is None:
            after = {"kind": "absent"}
        elif stat.S_ISREG(target_stat.st_mode):
            after_blob = backup_dir / f"{backup_name}.after"
            after = self._capture_regular(target, after_blob)
        else:
            after = {"kind": _file_kind(target_stat.st_mode)}

        before = entry.get("before") or {
            "kind": "regular" if entry.get("pre_existing") else "absent",
        }
        before_blob = (
            backup_dir / str(before.get("blob_ref"))
            if before.get("kind") == "regular" and before.get("blob_ref")
            else None
        )
        if before.get("kind") == "absent" and after.get("kind") == "regular":
            canonical_operation = "create"
        elif before.get("kind") == "regular" and after.get("kind") == "absent":
            canonical_operation = "delete"
        else:
            canonical_operation = operation or "modify"
            if canonical_operation in {"write", "edit", "update", "add"}:
                canonical_operation = "modify"
        stats, diff_state = _line_stats(before_blob, after_blob)
        manifest.commit(
            manifest_path,
            backup_name,
            operation=canonical_operation,
            after=after,
            stats=stats,
            diff_state=diff_state,
        )

    def abort_edit(self, turn_id: str, abs_path: str, error: str | None = None) -> None:
        if turn_id and abs_path:
            manifest.abort(
                turn_manifest_path(self.session_dir, turn_id),
                path_basename(abs_path),
                error,
            )

    def list_mutations(self, turn_id: str) -> list[dict]:
        rows: list[dict] = []
        for _backup_name, entry in manifest.entries(
            turn_manifest_path(self.session_dir, turn_id),
        ):
            if entry.get("status") == "committed":
                rows.append(dict(entry))
        return rows

    def _inspect_state(self, path: str) -> dict:
        try:
            chain = self._capture_parent_chain(path)
            descriptor = self._open_verified_parent(path, chain)
        except (FileNotFoundError, NotADirectoryError):
            return {"kind": "absent"}
        except OSError:
            return {"kind": "unsafe_parent"}
        try:
            return self._inspect_state_at(descriptor, Path(path).name)
        finally:
            os.close(descriptor)

    @staticmethod
    def _capture_parent_chain(path: str) -> dict:
        target = Path(path)
        if not target.is_absolute() or not target.name:
            raise OSError(f"history path must be an absolute file path: {path}")
        parts = target.parent.parts
        if not parts:
            raise OSError(f"history path has no parent: {path}")
        current = Path(parts[0])
        root_info = os.lstat(current)
        if not stat.S_ISDIR(root_info.st_mode) or stat.S_ISLNK(root_info.st_mode):
            raise OSError(f"unsafe root for history path: {path}")
        components = []
        for name in parts[1:]:
            current = current / name
            info = os.lstat(current)
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise OSError(f"unsafe parent for history path: {current}")
            components.append({"name": name, "dev": info.st_dev, "ino": info.st_ino})
        return {
            "root": parts[0],
            "root_dev": root_info.st_dev,
            "root_ino": root_info.st_ino,
            "components": components,
        }

    @staticmethod
    def _open_verified_parent(path: str, chain: dict) -> int:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(str(chain["root"]), flags | nofollow)
        try:
            info = os.fstat(descriptor)
            if (info.st_dev, info.st_ino) != (
                chain.get("root_dev"), chain.get("root_ino"),
            ):
                raise OSError(f"history root changed before apply: {path}")
            for component in chain.get("components", []):
                child = os.open(
                    component["name"], flags | nofollow, dir_fd=descriptor,
                )
                child_info = os.fstat(child)
                if (child_info.st_dev, child_info.st_ino) != (
                    component.get("dev"), component.get("ino"),
                ):
                    os.close(child)
                    raise OSError(f"history parent changed before apply: {path}")
                os.close(descriptor)
                descriptor = child
            return descriptor
        except Exception:
            os.close(descriptor)
            raise

    @staticmethod
    def _inspect_state_at(descriptor: int, name: str) -> dict:
        try:
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return {"kind": "absent"}
        if not stat.S_ISREG(info.st_mode):
            return {"kind": _file_kind(info.st_mode)}
        if info.st_nlink != 1:
            return {"kind": "hardlink", "links": info.st_nlink}
        file_descriptor = os.open(
            name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=descriptor,
        )
        try:
            return {
                "kind": "regular",
                "digest": _digest_fd(file_descriptor),
                "mode": f"{stat.S_IMODE(info.st_mode):04o}",
                "size": info.st_size,
            }
        finally:
            os.close(file_descriptor)

    @staticmethod
    def _state_matches(actual: dict, expected: dict) -> bool:
        if actual.get("kind") != expected.get("kind"):
            return False
        if expected.get("kind") == "regular":
            return actual.get("digest") == expected.get("digest")
        return expected.get("kind") == "absent"

    @staticmethod
    def _same_recorded_state(first: dict, second: dict) -> bool:
        if first.get("kind") != second.get("kind"):
            return False
        if first.get("kind") == "regular":
            return first.get("digest") == second.get("digest")
        return first.get("kind") == "absent"

    def _state_with_blob(self, turn_id: str, state: dict) -> dict:
        value = dict(state)
        if value.get("kind") == "regular":
            value["blob_path"] = str(
                turn_backup_dir(self.session_dir, turn_id)
                / str(value.get("blob_ref") or "")
            )
        return value

    @staticmethod
    def _blob_is_exact(state: dict) -> bool:
        if state.get("kind") != "regular":
            return state.get("kind") == "absent"
        blob = Path(str(state.get("blob_path") or ""))
        if not blob.is_file():
            return False
        try:
            return _digest(blob) == state.get("digest")
        except OSError:
            return False

    def plan_history_operation(self, turn_id: str, direction: str) -> dict:
        if direction not in {"revert", "reapply"}:
            return {"status": "error", "error": f"unknown direction {direction!r}"}
        mutations = self.list_mutations(turn_id)
        if not mutations:
            return {"status": "error", "error": "no committed mutations"}
        backup_dir = turn_backup_dir(self.session_dir, turn_id)
        actions = []
        conflicts = []
        unavailable = []
        for mutation in mutations:
            path = mutation.get("path") or ""
            source = mutation.get("after") if direction == "revert" else mutation.get("before")
            target = mutation.get("before") if direction == "revert" else mutation.get("after")
            if (
                not path
                or mutation.get("recoverability") != "exact"
                or not isinstance(source, dict)
                or not isinstance(target, dict)
                or source.get("kind") not in {"regular", "absent"}
                or target.get("kind") not in {"regular", "absent"}
            ):
                unavailable.append(path)
                continue
            try:
                parent_chain = self._capture_parent_chain(path)
            except OSError:
                unavailable.append(path)
                continue
            source = {**source, "parent_chain": parent_chain}
            target = {**target, "parent_chain": parent_chain}
            missing_blob = False
            for state in (source, target):
                if state.get("kind") != "regular":
                    continue
                blob = backup_dir / str(state.get("blob_ref") or "")
                if not state.get("blob_ref") or not blob.is_file():
                    missing_blob = True
                    break
            if missing_blob:
                unavailable.append(path)
                continue
            current = self._inspect_state(path)
            if not self._state_matches(current, source):
                conflicts.append(path)
                continue
            actions.append({
                "path": path,
                "expected_current": source,
                "target": target,
                "rollback": source,
                "state": "pending",
                "error": None,
            })
        if unavailable:
            return {
                "status": "unavailable",
                "actions": actions,
                "conflicts": conflicts,
                "unavailable": unavailable,
                "error": "one or more mutations are not recoverable",
            }
        if conflicts:
            return {
                "status": "blocked",
                "actions": actions,
                "conflicts": conflicts,
                "unavailable": [],
                "error": "current file state does not match the recorded source",
            }
        return {
            "status": "ready",
            "actions": actions,
            "conflicts": [],
            "unavailable": [],
        }

    def plan_rewind_operation(self, turn_ids: list[str]) -> dict:
        """Fold a newest-to-oldest turn suffix into one action per path."""
        folded: dict[str, dict] = {}
        unavailable: list[str] = []
        discontinuous: list[str] = []
        for turn_id in reversed(list(dict.fromkeys(turn_ids))):
            for mutation in self.list_mutations(turn_id):
                path = mutation.get("path") or ""
                before = mutation.get("before")
                after = mutation.get("after")
                if (
                    not path
                    or mutation.get("recoverability") != "exact"
                    or not isinstance(before, dict)
                    or not isinstance(after, dict)
                    or before.get("kind") not in {"regular", "absent"}
                    or after.get("kind") not in {"regular", "absent"}
                ):
                    unavailable.append(path)
                    continue
                before = self._state_with_blob(turn_id, before)
                after = self._state_with_blob(turn_id, after)
                try:
                    parent_chain = self._capture_parent_chain(path)
                except OSError:
                    unavailable.append(path)
                    continue
                before["parent_chain"] = parent_chain
                after["parent_chain"] = parent_chain
                if not self._blob_is_exact(before) or not self._blob_is_exact(after):
                    unavailable.append(path)
                    continue
                current = folded.get(path)
                if current is None:
                    folded[path] = {
                        "path": path,
                        "expected_current": after,
                        "target": before,
                        "rollback": after,
                        "turn_ids": [turn_id],
                        "state": "pending",
                        "error": None,
                    }
                    continue
                if not self._same_recorded_state(current["expected_current"], before):
                    discontinuous.append(path)
                    continue
                current["expected_current"] = after
                current["rollback"] = after
                current["turn_ids"].append(turn_id)

        unavailable = sorted(set(filter(None, unavailable)))
        discontinuous = sorted(set(filter(None, discontinuous)))
        if unavailable or discontinuous:
            return {
                "status": "unavailable",
                "actions": list(folded.values()),
                "conflicts": [],
                "unavailable": unavailable + discontinuous,
                "error": (
                    "one or more mutations are not recoverable"
                    if unavailable else "mutation journal is discontinuous"
                ),
            }
        actions = list(folded.values())
        conflicts = [
            action["path"] for action in actions
            if not self._state_matches(
                self._inspect_state(action["path"]), action["expected_current"],
            )
        ]
        if conflicts:
            return {
                "status": "blocked",
                "actions": actions,
                "conflicts": conflicts,
                "unavailable": [],
                "error": "current file state does not match the folded source",
            }
        return {
            "status": "ready",
            "actions": actions,
            "conflicts": [],
            "unavailable": [],
        }

    def _intent_path(self, turn_id: str, direction: str, key: str) -> Path:
        digest = hashlib.sha256(f"{direction}\0{key}".encode()).hexdigest()[:24]
        return turn_backup_dir(self.session_dir, turn_id) / "intents" / f"{digest}.json"

    def _rewind_intent_path(self, key: str) -> Path:
        digest = hashlib.sha256(f"rewind\0{key}".encode()).hexdigest()[:24]
        return session_backup_root(self.session_dir) / "intents" / f"{digest}.json"

    def _workspace_lock_path(self) -> Path:
        from openprogram.paths import get_state_dir

        root = get_state_dir() / "mutation-locks"
        root.mkdir(parents=True, exist_ok=True)
        return root / "history.lock"

    @contextmanager
    def _workspace_lock(self, _paths: list[str]):
        import fcntl

        with self._workspace_lock_path().open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _apply_state(
        self,
        path: str,
        state: dict,
        backup_dir: Path,
        transaction_id: str,
        expected_current: dict | None = None,
    ) -> str | None:
        target = Path(path)
        tmp_name = f".{target.name}.{transaction_id}.tmp"
        guard_name = f".{target.name}.{transaction_id}.guard"
        expected = expected_current or self._inspect_state(path)
        chain = expected.get("parent_chain") or self._capture_parent_chain(path)
        parent_descriptor = self._open_verified_parent(path, chain)
        try:
            if state.get("kind") == "regular":
                blob = Path(str(state.get("blob_path"))) \
                    if state.get("blob_path") else (
                        backup_dir / str(state.get("blob_ref") or "")
                    )
                if not blob.is_file():
                    raise OSError(f"missing recovery blob for {path}")
                if state.get("digest") and _digest(blob) != state.get("digest"):
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
                moved = self._inspect_state_at(parent_descriptor, guard_name)
                if not self._state_matches(moved, expected):
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
            guard_exists = self._inspect_state_at(
                parent_descriptor, guard_name,
            ).get("kind") != "absent"
            return str(target.parent / guard_name) if guard_exists else None
        finally:
            try:
                os.unlink(tmp_name, dir_fd=parent_descriptor)
            except FileNotFoundError:
                pass
            os.close(parent_descriptor)

    def _restore_changed_guard(
        self,
        action: dict,
        guard_path: str,
        transaction_id: str,
    ) -> None:
        target = Path(action["path"])
        guard = Path(guard_path)
        applied_name = f".{target.name}.{transaction_id}.applied"
        chain = action["expected_current"].get("parent_chain") \
            or self._capture_parent_chain(action["path"])
        descriptor = self._open_verified_parent(action["path"], chain)
        try:
            if self._inspect_state_at(descriptor, target.name).get("kind") != "absent":
                os.rename(
                    target.name, applied_name,
                    src_dir_fd=descriptor, dst_dir_fd=descriptor,
                )
                action["recovery_artifact"] = str(target.parent / applied_name)
            if self._inspect_state_at(descriptor, target.name).get("kind") != "absent":
                raise OSError(f"external writer recreated {target}")
            os.rename(
                guard.name, target.name,
                src_dir_fd=descriptor, dst_dir_fd=descriptor,
            )
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        try:
            descriptor = os.open(path, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError:
            pass

    @staticmethod
    def _intent_result(intent: dict) -> dict:
        committed = intent.get("status") == "committed"
        return {
            "status": intent.get("status", "error"),
            "transaction_id": intent.get("transaction_id"),
            "idempotency_key": intent.get("idempotency_key"),
            "restored_paths": [
                action["path"] for action in intent.get("actions", [])
            ] if committed else [],
            "conflicts": intent.get("conflicts", []),
            "unavailable": intent.get("unavailable", []),
            "error": intent.get("error"),
        }

    @staticmethod
    def _rewind_intent_result(intent: dict, *, replayed: bool = False) -> dict:
        committed = intent.get("status") == "committed"
        return {
            "status": intent.get("status", "error"),
            "transaction_id": intent.get("transaction_id"),
            "idempotency_key": intent.get("idempotency_key"),
            "restored_paths": [
                action["path"] for action in intent.get("actions", [])
            ] if committed else [],
            "conflicts": intent.get("conflicts", []),
            "unavailable": intent.get("unavailable", []),
            "error": intent.get("error"),
            "new_head_id": intent.get("target_head_id") if committed else None,
            "source_head_id": intent.get("expected_head_id"),
            "source_branch_id": intent.get("source_branch_id"),
            "target_branch_id": intent.get("target_branch_id"),
            "target_msg_id": intent.get("target_msg_id"),
            "user_text": intent.get("user_text", ""),
            "turn_ids": intent.get("turn_ids", []),
            "head_changed": committed and not replayed,
            "replayed": replayed,
        }

    def read_rewind_intent(self, key: str) -> dict | None:
        path = self._rewind_intent_path(key)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def _recover_rewind_intent(
        self,
        intent_path: Path,
        *,
        get_head,
        compare_and_set_head,
    ) -> dict:
        try:
            initial = json.loads(intent_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"status": "error", "error": "invalid rewind intent"}
        paths = [action["path"] for action in initial.get("actions", [])]
        with self._workspace_lock(paths):
            try:
                intent = json.loads(intent_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {"status": "error", "error": "invalid rewind intent"}
            if intent.get("status") in {
                "committed", "rolled_back", "recovery_required", "aborted",
            }:
                return self._rewind_intent_result(intent, replayed=True)
            actions = intent.get("actions") or []
            head = get_head()
            expected_head = intent.get("expected_head_id")
            target_head = intent.get("target_head_id")
            states = []
            for action in actions:
                actual = self._inspect_state(action["path"])
                if self._state_matches(actual, action["rollback"]):
                    states.append("source")
                elif self._state_matches(actual, action["target"]):
                    states.append("target")
                else:
                    states.append("external")
            if all(state == "target" for state in states) and head == target_head:
                intent["status"] = "committed"
                intent["error"] = None
                manifest.save(intent_path, intent)
                return self._rewind_intent_result(intent, replayed=True)
            if head not in {expected_head, target_head} or "external" in states:
                intent["status"] = "recovery_required"
                intent["error"] = "external state prevents deterministic recovery"
                manifest.save(intent_path, intent)
                return self._rewind_intent_result(intent, replayed=True)
            recovery_required = False
            for action, state_name in reversed(list(zip(actions, states))):
                if state_name == "source":
                    action["state"] = "rolled_back"
                    continue
                try:
                    action["state"] = "rolling_back"
                    manifest.save(intent_path, intent)
                    rollback_guard = self._apply_state(
                        action["path"], action["rollback"], self.session_dir,
                        str(intent.get("transaction_id") or "recovery"),
                        action["target"],
                    )
                    if rollback_guard:
                        action["rollback_guard_path"] = rollback_guard
                    if not self._state_matches(
                        self._inspect_state(action["path"]), action["rollback"],
                    ):
                        raise OSError("rollback verification failed")
                    action["state"] = "rolled_back"
                    manifest.save(intent_path, intent)
                except Exception as exc:
                    recovery_required = True
                    action["error"] = str(exc)
            if not recovery_required and head == target_head:
                if not compare_and_set_head(intent, target_head, expected_head):
                    recovery_required = True
            intent["status"] = (
                "recovery_required" if recovery_required else "rolled_back"
            )
            intent["error"] = (
                "automatic rollback could not complete"
                if recovery_required else "interrupted rewind rolled back"
            )
            manifest.save(intent_path, intent)
            return self._rewind_intent_result(intent, replayed=True)

    def recover_rewind_intents(self, *, get_head, compare_and_set_head) -> list[dict]:
        root = session_backup_root(self.session_dir) / "intents"
        if not root.is_dir():
            return []
        results = []
        for path in sorted(root.glob("*.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if value.get("status") in {"prepared", "applying"}:
                results.append(self._recover_rewind_intent(
                    path,
                    get_head=get_head,
                    compare_and_set_head=compare_and_set_head,
                ))
        return results

    @staticmethod
    def _plan_hash(actions: list[dict]) -> str:
        return "sha256:" + hashlib.sha256(
            json.dumps(actions, sort_keys=True).encode(),
        ).hexdigest()

    @staticmethod
    def rewind_plan_hash(
        turn_ids: list[str],
        expected_head_id: str | None,
        target_head_id: str | None,
        actions: list[dict],
    ) -> str:
        return "sha256:" + hashlib.sha256(
            json.dumps({
                "turn_ids": turn_ids,
                "expected_head_id": expected_head_id,
                "target_head_id": target_head_id,
                "actions": actions,
            }, sort_keys=True).encode(),
        ).hexdigest()

    def apply_history_operation(
        self,
        turn_id: str,
        direction: str,
        *,
        idempotency_key: str | None = None,
    ) -> dict:
        key = idempotency_key or uuid.uuid4().hex
        transaction_id = f"{direction}_{uuid.uuid4().hex}"
        intent_path = self._intent_path(turn_id, direction, key)
        if intent_path.exists():
            try:
                existing = json.loads(intent_path.read_text(encoding="utf-8"))
                if existing.get("status") in {
                    "committed", "rolled_back", "recovery_required", "aborted",
                }:
                    return self._intent_result(existing)
                return self._intent_result({
                    **existing,
                    "status": "recovery_required",
                    "error": "incomplete durable intent requires recovery",
                })
            except (OSError, json.JSONDecodeError):
                pass
        plan = self.plan_history_operation(turn_id, direction)
        if plan.get("status") != "ready":
            return {
                **plan,
                "transaction_id": None,
                "restored_paths": [],
            }
        intent = {
            "version": 1,
            "transaction_id": transaction_id,
            "idempotency_key": key,
            "turn_id": turn_id,
            "direction": direction,
            "plan_hash": self._plan_hash(plan["actions"]),
            "status": "prepared",
            "actions": plan["actions"],
            "conflicts": [],
            "unavailable": [],
            "error": None,
        }
        manifest.save(intent_path, intent)
        backup_dir = turn_backup_dir(self.session_dir, turn_id)
        paths = [action["path"] for action in intent["actions"]]
        with self._workspace_lock(paths):
            current_plan = self.plan_history_operation(turn_id, direction)
            if current_plan.get("status") != "ready":
                intent.update({
                    "status": "aborted",
                    "conflicts": current_plan.get("conflicts", []),
                    "unavailable": current_plan.get("unavailable", []),
                    "error": current_plan.get("error"),
                })
                manifest.save(intent_path, intent)
                return self._intent_result(intent)
            if self._plan_hash(current_plan["actions"]) != intent["plan_hash"]:
                intent.update({"status": "aborted", "error": "stale_plan"})
                manifest.save(intent_path, intent)
                return self._intent_result(intent)
            intent["status"] = "applying"
            manifest.save(intent_path, intent)
            touched: list[dict] = []
            try:
                for action in intent["actions"]:
                    touched.append(action)
                    if not self._state_matches(
                        self._inspect_state(action["path"]),
                        action["expected_current"],
                    ):
                        raise OSError(f"stale current state for {action['path']}")
                    guard_path = self._apply_state(
                        action["path"], action["target"], backup_dir, transaction_id,
                        action["expected_current"],
                    )
                    if guard_path:
                        action["guard_path"] = guard_path
                    actual = self._inspect_state(action["path"])
                    if not self._state_matches(actual, action["target"]):
                        raise OSError(f"verification failed for {action['path']}")
                    if guard_path and not self._state_matches(
                        self._inspect_state(guard_path), action["rollback"],
                    ):
                        self._restore_changed_guard(
                            action, guard_path, transaction_id,
                        )
                        raise OSError(
                            f"external writer changed moved inode for {action['path']}",
                        )
                    action["state"] = "verified"
                    manifest.save(intent_path, intent)
            except Exception as exc:
                recovery_required = False
                for action in reversed(touched):
                    try:
                        actual = self._inspect_state(action["path"])
                        if self._state_matches(actual, action["rollback"]):
                            action["state"] = "rolled_back"
                            continue
                        if not self._state_matches(actual, action["target"]):
                            recovery_required = True
                            action["error"] = "external change prevents rollback"
                            continue
                        rollback_guard = self._apply_state(
                            action["path"], action["rollback"], backup_dir,
                            transaction_id, action["target"],
                        )
                        if rollback_guard:
                            action["rollback_guard_path"] = rollback_guard
                        if not self._state_matches(
                            self._inspect_state(action["path"]), action["rollback"],
                        ):
                            raise OSError("rollback verification failed")
                        action["state"] = "rolled_back"
                    except Exception as rollback_error:
                        recovery_required = True
                        action["error"] = str(rollback_error)
                intent["status"] = (
                    "recovery_required" if recovery_required else "rolled_back"
                )
                intent["error"] = str(exc)
                manifest.save(intent_path, intent)
                return self._intent_result(intent)
            intent["status"] = "committed"
            manifest.save(intent_path, intent)
        return self._intent_result(intent)

    def apply_rewind_operation(
        self,
        turn_ids: list[str],
        *,
        expected_head_id: str | None,
        target_head_id: str | None,
        get_head,
        compare_and_set_head,
        idempotency_key: str | None = None,
        target_msg_id: str | None = None,
        user_text: str = "",
        source_branch_id: str | None = None,
        target_branch_id: str | None = None,
        expected_plan_hash: str | None = None,
    ) -> dict:
        """Apply one folded file plan and move HEAD only after verification."""
        key = idempotency_key or uuid.uuid4().hex
        intent_path = self._rewind_intent_path(key)
        if intent_path.exists():
            try:
                existing = json.loads(intent_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing = None
            if isinstance(existing, dict):
                if (
                    target_msg_id != existing.get("target_msg_id")
                    or (
                        expected_plan_hash
                        and expected_plan_hash != existing.get("preview_plan_hash")
                    )
                ):
                    return {
                        "status": "idempotency_conflict",
                        "transaction_id": existing.get("transaction_id"),
                        "restored_paths": [],
                        "conflicts": [],
                        "unavailable": [],
                        "error": "idempotency key is bound to another rewind request",
                        "new_head_id": None,
                        "head_changed": False,
                    }
                if existing.get("status") in {
                    "committed", "rolled_back", "recovery_required", "aborted",
                }:
                    return self._rewind_intent_result(existing, replayed=True)
                return self._recover_rewind_intent(
                    intent_path,
                    get_head=get_head,
                    compare_and_set_head=(
                        lambda _intent, expected, target:
                        compare_and_set_head(expected, target)
                    ),
                )

        plan = self.plan_rewind_operation(turn_ids)
        if plan.get("status") != "ready":
            return {
                **plan,
                "transaction_id": None,
                "restored_paths": [],
                "new_head_id": None,
                "head_changed": False,
            }
        transaction_id = f"rewind_{uuid.uuid4().hex}"
        plan_payload = {
            "turn_ids": turn_ids,
            "expected_head_id": expected_head_id,
            "target_head_id": target_head_id,
            "actions": plan["actions"],
        }
        preview_plan_hash = self.rewind_plan_hash(
            turn_ids, expected_head_id, target_head_id, plan["actions"],
        )
        if expected_plan_hash and expected_plan_hash != preview_plan_hash:
            return {
                "status": "aborted",
                "transaction_id": None,
                "restored_paths": [],
                "conflicts": [],
                "unavailable": [],
                "error": "stale_plan",
                "new_head_id": None,
                "head_changed": False,
            }
        intent = {
            "version": 1,
            "transaction_id": transaction_id,
            "idempotency_key": key,
            **plan_payload,
            "target_msg_id": target_msg_id,
            "user_text": user_text,
            "source_branch_id": source_branch_id,
            "target_branch_id": target_branch_id,
            "preview_plan_hash": preview_plan_hash,
            "plan_hash": "sha256:" + hashlib.sha256(
                json.dumps(plan_payload, sort_keys=True).encode(),
            ).hexdigest(),
            "status": "prepared",
            "conflicts": [],
            "unavailable": [],
            "error": None,
        }
        manifest.save(intent_path, intent)
        paths = [action["path"] for action in intent["actions"]]
        with self._workspace_lock(paths):
            if get_head() != expected_head_id:
                intent.update({"status": "aborted", "error": "stale_head"})
                manifest.save(intent_path, intent)
                return self._rewind_intent_result(intent)
            current_plan = self.plan_rewind_operation(turn_ids)
            current_payload = {
                "turn_ids": turn_ids,
                "expected_head_id": expected_head_id,
                "target_head_id": target_head_id,
                "actions": current_plan.get("actions", []),
            }
            current_hash = "sha256:" + hashlib.sha256(
                json.dumps(current_payload, sort_keys=True).encode(),
            ).hexdigest()
            if current_plan.get("status") != "ready":
                intent.update({
                    "status": "aborted",
                    "conflicts": current_plan.get("conflicts", []),
                    "unavailable": current_plan.get("unavailable", []),
                    "error": current_plan.get("error"),
                })
                manifest.save(intent_path, intent)
                return self._rewind_intent_result(intent)
            if current_hash != intent["plan_hash"]:
                intent.update({"status": "aborted", "error": "stale_plan"})
                manifest.save(intent_path, intent)
                return self._rewind_intent_result(intent)
            intent["status"] = "applying"
            manifest.save(intent_path, intent)
            touched: list[dict] = []
            head_moved = False
            try:
                for action in intent["actions"]:
                    touched.append(action)
                    if not self._state_matches(
                        self._inspect_state(action["path"]),
                        action["expected_current"],
                    ):
                        raise OSError(f"stale current state for {action['path']}")
                    action["state"] = "applying"
                    manifest.save(intent_path, intent)
                    guard_path = self._apply_state(
                        action["path"], action["target"], self.session_dir,
                        transaction_id, action["expected_current"],
                    )
                    if guard_path:
                        action["guard_path"] = guard_path
                    action["state"] = "applied"
                    action["applied_digest"] = action["target"].get("digest")
                    manifest.save(intent_path, intent)
                    if not self._state_matches(
                        self._inspect_state(action["path"]), action["target"],
                    ):
                        raise OSError(f"verification failed for {action['path']}")
                    if guard_path and not self._state_matches(
                        self._inspect_state(guard_path), action["rollback"],
                    ):
                        self._restore_changed_guard(
                            action, guard_path, transaction_id,
                        )
                        raise OSError(
                            f"external writer changed moved inode for {action['path']}",
                        )
                    action["state"] = "verified"
                    manifest.save(intent_path, intent)
                if not compare_and_set_head(expected_head_id, target_head_id):
                    raise OSError("stale_head")
                head_moved = True
                intent["status"] = "committed"
                manifest.save(intent_path, intent)
                return self._rewind_intent_result(intent)
            except Exception as exc:
                recovery_required = False
                if head_moved and not compare_and_set_head(
                    target_head_id, expected_head_id,
                ):
                    recovery_required = True
                for action in reversed(touched):
                    try:
                        actual = self._inspect_state(action["path"])
                        if self._state_matches(actual, action["rollback"]):
                            action["state"] = "rolled_back"
                            continue
                        if not self._state_matches(actual, action["target"]):
                            recovery_required = True
                            action["error"] = "external change prevents rollback"
                            continue
                        rollback_guard = self._apply_state(
                            action["path"], action["rollback"], self.session_dir,
                            transaction_id, action["target"],
                        )
                        if rollback_guard:
                            action["rollback_guard_path"] = rollback_guard
                        if not self._state_matches(
                            self._inspect_state(action["path"]), action["rollback"],
                        ):
                            raise OSError("rollback verification failed")
                        action["state"] = "rolled_back"
                    except Exception as rollback_error:
                        recovery_required = True
                        action["error"] = str(rollback_error)
                intent["status"] = (
                    "recovery_required" if recovery_required else "rolled_back"
                )
                intent["error"] = str(exc)
                manifest.save(intent_path, intent)
                return self._rewind_intent_result(intent)

    def restore_turn(self, turn_id: str) -> list[str]:
        """Legacy best-effort restore; task B replaces this execution path."""
        restored: list[str] = []
        manifest_path = turn_manifest_path(self.session_dir, turn_id)
        backup_dir = turn_backup_dir(self.session_dir, turn_id)
        for backup_name, entry in manifest.entries(manifest_path):
            if entry.get("status") == "aborted":
                continue
            original = entry.get("path") or ""
            pre_existing = bool(entry.get("pre_existing"))
            if not original:
                continue
            try:
                if not pre_existing:
                    if Path(original).exists():
                        Path(original).unlink()
                        restored.append(original)
                    continue
                source = backup_dir / backup_name
                if not source.exists():
                    continue
                destination = Path(original)
                destination.parent.mkdir(parents=True, exist_ok=True)
                tmp = destination.with_suffix(destination.suffix + ".restore.tmp")
                shutil.copy2(source, tmp)
                tmp.replace(destination)
                restored.append(original)
            except OSError:
                continue
        return restored

    def list_backed_paths(self, turn_id: str) -> list[str]:
        return [
            entry.get("path", "")
            for _name, entry in manifest.entries(
                turn_manifest_path(self.session_dir, turn_id),
            )
            if entry.get("path") and entry.get("status") != "aborted"
        ]


BackupStore = CheckpointStore
