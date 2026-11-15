"""Plan and apply rewinds in three modes: code and conversation, conversation, code."""
from __future__ import annotations

import logging
import hashlib
import uuid
from typing import Any

from openprogram.state.store.session.rewind_recovery import (
    cas_for_intent as _cas_for_intent,
    head as _head,
    recover_session_rewinds,
)
from openprogram.state.store.snapshot.checkpoint.gc import (
    MAX_TURNS,
    evicted_turns,
    turn_directories,
)
from openprogram.state.store.snapshot.checkpoint.paths import session_backup_root
from openprogram.state.store.snapshot.checkpoint.prompt_checkpoints import PromptCheckpoints
from openprogram.state.store.snapshot.checkpoint.restore_plan import (
    build_restore_plan,
    first_writes,
    writes_by_other_prompts,
)

from .rewind_marks import forget_read_baselines, rewind_meta_updates

_log = logging.getLogger(__name__)

CODE_AND_CONVERSATION = "code_and_conversation"
CONVERSATION = "conversation"
CODE = "code"
MODES = (CODE_AND_CONVERSATION, CONVERSATION, CODE)
JOB_RUNNING_MESSAGE = "A background task started in this conversation is still running."
NO_CODE_RESTORE_MESSAGE = "Code can no longer be restored to this message."
ARCHIVED_MESSAGE = "An archived conversation can't be rewound."
_WRITE_TOOLS = {"write", "edit", "apply_patch", "notebook_edit"}


def _active_chain(index) -> list:
    from openprogram.state.store.session.session_store import _node_conv_predecessor

    chain = []
    seen: set[str] = set()
    node = index.nodes_by_id.get(index.head_id) if index.head_id else None
    while node is not None and node.id not in seen:
        seen.add(node.id)
        chain.append(node)
        predecessor = _node_conv_predecessor(node)
        node = index.nodes_by_id.get(predecessor) if predecessor else None
    return chain


def _branch_id(session_id: str, head_id: str | None) -> str:
    digest = hashlib.sha256(f"{session_id}\0{head_id or 'ROOT'}".encode()).hexdigest()
    return f"branch_{digest[:16]}"


def _visible_prompt(node) -> bool:
    return node.role == "user" and (node.metadata or {}).get("display") not in {"root", "runtime"}


def _pickable_positions(chain: list) -> set[int]:
    """Prompts the picker lists, as positions in a newest-first chain.

    Before the first reply only the first prompt counts: anything sent while
    that turn ran belongs to it. A prompt with no text has nothing to show.
    """
    prompts = [
        position for position, node in enumerate(chain)
        if _visible_prompt(node) and _node_text(node).strip()
    ]
    if not prompts:
        return set()
    replies = [position for position, node in enumerate(chain) if node.role in {"llm", "assistant"}]
    first_reply = max(replies, default=-1)
    return {position for position in prompts if position < first_reply or position == prompts[-1]}


def _node_text(node) -> str:
    output = node.output or ""
    return str(output) if isinstance(output, dict) else output


def _write_tool_turns(index, turn_ids: list[str]) -> set[str]:
    """Turns with a file-writing tool call, read from the conversation itself."""
    wanted = set(turn_ids)
    return {
        node.caller for node in index.all_nodes()
        if node.caller in wanted and node.name in _WRITE_TOOLS
    }


def _legacy_gap(journal, index, session_dir, turn_ids: list[str], branched: bool) -> str | None:
    """Why a prompt with no checkpoint cannot have its code restored, if so."""
    if branched:
        return "no_checkpoint"
    evicted = evicted_turns(session_dir)
    if any(turn_id in evicted for turn_id in turn_ids):
        return "evicted"
    root = session_backup_root(session_dir)
    if root.exists() and len(turn_directories(root)) >= MAX_TURNS - 2:
        writers = _write_tool_turns(index, turn_ids)
        if any(not journal.list_backed_paths(turn_id) for turn_id in writers):
            return "evicted"
    return None


