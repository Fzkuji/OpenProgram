"""Per-session exact file-mutation journal and recovery snapshots."""
from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import documents, file_apply, file_state, journal, manifest, planning, transactions
from .capture import MutationJournalError as MutationJournalError
from .recovery_records import (
    invalid_history_result, invalid_rewind_result, read_history_record, read_rewind_record,
)
from .paths import (
    session_backup_root,
    turn_backup_dir,
)


class CheckpointStore:
    def __init__(
        self, session_dir: Path | None = None, *, recovery_root: Path | None = None,
    ):
        if session_dir is None and recovery_root is None:
            raise TypeError("session_dir or recovery_root is required")
        self.session_dir = Path(session_dir) if session_dir is not None else None
        self.recovery_root = Path(recovery_root) if recovery_root is not None else None


    def backup_before_edit(
        self,
        turn_id: str,
        abs_path: str,
        *,
        content_src: str | Path | None = None,
        project_locator: dict | None = None,
    ) -> None:
        return journal.backup_before_edit(self.session_dir, turn_id, abs_path, content_src=content_src, project_locator=project_locator)

    def commit_after_edit(
        self, turn_id: str, abs_path: str, *, operation: str | None = None,
    ) -> None:
        return journal.commit_after_edit(self.session_dir, turn_id, abs_path, operation=operation)


    def abort_edit(self, turn_id: str, abs_path: str, error: str | None = None) -> None:
        return journal.abort_edit(self.session_dir, turn_id, abs_path, error)

    def list_mutations(self, turn_id: str) -> list[dict]:
        return journal.list_mutations(self.session_dir, turn_id)

    def list_file_history(self, turn_id: str) -> list[dict]:
        return journal.list_file_history(self.session_dir, turn_id)


    def plan_history_operation(self, turn_id: str, direction: str) -> dict:
        return planning.plan_history_operation(self.session_dir, turn_id, direction)

    def plan_rewind_operation(
        self, turn_ids: list[str], direction: str = "revert",
    ) -> dict:
        return planning.plan_rewind_operation(self.session_dir, turn_ids, direction)

    def _intent_path(self, turn_id: str, direction: str, key: str) -> Path:
        digest = hashlib.sha256(f"{direction}\0{key}".encode()).hexdigest()[:24]
        return turn_backup_dir(self.session_dir, turn_id) / "intents" / f"{digest}.json"

    def _rewind_intent_path(self, key: str) -> Path:
        digest = hashlib.sha256(f"rewind\0{key}".encode()).hexdigest()[:24]
        return session_backup_root(self.session_dir) / "intents" / f"{digest}.json"

    @contextmanager
    def _rewind_intent_lock(self, key: str):
        from openprogram import _compat as fcntl

        digest = hashlib.sha256(f"rewind\0{key}".encode()).hexdigest()[:24]
        root = session_backup_root(self.session_dir) / "intent-locks"
        root.mkdir(parents=True, exist_ok=True)
        with (root / f"{digest}.lock").open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


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
            "error_code": intent.get("error_code") or (
                "RECOVERY_REQUIRED" if intent.get("status") == "recovery_required" else None
            ),
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
            value = read_rewind_record(path)
        except FileNotFoundError:
            return None
        if value is None:
            return invalid_rewind_result(path)
        if value["status"] == "recovery_required" and not value.get("error_code"):
            value["error_code"] = "RECOVERY_REQUIRED"
        return value

    def read_history_intent(
        self, turn_id: str, direction: str, key: str,
    ) -> dict | None:
        """Read one single-turn history receipt without applying or rewriting it."""
        path = self._intent_path(turn_id, direction, key)
        try:
            value = read_history_record(path)
        except FileNotFoundError:
            return None
        if value is None:
            return invalid_history_result(path)
        if value["status"] == "recovery_required" and not value.get("error_code"):
            value["error_code"] = "RECOVERY_REQUIRED"
        return value

    def _recover_rewind_intent(
        self,
        intent_path: Path,
        *,
        get_head,
        compare_and_set_head,
    ) -> dict:
        initial = read_rewind_record(intent_path)
        if initial is None:
            return invalid_rewind_result(intent_path)
        paths = [action["path"] for action in initial["actions"]]
        with transactions._workspace_lock(paths):
            intent = read_rewind_record(intent_path)
            if intent is None:
                return invalid_rewind_result(intent_path)
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
                actual = file_state._inspect_state(action["path"])
                if file_state._state_matches(actual, action["rollback"]):
                    states.append("source")
                elif file_state._state_matches(actual, action["target"]):
                    states.append("target")
                else:
                    states.append("external")
            if all(state == "target" for state in states) and head == target_head:
                if expected_head == target_head and not compare_and_set_head(
                    intent, expected_head, target_head,
                ):
                    intent["status"] = "recovery_required"
                    intent["error_code"] = "RECOVERY_REQUIRED"
                    intent["error"] = "same-head transaction finalization failed"
                    manifest.save(intent_path, intent)
                    return self._rewind_intent_result(intent, replayed=True)
                intent["status"] = "committed"
                intent["error"] = None
                manifest.save(intent_path, intent)
                return self._rewind_intent_result(intent, replayed=True)
            if head not in {expected_head, target_head} or "external" in states:
                intent["status"] = "recovery_required"
                intent["error_code"] = "RECOVERY_REQUIRED"
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
                    rollback_guard = file_apply._apply_state(
                        action["path"], action["rollback"], self.session_dir,
                        str(intent.get("transaction_id") or "recovery") + "_rollback",
                        action["target"],
                    )
                    if rollback_guard:
                        action["rollback_guard_path"] = rollback_guard
                    if not file_state._state_matches(
                        file_state._inspect_state(action["path"]), action["rollback"],
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
            if recovery_required:
                intent["error_code"] = "RECOVERY_REQUIRED"
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
            value = read_rewind_record(path)
            if value is None:
                results.append(invalid_rewind_result(path))
                continue
            if value["status"] in {"prepared", "applying"}:
                results.append(self._recover_rewind_intent(
                    path,
                    get_head=get_head,
                    compare_and_set_head=compare_and_set_head,
                ))
        return results

    def recover_history_intents(self) -> list[dict]:
        """Terminalize ordinary history intents left during a crash.

        A single-turn intent has no separate recovery coordinator.  Startup
        therefore preserves its manifest and records an explicit recovery
        state instead of exposing it forever as an in-progress operation.
        """
        results = []
        roots = (
            session_backup_root(self.session_dir),
            Path(self.session_dir) / "file_backups",
        )
        paths = sorted({path for root in roots for path in root.glob("*/intents/*.json")})
        for path in paths:
            intent = read_history_record(path)
            if intent is None:
                results.append(invalid_history_result(path))
                continue
            if intent["status"] not in {"prepared", "applying"}:
                continue
            intent["status"] = "recovery_required"
            intent["error_code"] = "RECOVERY_REQUIRED"
            intent["error"] = "incomplete history intent requires explicit recovery"
            manifest.save(path, intent)
            results.append(transactions._intent_result(intent))
        return results


    def publish_document(
        self, operation_id: str, target_path: str | Path, source_path: str | Path,
        *, expected_revision: str | None = None, expected_mtime: int | float | None = None,
        fingerprint: str, metadata: dict | None = None,
    ) -> dict:
        return documents.publish_document(
            self.recovery_root, operation_id, target_path, source_path,
            expected_revision=expected_revision, expected_mtime=expected_mtime,
            fingerprint=fingerprint, metadata=metadata,
        )


    def read_document_operation(self, operation_id: str) -> dict:
        return documents.read_document_operation(self.recovery_root, operation_id)

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
        try:
            existing = read_history_record(intent_path)
        except FileNotFoundError:
            existing = None
        else:
            if existing is None:
                return invalid_history_result(intent_path)
            if existing["status"] == "recovery_required" and not existing.get("error_code"):
                existing["error_code"] = "RECOVERY_REQUIRED"
            if existing["status"] in {
                "committed", "rolled_back", "recovery_required", "aborted",
            }:
                return transactions._intent_result(existing)
            return transactions._intent_result({
                **existing,
                "status": "recovery_required",
                "error_code": "RECOVERY_REQUIRED",
                "error": "incomplete durable intent requires recovery",
            })
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
            "plan_hash": transactions._plan_hash(plan["actions"]),
            "status": "prepared",
            "actions": plan["actions"],
            "conflicts": [],
            "unavailable": [],
            "error": None,
        }
        manifest.save(intent_path, intent)
        backup_dir = turn_backup_dir(self.session_dir, turn_id)
        current_plan = self.plan_history_operation(turn_id, direction)
        if current_plan.get("status") != "ready":
            intent.update({
                "status": "aborted",
                "conflicts": current_plan.get("conflicts", []),
                "unavailable": current_plan.get("unavailable", []),
                "error": current_plan.get("error"),
            })
            manifest.save(intent_path, intent)
            return transactions._intent_result(intent)
        if transactions._plan_hash(current_plan["actions"]) != intent["plan_hash"]:
            intent.update({"status": "aborted", "error": "stale_plan"})
            manifest.save(intent_path, intent)
            return transactions._intent_result(intent)
        return transactions._execute_history_intent(
            intent, intent_path, backup_dir,
            preflight=lambda: self.plan_history_operation(turn_id, direction),
        )

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
        custom_actions: list[dict] | None = None,
        forward_meta_update: dict | None = None,
        rollback_meta_update: dict | None = None,
    ) -> dict:
        key = idempotency_key or uuid.uuid4().hex
        with self._rewind_intent_lock(key):
            return self._apply_rewind_operation_locked(
                turn_ids,
                expected_head_id=expected_head_id,
                target_head_id=target_head_id,
                get_head=get_head,
                compare_and_set_head=compare_and_set_head,
                idempotency_key=key,
                target_msg_id=target_msg_id,
                user_text=user_text,
                source_branch_id=source_branch_id,
                target_branch_id=target_branch_id,
                expected_plan_hash=expected_plan_hash,
                custom_actions=custom_actions,
                forward_meta_update=forward_meta_update,
                rollback_meta_update=rollback_meta_update,
            )

    def _apply_rewind_operation_locked(
        self,
        turn_ids: list[str],
        *,
        expected_head_id: str | None,
        target_head_id: str | None,
        get_head,
        compare_and_set_head,
        idempotency_key: str,
        target_msg_id: str | None,
        user_text: str,
        source_branch_id: str | None,
        target_branch_id: str | None,
        expected_plan_hash: str | None,
        custom_actions: list[dict] | None,
        forward_meta_update: dict | None,
        rollback_meta_update: dict | None,
    ) -> dict:
        """Apply one folded file plan and move HEAD only after verification."""
        key = idempotency_key
        intent_path = self._rewind_intent_path(key)
        if intent_path.exists():
            existing = read_rewind_record(intent_path)
            if existing is None:
                return {**invalid_rewind_result(intent_path),
                        "head_changed": False, "new_head_id": None, "replayed": True}
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

        plan = (
            transactions._validate_custom_history_actions(custom_actions)
            if custom_actions is not None
            else self.plan_rewind_operation(turn_ids)
        )
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
            "forward_meta_update": forward_meta_update,
            "rollback_meta_update": rollback_meta_update,
            "conflicts": [],
            "unavailable": [],
            "error": None,
        }
        manifest.save(intent_path, intent)
        paths = [action["path"] for action in intent["actions"]]
        with transactions._workspace_lock(paths):
            if get_head() != expected_head_id:
                intent.update({"status": "aborted", "error": "stale_head"})
                manifest.save(intent_path, intent)
                return self._rewind_intent_result(intent)
            current_plan = (
                transactions._validate_custom_history_actions(custom_actions)
                if custom_actions is not None
                else self.plan_rewind_operation(turn_ids)
            )
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
                    if not file_state._state_matches(
                        file_state._inspect_state(action["path"]),
                        action["expected_current"],
                    ):
                        raise OSError(f"stale current state for {action['path']}")
                    action["state"] = "applying"
                    manifest.save(intent_path, intent)
                    guard_path = file_apply._apply_state(
                        action["path"], action["target"], self.session_dir,
                        transaction_id, action["expected_current"],
                    )
                    if guard_path:
                        action["guard_path"] = guard_path
                    action["state"] = "applied"
                    action["applied_digest"] = action["target"].get("digest")
                    manifest.save(intent_path, intent)
                    if not file_state._state_matches(
                        file_state._inspect_state(action["path"]), action["target"],
                    ):
                        raise OSError(f"verification failed for {action['path']}")
                    if guard_path and not file_state._state_matches(
                        file_state._inspect_state(guard_path), action["rollback"],
                    ):
                        file_apply._restore_changed_guard(
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
                for action in intent["actions"]:
                    if not file_state._state_matches(
                        file_state._inspect_state(action["path"]), action["target"],
                    ):
                        raise OSError(
                            f"external change after apply for {action['path']}",
                        )
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
                        actual = file_state._inspect_state(action["path"])
                        if file_state._state_matches(actual, action["rollback"]):
                            action["state"] = "rolled_back"
                            continue
                        if not file_state._state_matches(actual, action["target"]):
                            recovery_required = True
                            action["error"] = "external change prevents rollback"
                            continue
                        rollback_guard = file_apply._apply_state(
                            action["path"], action["rollback"], self.session_dir,
                            transaction_id + "_rollback", action["target"],
                        )
                        if rollback_guard:
                            action["rollback_guard_path"] = rollback_guard
                        if not file_state._state_matches(
                            file_state._inspect_state(action["path"]), action["rollback"],
                        ):
                            raise OSError("rollback verification failed")
                        action["state"] = "rolled_back"
                    except Exception as rollback_error:
                        recovery_required = True
                        action["error"] = str(rollback_error)
                intent["status"] = (
                    "recovery_required" if recovery_required else "rolled_back"
                )
                if recovery_required:
                    intent["error_code"] = "RECOVERY_REQUIRED"
                intent["error"] = str(exc)
                manifest.save(intent_path, intent)
                return self._rewind_intent_result(intent)

    def restore_turn(self, turn_id: str) -> list[str]:
        return journal.restore_turn(self.session_dir, turn_id)

    def list_backed_paths(self, turn_id: str) -> list[str]:
        return journal.list_backed_paths(self.session_dir, turn_id)


BackupStore = CheckpointStore
