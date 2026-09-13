"""Shared workspace locking and guarded history transaction execution."""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from pathlib import Path

from . import file_apply, file_state, manifest


def _workspace_lock_path() -> Path:
    from openprogram.paths import get_state_dir

    root = get_state_dir() / "mutation-locks"
    root.mkdir(parents=True, exist_ok=True)
    return root / "history.lock"



@contextmanager
def _workspace_lock(_paths: list[str]):
    from openprogram import _compat as fcntl

    with _workspace_lock_path().open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)



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
        "error_code": intent.get("error_code"),
        "error": intent.get("error"),
    }



def _validate_custom_history_actions(actions: list[dict]) -> dict:
    conflicts = []
    unavailable = []
    for action in actions:
        path = action.get("path") or ""
        if not path:
            unavailable.append(path)
            continue
        if not file_state._state_matches(
            file_state._inspect_state(path), action.get("expected_current") or {},
        ):
            conflicts.append(path)
            continue
        for state in (action.get("target") or {}, action.get("rollback") or {}):
            if not file_state._blob_is_exact(state):
                unavailable.append(path)
                break
    if unavailable:
        return {
            "status": "unavailable", "actions": actions,
            "conflicts": conflicts, "unavailable": sorted(set(unavailable)),
            "error": "custom history target is unavailable",
        }
    if conflicts:
        return {
            "status": "blocked", "actions": actions,
            "conflicts": sorted(set(conflicts)), "unavailable": [],
            "error": "current workspace does not match the source branch",
        }
    return {
        "status": "ready", "actions": actions,
        "conflicts": [], "unavailable": [],
    }



def _plan_hash(actions: list[dict]) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(actions, sort_keys=True).encode(),
    ).hexdigest()



def _execute_history_intent(
    intent: dict, intent_path: Path, backup_dir: Path, *, preflight=None,
) -> dict:
    """Apply a prepared file intent using the shared guarded transaction."""
    paths = [action["path"] for action in intent.get("actions", [])]
    with _workspace_lock(paths):
        if preflight is not None:
            current_plan = preflight()
            if current_plan.get("status") != "ready":
                intent.update({
                    "status": "aborted",
                    "conflicts": current_plan.get("conflicts", []),
                    "unavailable": current_plan.get("unavailable", []),
                    "error": current_plan.get("error"),
                })
                manifest.save(intent_path, intent)
                return _intent_result(intent)
            if _plan_hash(current_plan["actions"]) != intent.get("plan_hash"):
                intent.update({"status": "aborted", "error": "stale_plan"})
                manifest.save(intent_path, intent)
                return _intent_result(intent)
        conflicts = []
        unavailable = []
        for action in intent["actions"]:
            if not file_state._state_matches(
                file_state._inspect_state(action["path"]),
                action.get("expected_current") or {},
            ):
                conflicts.append(action["path"])
            for state in (action.get("target") or {}, action.get("rollback") or {}):
                if state.get("kind") != "regular":
                    continue
                blob = Path(str(state.get("blob_path") or backup_dir / str(state.get("blob_ref") or "")))
                if not blob.is_file() or (state.get("digest") and file_state._digest(blob) != state.get("digest")):
                    unavailable.append(action["path"])
        current = {
            "status": "unavailable" if unavailable else "blocked" if conflicts else "ready",
            "conflicts": sorted(set(conflicts)), "unavailable": sorted(set(unavailable)),
            "error": "custom history target is unavailable" if unavailable else "current file state does not match the recorded source",
        }
        if current.get("status") != "ready":
            intent.update({
                "status": "aborted",
                "conflicts": current.get("conflicts", []),
                "unavailable": current.get("unavailable", []),
                "error": current.get("error"),
            })
            manifest.save(intent_path, intent)
            return _intent_result(intent)
        intent["status"] = "applying"
        manifest.save(intent_path, intent)
        touched: list[dict] = []
        try:
            for action in intent["actions"]:
                touched.append(action)
                if not file_state._state_matches(
                    file_state._inspect_state(action["path"]),
                    action["expected_current"],
                ):
                    raise OSError(f"stale current state for {action['path']}")
                guard_path = file_apply._apply_state(
                    action["path"], action["target"], backup_dir,
                    intent["transaction_id"], action["expected_current"],
                )
                if guard_path:
                    action["guard_path"] = guard_path
                if not file_state._state_matches(
                    file_state._inspect_state(action["path"]), action["target"],
                ):
                    raise OSError(f"verification failed for {action['path']}")
                if guard_path and not file_state._state_matches(
                    file_state._inspect_state(guard_path), action["rollback"],
                ):
                    file_apply._restore_changed_guard(
                        action, guard_path, intent["transaction_id"],
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
                    actual = file_state._inspect_state(action["path"])
                    if file_state._state_matches(actual, action["rollback"]):
                        action["state"] = "rolled_back"
                        continue
                    if not file_state._state_matches(actual, action["target"]):
                        recovery_required = True
                        action["error"] = "external change prevents rollback"
                        continue
                    rollback_guard = file_apply._apply_state(
                        action["path"], action["rollback"], backup_dir,
                        intent["transaction_id"] + "_rollback", action["target"],
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
            return _intent_result(intent)
        try:
            intent["status"] = "committed"
            manifest.save(intent_path, intent)
        except Exception as exc:
            # The target was changed, but durable completion was not recorded.
            intent["status"] = "recovery_required"
            intent["error_code"] = "RECOVERY_REQUIRED"
            intent["error"] = f"history commit failed: {exc}"
            try:
                manifest.save(intent_path, intent)
            except Exception as save_error:
                intent["error"] = f"history commit failed: {exc}; state save failed: {save_error}"
            return _intent_result(intent)
    return _intent_result(intent)