def list_rewind_points(
    session_id: str, limit: int | None = 10, *, recover: bool = True,
) -> list[dict[str, Any]]:
    """Return user turns on the active predecessor chain, newest first."""
    try:
        from openprogram.state.store.session.session_store import default_store
        from openprogram.state.store.snapshot.checkpoint import CheckpointStore
    except Exception:
        return []

    store = default_store()
    if recover:
        recover_session_rewinds(session_id, store=store)
    pair = store._open(session_id)
    if pair is None:
        return []
    _session_log, index = pair
    session_dir = store._session_dir(session_id)
    journal = CheckpointStore(session_dir)
    checkpoints = PromptCheckpoints(session_dir).ids()
    branched = bool((store.get_session(session_id) or {}).get("branched_from"))
    chain = _active_chain(index)
    pickable = _pickable_positions(chain)
    points: list[dict[str, Any]] = []
    for position, node in enumerate(chain):
        if position not in pickable:
            continue
        if limit is not None and len(points) >= limit:
            break
        output = _node_text(node)
        summary = output[:80].replace("\n", " ").strip()
        if len(output) > 80:
            summary += "..."
        files: list[str] = []
        for candidate in reversed(chain[:position]):
            if candidate.role == "user":
                break
            if candidate.role == "llm":
                files = journal.list_backed_paths(candidate.id)
                break
        has_checkpoint = node.id in checkpoints
        gap = None if has_checkpoint else _legacy_gap(
            journal, index, session_dir,
            [item.id for item in chain[:position + 1] if item.role == "llm"],
            branched,
        )
        points.append({
            "msg_id": node.id,
            "seq": node.seq,
            "summary": summary,
            "user_text": output,
            "created_at": getattr(node, "created_at", 0) or 0,
            "files_affected": files,
            "attachments": (node.metadata or {}).get("attachments") or [],
            "code_available": gap is None,
            "code_unavailable_reason": gap,
            "checkpoint": "prompt" if has_checkpoint else "legacy",
        })
    return points


def plan_rewind(
    session_id: str,
    target_msg_id: str,
    *,
    mode: str = CODE_AND_CONVERSATION,
    recover: bool = True,
) -> dict[str, Any]:
    """Return a read-only rewind plan. This function never changes HEAD/files."""
    plan = _build_plan(session_id, target_msg_id, mode, recover=recover, capture_current=False)
    plan.pop("_actions", None)
    return plan


