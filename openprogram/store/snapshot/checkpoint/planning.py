"""Read committed mutations and plan history changes without executing them."""
from __future__ import annotations

from pathlib import Path

from . import file_state, journal
from .paths import turn_backup_dir


def state_with_blob(session_dir: Path, turn_id: str, state: dict) -> dict:
    value = dict(state)
    if value.get("kind") == "regular":
        value["blob_path"] = str(
            turn_backup_dir(session_dir, turn_id)
            / str(value.get("blob_ref") or "")
        )
    return value


def plan_history_operation(session_dir: Path, turn_id: str, direction: str) -> dict:
    if direction not in {"revert", "reapply"}:
        return {"status": "error", "error": f"unknown direction {direction!r}"}
    mutations = journal.list_mutations(session_dir, turn_id)
    if not mutations:
        return {"status": "error", "error": "no committed mutations"}
    backup_dir = turn_backup_dir(session_dir, turn_id)
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
            parent_chain = file_state._capture_parent_chain(path)
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
        current = file_state._inspect_state(path)
        if not file_state._state_matches(current, source):
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


def plan_rewind_operation(
    session_dir: Path,
    turn_ids: list[str],
    direction: str = "revert",
) -> dict:
    """Fold a related turn set into one reversible action per path."""
    if direction not in {"revert", "reapply"}:
        return {"status": "error", "error": f"unknown direction {direction!r}"}
    folded: dict[str, dict] = {}
    unavailable: list[str] = []
    discontinuous: list[str] = []
    ordered_turn_ids = list(dict.fromkeys(turn_ids))
    records = [
        (turn_id, mutation)
        for turn_id in reversed(ordered_turn_ids)
        for mutation in journal.list_mutations(session_dir, turn_id)
    ]
    if records and all(
        isinstance(mutation.get("mutation_sequence"), int)
        for _turn_id, mutation in records
    ):
        records.sort(key=lambda item: item[1]["mutation_sequence"])
    for turn_id, mutation in records:
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
        before = state_with_blob(session_dir, turn_id, before)
        after = state_with_blob(session_dir, turn_id, after)
        try:
            parent_chain = file_state._capture_parent_chain(path)
        except OSError:
            unavailable.append(path)
            continue
        before["parent_chain"] = parent_chain
        after["parent_chain"] = parent_chain
        if not file_state._blob_is_exact(before) or not file_state._blob_is_exact(after):
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
        if not file_state._same_recorded_state(current["expected_current"], before):
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
    if direction == "reapply":
        for action in actions:
            source = action["target"]
            target = action["expected_current"]
            action["expected_current"] = source
            action["target"] = target
            action["rollback"] = source
    conflicts = [
        action["path"] for action in actions
        if not file_state._state_matches(
            file_state._inspect_state(action["path"]), action["expected_current"],
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

