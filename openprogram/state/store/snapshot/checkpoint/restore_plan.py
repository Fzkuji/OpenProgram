"""Turn a set of target file states into restore actions and report rows."""
from __future__ import annotations

import os
import time
from pathlib import Path

from .prompt_checkpoints import PromptCheckpoints, writers_after

STATS_BUDGET_SECONDS = 3.0
STATS_MAX_BYTES = 1024 * 1024
# Facts a target carries for planning; a restore does not act on them.
_PLANNING_KEYS = {"captured_sequence", "changed_since_edit"}


def written_by_another_session(path: str, session: str, origin: dict, since: int) -> bool:
    """Whether another session wrote the path after its target state was read.

    A session this one was forked from counts only for writes after the fork.
    """
    for writer, sequence in writers_after(path, since):
        if writer == session:
            continue
        if writer == origin.get("source_session_id") and sequence <= int(origin.get("through_sequence") or 0):
            continue
        return True
    return False


def writes_by_other_prompts(
    journal, user_ids: list[str], turn_ids: list[str], withdrawn: set[str],
) -> dict[str, int]:
    """Each path's newest write by a turn of a prompt outside a rewind's range.

    Two branches of a conversation run turns at once and a scheduled run has
    its own branch, so turns outside the range may have written the same
    files. Only turns a checkpoint names count, and not those a rewind
    withdrew. A write a committed rewind already took back is not in the file
    any more and does not count either. Values are workspace mutation
    sequences.
    """
    inside = set(user_ids)
    skipped = set(turn_ids) | withdrawn
    restored = journal.restored_writes()
    newest: dict[str, int] = {}
    for row in PromptCheckpoints(journal.session_dir).all():
        prompt = str(row.get("user_msg_id"))
        if prompt in inside or prompt in withdrawn:
            continue
        for turn in row.get("turn_ids") or []:
            if turn in skipped:
                continue
            for record in journal.list_file_history(turn):
                path = record.get("path")
                sequence = int(record.get("mutation_sequence") or 0)
                if not path or sequence <= restored.get((turn, path), 0):
                    continue
                if sequence > newest.get(path, 0):
                    newest[path] = sequence
    return newest


def first_writes(records: list[dict]) -> dict[str, int]:
    """Where a rewind's own turns first wrote each path, as a workspace sequence.

    A path with a record that does not say where its first write started is
    left out: nothing can then be placed before it.
    """
    first: dict[str, int] = {}
    unknown: set[str] = set()
    for record in records:
        path = record.get("path")
        if not path:
            continue
        sequence = int(record.get("first_sequence") or 0)
        if not sequence:
            unknown.add(path)
        else:
            first[path] = min(first.get(path, sequence), sequence)
    return {path: sequence for path, sequence in first.items() if path not in unknown}


def _line_counts(current: dict, target: dict, path: str) -> tuple[int, int] | None:
    import difflib

    def read(state: dict, source: str | None) -> list[str] | None:
        if state.get("kind") == "absent":
            return []
        if not source or int(state.get("size") or os.path.getsize(source)) > STATS_MAX_BYTES:
            return None
        data = Path(source).read_bytes()
        if b"\0" in data:
            return None
        return data.decode("utf-8", errors="replace").splitlines()

    try:
        now = read(current, path)
        then = read(target, target.get("blob_path"))
    except OSError:
        return None
    if now is None or then is None:
        return None
    additions = deletions = 0
    # Counted in the direction of the restore; the first two lines are headers.
    for line in list(difflib.unified_diff(now, then, lineterm="", n=0))[2:]:
        if line.startswith("+"):
            additions += 1
        elif line.startswith("-"):
            deletions += 1
    return additions, deletions


def build_restore_plan(
    journal,
    targets: dict[str, dict],
    *,
    capture_current: bool,
    with_stats: bool = True,
    turn_ids_by_path: dict[str, list[str]] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Return ``(actions, rows)``.

    Rows name what a rewind does to each file: ``restore``, ``delete`` or
    ``skip`` with a reason. A target's ``captured_sequence`` says when it was
    read, so writes by other sessions after that point are left alone; its
    ``changed_since_edit`` flag marks a file that no longer matches the journal.
    Actions are the transaction's input, one for every
    non-skipped row. With ``capture_current`` the present content is saved so
    the transaction can roll back; without it this function writes nothing.
    """
    checkpoints = PromptCheckpoints(journal.session_dir)
    session = journal.session_dir.name
    origin = checkpoints.read_origin()
    deadline = time.monotonic() + STATS_BUDGET_SECONDS
    actions: list[dict] = []
    rows: list[dict] = []
    paths_turns = turn_ids_by_path or {}

    def skip(path: str, reason: str) -> None:
        rows.append({
            "path": path, "action": "skip", "reason": reason,
            "additions": 0, "deletions": 0, "turn_ids": paths_turns.get(path, []),
        })

    for path in sorted(targets):
        target = targets[path]
        current = journal._inspect_state(path)
        kind = current.get("kind")
        target_kind = target.get("kind")
        if target_kind == "unavailable":
            if kind in {"regular", "absent"}:
                skip(path, "no_backup")
            else:
                skip(path, "unsafe_path")
            continue
        if kind not in {"regular", "absent"}:
            skip(path, "unsafe_path")
            continue
        if journal._state_matches(current, target):
            continue
        if written_by_another_session(path, session, origin, int(target.get("captured_sequence") or 0)):
            skip(path, "other_session")
            continue
        if target.get("changed_since_edit"):
            skip(path, "changed_since_edit")
            continue
        try:
            chain = journal._capture_parent_chain(path)
        except FileNotFoundError:
            skip(path, "parent_missing")
            continue
        except OSError:
            skip(path, "unsafe_path")
            continue
        expected = {"kind": kind, "parent_chain": chain}
        rollback = dict(expected)
        if kind == "regular":
            expected.update(digest=current["digest"], mode=current["mode"], size=current["size"])
            rollback = {
                **expected,
                "blob_path": str(checkpoints.blob_path(current["digest"])),
            }
            if capture_current:
                try:
                    stored = checkpoints.store_blob(path)
                except OSError:
                    skip(path, "changed_while_reading")
                    continue
                if f"sha256:{stored}" != current["digest"]:
                    skip(path, "changed_while_reading")
                    continue
        target_state = {
            **{key: value for key, value in target.items() if key not in _PLANNING_KEYS},
            "parent_chain": chain,
        }
        actions.append({
            "path": path,
            "expected_current": expected,
            "target": target_state,
            "rollback": rollback,
            "turn_ids": paths_turns.get(path, []),
            "state": "pending",
            "error": None,
        })
        counts = None
        if with_stats and time.monotonic() < deadline:
            counts = _line_counts(current, target, path)
        rows.append({
            "path": path,
            "action": "delete" if target_kind == "absent" else "restore",
            "reason": None,
            "additions": counts[0] if counts else 0,
            "deletions": counts[1] if counts else 0,
            "turn_ids": paths_turns.get(path, []),
        })
    return actions, rows