def _build_plan(
    session_id: str,
    target_msg_id: str,
    mode: str,
    *,
    recover: bool,
    capture_current: bool,
) -> dict[str, Any]:
    if mode not in MODES:
        return _err(session_id, target_msg_id, f"unsupported rewind mode {mode!r}")
    try:
        from openprogram.state.store.session.session_store import (
            _node_conv_predecessor,
            default_store,
        )
        from openprogram.state.store.snapshot.checkpoint import CheckpointStore
    except Exception as exc:
        return _err(session_id, target_msg_id, f"import failed: {exc}")

    store = default_store()
    if recover:
        recover_session_rewinds(session_id, store=store)
    pair = store._open(session_id)
    if pair is None:
        return _err(session_id, target_msg_id, f"unknown session {session_id!r}")
    _session_log, index = pair
    target = index.nodes_by_id.get(target_msg_id)
    if target is None:
        return _err(session_id, target_msg_id, f"node {target_msg_id!r} not found")
    if target.role != "user" or (target.metadata or {}).get("display") == "root":
        return _err(session_id, target_msg_id, "target must be a non-root user turn")
    chain = _active_chain(index)
    target_position = next(
        (position for position, node in enumerate(chain) if node.id == target_msg_id),
        None,
    )
    if target_position is None:
        return _err(
            session_id, target_msg_id,
            f"node {target_msg_id!r} is not on the active branch",
        )
    suffix = chain[:target_position + 1]
    turn_ids = [node.id for node in suffix if node.role == "llm"]
    from openprogram.runtime.conversation.history_ownership import owned_change_set_closure

    ownership = owned_change_set_closure(session_id, turn_ids)
    if ownership["status"] != "ready":
        return {
            **_err(session_id, target_msg_id, JOB_RUNNING_MESSAGE),
            "status": "blocked",
            "error_code": "job_running",
            "blockers": ownership["blockers"],
            "linked_impacts": ownership["linked"],
        }
    turn_ids = ownership["owned_turn_ids"] + turn_ids
    turn_ids.sort(
        key=lambda turn_id: (
            index.nodes_by_id[turn_id].seq
            if turn_id in index.nodes_by_id else -1
        ),
        reverse=True,
    )
    if (store.get_session(session_id) or {}).get("archived"):
        return {
            **_err(session_id, target_msg_id, ARCHIVED_MESSAGE),
            "status": "blocked",
            "error_code": "archived",
        }
    source_head = index.head_id
    target_head = _node_conv_predecessor(target)
    if target_head is None:
        return _err(
            session_id, target_msg_id,
            "rewind target has no persistent predecessor",
        )
    session_dir = store._session_dir(session_id)
    journal = CheckpointStore(session_dir)
    session = store.get_session(session_id) or {}
    targets, by_path, gap, kind = _restore_targets(
        journal, index, session_dir, suffix, turn_ids, bool(session.get("branched_from")),
    )
    if targets is None:
        actions: list[dict] = []
        rows: list[dict] = []
    else:
        actions, rows = build_restore_plan(
            journal, targets,
            capture_current=capture_current,
            with_stats=not capture_current,
            turn_ids_by_path=by_path,
        )
    source_branch_id = index.meta.get("active_branch_id") or _branch_id(session_id, source_head)
    hashes = {
        CODE_AND_CONVERSATION: journal.rewind_plan_hash(turn_ids, source_head, target_head, actions, source_branch_id),
        CONVERSATION: journal.rewind_plan_hash(turn_ids, source_head, target_head, [], source_branch_id),
        CODE: journal.rewind_plan_hash(turn_ids, source_head, source_head, actions, source_branch_id),
    }
    unavailable = gap is not None and mode != CONVERSATION
    changing = [row for row in rows if row["action"] != "skip"]
    skipped = [row for row in rows if row["action"] == "skip"]
    conflicts = [row["path"] for row in skipped] if mode != CONVERSATION else []
    error = NO_CODE_RESTORE_MESSAGE if unavailable else ("Some files cannot be safely restored; no changes were made." if conflicts else None)
    return {
        "status": "unavailable" if unavailable else "blocked" if conflicts else "ready",
        "phase": "plan",
        "mode": mode,
        "session_id": session_id,
        "target_msg_id": target_msg_id,
        "source_head_id": source_head,
        "target_head_id": source_head if mode == CODE else target_head,
        "source_branch_id": source_branch_id,
        "expected_active_branch_id": index.meta.get("active_branch_id"),
        "turn_ids": turn_ids,
        "owned_turn_ids": ownership["owned_turn_ids"],
        "linked_impacts": ownership["linked"],
        "turns_reverted": len(turn_ids),
        "nodes_rewound": len(suffix),
        "user_text": _node_text(target),
        "attachments": (target.metadata or {}).get("attachments") or [],
        "plan_hash": hashes[mode],
        "plan_hashes": hashes,
        "idempotency_key": uuid.uuid4().hex,
        "files": rows,
        "summary": {
            "files": len(changing),
            "additions": sum(row["additions"] for row in changing),
            "deletions": sum(row["deletions"] for row in changing),
            "skipped": len(skipped),
        },
        "has_changes": bool(changing),
        "skipped": skipped,
        "code_available": gap is None and not skipped,
        "code_unavailable_reason": gap or ("conflict" if skipped else None),
        "error_code": "file_conflict" if conflicts else None,
        "checkpoint": kind,
        "conflicts": conflicts,
        "unavailable": [row["path"] for row in skipped if row["reason"] == "no_backup"],
        "error": error,
        "errors": [error] if error else [],
        "new_head_id": None,
        "head_changed": False,
        "_actions": actions,
    }


