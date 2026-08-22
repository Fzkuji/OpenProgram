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
from .paths import path_basename, turn_backup_dir, turn_manifest_path


_STATS_MAX_BYTES = 1024 * 1024


class MutationJournalError(RuntimeError):
    """A trusted mutation could not be recorded safely."""


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
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
        target = Path(path)
        try:
            info = os.lstat(target)
        except FileNotFoundError:
            return {"kind": "absent"}
        if not stat.S_ISREG(info.st_mode):
            return {"kind": _file_kind(info.st_mode)}
        if info.st_nlink != 1:
            return {"kind": "hardlink", "links": info.st_nlink}
        return {
            "kind": "regular",
            "digest": _digest(target),
            "mode": f"{stat.S_IMODE(info.st_mode):04o}",
            "size": info.st_size,
        }

    @staticmethod
    def _state_matches(actual: dict, expected: dict) -> bool:
        if actual.get("kind") != expected.get("kind"):
            return False
        if expected.get("kind") == "regular":
            return actual.get("digest") == expected.get("digest")
        return expected.get("kind") == "absent"

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

    def _intent_path(self, turn_id: str, direction: str, key: str) -> Path:
        digest = hashlib.sha256(f"{direction}\0{key}".encode()).hexdigest()[:24]
        return turn_backup_dir(self.session_dir, turn_id) / "intents" / f"{digest}.json"

    def _workspace_lock_path(self) -> Path:
        root = self.session_dir.parent / ".mutation-locks"
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
    ) -> None:
        target = Path(path)
        if state.get("kind") == "absent":
            try:
                target.unlink()
            except FileNotFoundError:
                pass
            self._fsync_directory(target.parent)
            return
        blob = backup_dir / str(state.get("blob_ref") or "")
        if not blob.is_file():
            raise OSError(f"missing recovery blob for {path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.parent / f".{target.name}.{transaction_id}.tmp"
        try:
            shutil.copy2(blob, tmp)
            with tmp.open("rb") as handle:
                os.fsync(handle.fileno())
            os.chmod(tmp, int(str(state.get("mode") or "0644"), 8))
            os.replace(tmp, target)
            self._fsync_directory(target.parent)
        finally:
            if tmp.exists():
                tmp.unlink()

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
            "restored_paths": [
                action["path"] for action in intent.get("actions", [])
            ] if committed else [],
            "conflicts": intent.get("conflicts", []),
            "unavailable": intent.get("unavailable", []),
            "error": intent.get("error"),
        }

    @staticmethod
    def _plan_hash(actions: list[dict]) -> str:
        return "sha256:" + hashlib.sha256(
            json.dumps(actions, sort_keys=True).encode(),
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
                    self._apply_state(
                        action["path"], action["target"], backup_dir, transaction_id,
                    )
                    actual = self._inspect_state(action["path"])
                    if not self._state_matches(actual, action["target"]):
                        raise OSError(f"verification failed for {action['path']}")
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
                        self._apply_state(
                            action["path"], action["rollback"], backup_dir,
                            transaction_id,
                        )
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