def _restore_targets(journal, index, session_dir, suffix, turn_ids, branched):
    """Target states for a suffix: ``(states, turn ids by path, gap, kind)``."""
    user_ids = [node.id for node in reversed(suffix) if node.role == "user"]
    states = PromptCheckpoints(session_dir).targets(user_ids)
    if states is not None:
        provenance = PromptCheckpoints(session_dir).read_origin()
        if branched and states and "turn_mutations" not in provenance:
            return None, {}, "journal_gap", "prompt"
        origin = provenance.get("turn_mutations") or {}
        records = [(turn, row) for turn in reversed(turn_ids)
                   for row in (journal.list_file_history(turn) or origin.get(turn, []))]
        records.sort(key=lambda item: item[1].get("mutation_sequence") or 0)
        latest, by_path, changed = {}, {}, set()
        for turn, row in records:
            path = row.get("path")
            if not path:
                continue
            by_path.setdefault(path, []).append(turn)
            before, after = row.get("before") or {}, row.get("after") or {}
            if path in latest and not journal._same_recorded_state(latest[path], before):
                changed.add(path)
            if after.get("kind") not in {"regular", "absent"}:
                changed.add(path)
            latest[path] = after
        for path, after in latest.items():
            if not journal._state_matches(journal._inspect_state(path), after):
                changed.add(path)
        # Another branch's turns, or a scheduled run's, may have written the
        # same files: go back only as far as their work, and leave a file
        # alone when their write sits on top of this range's own edits.
        others = writes_by_other_prompts(
            journal, user_ids, turn_ids, set(index.meta.get("rewind_hidden_nodes") or []),
        )
        others = {path: sequence for path, sequence in others.items() if path in latest}
        if others:
            beside, overwritten = PromptCheckpoints(session_dir).beside_other_prompts(
                user_ids, first_writes([row for _, row in records]), others,
            )
            states.update(beside)
            changed |= overwritten
        # Prompt snapshots include paths untouched by this suffix. Only its
        # recorded writes authorize restoring a path, including copied turns.
        targets = {path: {**states.get(path, {"kind": "unavailable"}),
                          "changed_since_edit": path in changed}
                   for path in latest}
        if _write_tool_turns(index, turn_ids) - {turn for turn, _ in records}:
            return None, {}, "journal_gap", "prompt"
        return targets, by_path, None, "prompt"
    gap = _legacy_gap(journal, index, session_dir, turn_ids, branched)
    if gap:
        return None, {}, gap, "legacy"
    folded = journal.plan_rewind_operation(turn_ids)
    if folded.get("status") == "unavailable":
        return None, {}, "journal_gap", "legacy"
    changed = set(folded.get("conflicts") or [])
    return (
        {
            action["path"]: {
                **action["target"],
                "captured_sequence": _before_first_write(journal, action),
                "changed_since_edit": action["path"] in changed,
            }
            for action in folded.get("actions", [])
        },
        {action["path"]: action.get("turn_ids", []) for action in folded.get("actions", [])},
        None,
        "legacy",
    )


def _before_first_write(journal, action: dict) -> int:
    """The workspace sequence just before a folded action's first edit."""
    sequences = [
        mutation["mutation_sequence"]
        for turn_id in action.get("turn_ids", [])
        for mutation in journal.list_mutations(turn_id)
        if mutation.get("path") == action["path"]
        and isinstance(mutation.get("mutation_sequence"), int)
    ]
    return min(sequences, default=1) - 1


def _operation_response(
    session_id: str,
    target_msg_id: str,
    result: dict,
    *,
    nodes_rewound: int,
) -> dict[str, Any]:
    committed = result.get("status") == "committed"
    error = result.get("error")
    turn_ids = result.get("turn_ids") or []
    return {
        "session_id": session_id,
        "target_msg_id": target_msg_id,
        "user_text": result.get("user_text", ""),
        "source_head_id": result.get("source_head_id"),
        "turns_reverted": len(turn_ids) if committed else 0,
        "nodes_rewound": nodes_rewound if committed else 0,
        "total_restored_paths": result.get("restored_paths", []),
        "errors": [error] if error else [],
        **result,
    }


def rewind_to(
    session_id: str,
    target_msg_id: str,
    *,
    idempotency_key: str | None = None,
    expected_plan_hash: str | None = None,
    mode: str = CODE_AND_CONVERSATION,
) -> dict[str, Any]:
    """Apply a confirmed plan; files and withdrawal commit on the current branch."""
    try:
        from openprogram.state.store.session.session_store import default_store
        from openprogram.state.store.snapshot.checkpoint import CheckpointStore
    except Exception as exc:
        return _err(session_id, target_msg_id, f"import failed: {exc}")

    store = default_store()
    if store._open(session_id) is None:
        return _err(session_id, target_msg_id, f"unknown session {session_id!r}")
    journal = CheckpointStore(store._session_dir(session_id))
    # One critical section from the key lookup to the outcome: a second
    # request with the same key waits here for the first, then finds its
    # intent and replays the outcome instead of planning against the state
    # the first one left.
    with store._conversation_write_lock(session_id):
        result, response = _rewind_to_locked(
            store, journal, session_id, target_msg_id,
            idempotency_key=idempotency_key,
            expected_plan_hash=expected_plan_hash,
            mode=mode,
        )
    if result is not None and result.get("status") == "committed" and not result.get("replayed"):
        forget_read_baselines(session_id)
        if result.get("head_changed"):
            try:
                store.commit_turn(session_id, "rewind")
            except Exception:
                # The rewind already moved the head; only the commit marker is missing.
                _log.warning("rewind not committed for session %s", session_id, exc_info=True)
    return response


def _rewind_to_locked(
    store,
    journal,
    session_id: str,
    target_msg_id: str,
    *,
    idempotency_key: str | None,
    expected_plan_hash: str | None,
    mode: str,
) -> tuple[dict | None, dict[str, Any]]:
    """Body of :func:`rewind_to`; the caller holds the conversation write lock.

    Returns the journal result (``None`` when nothing was applied) and the
    response.
    """
    if idempotency_key:
        existing = journal.read_rewind_intent(idempotency_key)
        if existing is not None:
            with store._rewind_recovery_scope(session_id):
                result = journal.apply_rewind_operation(
                    existing.get("turn_ids") or [],
                    expected_head_id=existing.get("expected_head_id"),
                    target_head_id=existing.get("target_head_id"),
                    get_head=lambda: _head(store, session_id),
                    compare_and_set_head=lambda expected, target, rollback=False: _cas_for_intent(
                        store, session_id, existing, expected, target, rollback=rollback,
                    ),
                    idempotency_key=idempotency_key,
                    target_msg_id=target_msg_id,
                    user_text=existing.get("user_text", ""),
                    source_branch_id=existing.get("source_branch_id"),
                    target_branch_id=existing.get("target_branch_id"),
                    expected_plan_hash=expected_plan_hash,
                )
            return result, _operation_response(
                session_id, target_msg_id, result,
                nodes_rewound=len(existing.get("turn_ids") or []) * 2,
            )

    plan = _build_plan(session_id, target_msg_id, mode, recover=True, capture_current=True)
    if expected_plan_hash and plan.get("plan_hash") and expected_plan_hash != plan.get("plan_hash"):
        plan.pop("_actions", None)
        return None, {
            **plan,
            "status": "aborted",
            "phase": "apply",
            "error": "stale_plan",
            "errors": ["stale_plan"],
        }

    if plan.get("status") != "ready":
        plan.pop("_actions", None)
        return None, plan

    def current_actions():
        fresh = _build_plan(session_id, target_msg_id, mode, recover=False, capture_current=True)
        actions = fresh.pop("_actions", [])
        if fresh.get("plan_hash") != plan["plan_hash"]:
            return {"status": "aborted", "error": "stale_plan"}
        if mode == CONVERSATION:
            actions = []
        return fresh if fresh.get("status") != "ready" else actions

    key = idempotency_key or uuid.uuid4().hex
    moves_head = mode != CODE
    target_branch_id = plan["source_branch_id"] if moves_head else None
    source_branch_id = plan["source_branch_id"]
    forward, rollback = rewind_meta_updates(
        store.get_session(session_id) or {},
        plan,
        mode=mode,
        target_branch_id=target_branch_id,
    )
    if moves_head:
        from openprogram.state.store.session.rewind_view import BRANCH_RECORD_MOVES, view_update
        visibility = view_update(store, session_id, plan["source_head_id"], plan["target_head_id"])
        previous = store.get_session(session_id) or {}
        rollback.update({key: previous.get(key, []) for key in visibility if key != BRANCH_RECORD_MOVES})
        # Undo moves the branch's name record back to the tip it left.
        rollback[BRANCH_RECORD_MOVES] = [[to, frm] for frm, to in reversed(visibility[BRANCH_RECORD_MOVES])]
        forward.update(visibility)
    intent_context = {
        "expected_active_branch_id": plan["expected_active_branch_id"],
        "expected_head_id": plan["source_head_id"],
        "target_head_id": plan["target_head_id"],
        "source_branch_id": source_branch_id,
        "target_branch_id": target_branch_id,
        "forward_meta_update": forward,
        "rollback_meta_update": rollback,
    }
    skipped = plan["skipped"] if mode != CONVERSATION else []
    # The caller's conversation write lock covers the intent to its outcome,
    # taken before the journal's own locks (one order: conversation, then
    # journal and workspace), so recovery never replays a live rewind.
    with store._rewind_recovery_scope(session_id):
        result = journal.apply_rewind_operation(
            plan["turn_ids"],
            expected_head_id=plan["source_head_id"],
            target_head_id=plan["target_head_id"],
            get_head=lambda: _head(store, session_id),
            compare_and_set_head=lambda expected, target, rollback=False: _cas_for_intent(
                store, session_id, intent_context, expected, target, rollback=rollback,
            ),
            idempotency_key=key,
            target_msg_id=target_msg_id,
            user_text=plan["user_text"],
            source_branch_id=source_branch_id,
            target_branch_id=target_branch_id,
            expected_plan_hash=plan["plan_hash"],
            forward_meta_update=forward,
            rollback_meta_update=rollback,
            action_planner=current_actions,
            intent_extra={"mode": mode, "skipped": skipped, "expected_active_branch_id": plan["expected_active_branch_id"]},
        )
    if result.get("status") == "blocked" and result.get("conflicts"):
        result = {**result, "status": "aborted", "error": "stale_plan"}
    return result, _operation_response(
        session_id, target_msg_id, result,
        nodes_rewound=plan["nodes_rewound"] if moves_head else 0,
    )


def _err(session_id: str, target: str, message: str) -> dict[str, Any]:
    return {
        "status": "error",
        "phase": "plan",
        "session_id": session_id,
        "target_msg_id": target,
        "user_text": "",
        "turns_reverted": 0,
        "nodes_rewound": 0,
        "total_restored_paths": [],
        "new_head_id": None,
        "head_changed": False,
        "error": message,
        "errors": [message],
    }
