"""Bounded Review scopes, exact journal diffs, Undo and Reapply actions."""
from __future__ import annotations

import asyncio
import difflib
import json
import os
import stat
import subprocess
from pathlib import Path
from typing import Any


_MAX_SCOPE_FILES = 5_000
_MAX_DIFF_BYTES = 512 * 1024
_MAX_DIFF_CHARS = 1_000_000


def _open_session(session_id: str):
    from openprogram.store.session.session_store import default_store

    store = default_store()
    pair = store._open(session_id)
    if pair is None:
        return None
    git, index = pair
    return store, git, index, store._session_dir(session_id)


def _project_root(session_id: str) -> Path | None:
    try:
        from openprogram.store.project.project_store import project_for_session

        project = project_for_session(session_id)
        if project and project.path:
            return Path(project.path).expanduser().resolve()
    except Exception:
        pass
    return None


def _relative(path: str, root: Path | None) -> str:
    if root is not None:
        try:
            return str(Path(path).resolve().relative_to(root))
        except (OSError, ValueError):
            pass
    return os.path.basename(path)


def _manifest_mutations(session_dir: Path, turn_id: str) -> list[dict]:
    from openprogram.store.snapshot.checkpoint import CheckpointStore

    return CheckpointStore(session_dir).list_mutations(turn_id)


def _normalise_file(row: dict, root: Path | None) -> dict:
    stats = row.get("stats") or {}
    path = row.get("path") or ""
    operation = row.get("op") or row.get("operation") or "modify"
    if operation == "create":
        operation = "add"
    return {
        "path": path,
        "rel": row.get("rel") or _relative(path, root),
        "op": operation,
        "added": row.get("added", stats.get("added")),
        "removed": row.get("removed", stats.get("removed")),
        "binary": bool(row.get("binary", stats.get("binary"))),
        "diff_state": row.get("diff_state", "available"),
        "recoverability": row.get("recoverability", "exact"),
        "unavailable_reason": row.get("unavailable_reason"),
        "turn_ids": list(row.get("turn_ids") or []),
    }


def _turn_summary(index, session_dir: Path, turn_id: str, root: Path | None) -> dict:
    node = index.nodes_by_id.get(turn_id)
    metadata = getattr(node, "metadata", None) or {}
    summary = metadata.get("turn_files")
    if isinstance(summary, dict) and isinstance(summary.get("files"), list):
        files = [_normalise_file(row, root) for row in summary["files"]]
    else:
        files = [
            _normalise_file(row, root)
            for row in _manifest_mutations(session_dir, turn_id)
        ]
    for row in files:
        row["turn_ids"] = [turn_id]
    return {
        "files": files,
        "reverted": bool(metadata.get("reverted")),
    }


def _active_nodes(index) -> list:
    from openprogram.store.session.session_store import _node_conv_predecessor

    nodes = []
    seen: set[str] = set()
    node = index.nodes_by_id.get(index.head_id) if index.head_id else None
    while node is not None and node.id not in seen:
        seen.add(node.id)
        nodes.append(node)
        predecessor = _node_conv_predecessor(node)
        node = index.nodes_by_id.get(predecessor) if predecessor else None
    return nodes


def _totals(files: list[dict]) -> tuple[int | None, int | None]:
    added = [row.get("added") for row in files]
    removed = [row.get("removed") for row in files]
    return (
        sum(added) if all(isinstance(value, int) for value in added) else None,
        sum(removed) if all(isinstance(value, int) for value in removed) else None,
    )


def _scope_payload(scope: str, source: str, files: list[dict], **extra) -> dict:
    bounded = files[:_MAX_SCOPE_FILES]
    added, removed = _totals(bounded)
    return {
        "status": "ready",
        "scope": scope,
        "source": source,
        "files": bounded,
        "file_count": len(files),
        "added": added,
        "removed": removed,
        "truncated": len(files) > _MAX_SCOPE_FILES,
        **extra,
    }


def _list_files(session_id: str, assistant_msg_id: str) -> dict:
    opened = _open_session(session_id)
    if opened is None:
        return {"files": [], "paths": [], "error": f"unknown session {session_id!r}"}
    _store, _git, index, session_dir = opened
    result = _turn_summary(
        index, session_dir, assistant_msg_id, _project_root(session_id),
    )
    return {
        **result,
        "paths": [row["path"] for row in result["files"]],
    }


def _turn_scope(session_id: str, turn_id: str) -> dict:
    opened = _open_session(session_id)
    if opened is None:
        return {"status": "error", "error": f"unknown session {session_id!r}"}
    _store, _git, index, session_dir = opened
    if turn_id not in index.nodes_by_id:
        return {"status": "error", "error": f"unknown turn {turn_id!r}"}
    summary = _turn_summary(index, session_dir, turn_id, _project_root(session_id))
    return _scope_payload(
        "turn", "mutation_journal", summary["files"],
        assistant_msg_id=turn_id, reverted=summary["reverted"],
    )


def _branch_scope(session_id: str) -> dict:
    opened = _open_session(session_id)
    if opened is None:
        return {"status": "error", "error": f"unknown session {session_id!r}"}
    _store, _git, index, session_dir = opened
    root = _project_root(session_id)
    merged: dict[str, dict] = {}
    for node in reversed(_active_nodes(index)):
        if node.role != "llm":
            continue
        summary = _turn_summary(index, session_dir, node.id, root)
        if summary["reverted"]:
            continue
        for row in summary["files"]:
            path = row["path"]
            current = merged.get(path)
            if current is None:
                merged[path] = {**row, "turn_ids": [node.id]}
                continue
            current["turn_ids"].append(node.id)
            for field in ("added", "removed"):
                first, second = current.get(field), row.get(field)
                current[field] = (
                    first + second
                    if isinstance(first, int) and isinstance(second, int)
                    else None
                )
            current["op"] = row["op"]
            current["binary"] = current["binary"] or row["binary"]
            if row["diff_state"] != "available":
                current["diff_state"] = row["diff_state"]
    return _scope_payload(
        "branch", "mutation_journal", list(merged.values()),
        head_id=index.head_id,
    )


def _git_output(root: Path, *args: str, timeout: float = 8.0) -> bytes:
    process = subprocess.Popen(
        ["git", "-C", str(root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise OSError("git command timed out")
    if process.returncode != 0:
        raise OSError(stderr.decode("utf-8", errors="replace").strip())
    return stdout


def _untracked_stats(path: Path) -> tuple[int | None, int | None, bool, str]:
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_DIFF_BYTES:
            return None, 0, False, "large"
        raw = path.read_bytes()
        if b"\0" in raw:
            return None, None, True, "binary"
        return len(raw.decode("utf-8", errors="replace").splitlines()), 0, False, "available"
    except OSError:
        return None, None, False, "unavailable"


def _workspace_scope(session_id: str) -> dict:
    root = _project_root(session_id)
    if root is None or not (root / ".git").exists():
        return {
            "status": "unavailable", "scope": "workspace", "source": "git",
            "files": [], "file_count": 0, "error": "workspace is not a Git repository",
        }
    try:
        raw_status = _git_output(
            root, "status", "--porcelain=v1", "-z", "--untracked-files=all",
        )
        raw_stats = _git_output(root, "diff", "--numstat", "HEAD", "--")
    except OSError as exc:
        return {
            "status": "unavailable", "scope": "workspace", "source": "git",
            "files": [], "file_count": 0, "error": str(exc),
        }
    stats: dict[str, tuple[int | None, int | None]] = {}
    for line in raw_stats.decode("utf-8", errors="replace").splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        stats[parts[2]] = (
            int(parts[0]) if parts[0].isdigit() else None,
            int(parts[1]) if parts[1].isdigit() else None,
        )
    chunks = raw_status.decode("utf-8", errors="replace").split("\0")
    files = []
    position = 0
    while position < len(chunks):
        chunk = chunks[position]
        position += 1
        if len(chunk) < 4:
            continue
        code, rel = chunk[:2], chunk[3:]
        if "R" in code or "C" in code:
            position += 1
        added, removed = stats.get(rel, (0, 0))
        binary = added is None or removed is None
        diff_state = "binary" if binary else "available"
        if code == "??":
            added, removed, binary, diff_state = _untracked_stats(root / rel)
        op = "add" if code == "??" or "A" in code else "delete" if "D" in code else "modify"
        files.append({
            "path": str(root / rel), "rel": rel, "op": op,
            "added": added, "removed": removed, "binary": binary,
            "diff_state": diff_state, "recoverability": "unavailable",
            "unavailable_reason": "workspace_scope", "turn_ids": [],
            "status_code": code,
        })
    return _scope_payload(
        "workspace", "git", files,
        root=str(root), ignored_policy="exclude_standard",
    )


def _same_state(first: dict, second: dict) -> bool:
    if first.get("kind") != second.get("kind"):
        return False
    if first.get("kind") == "regular":
        return first.get("digest") == second.get("digest")
    return first.get("kind") == "absent"


def _state_bytes(session_dir: Path, turn_id: str, state: dict) -> tuple[bytes, str]:
    if state.get("kind") == "absent":
        return b"", "available"
    if state.get("kind") != "regular" or not state.get("blob_ref"):
        raise OSError("recorded state is unavailable")
    from openprogram.store.snapshot.checkpoint.paths import turn_backup_dir

    path = turn_backup_dir(session_dir, turn_id) / str(state["blob_ref"])
    if not path.is_file():
        raise OSError("recovery blob is missing")
    if path.stat().st_size > _MAX_DIFF_BYTES:
        return b"", "large"
    raw = path.read_bytes()
    if b"\0" in raw:
        return b"", "binary"
    return raw, "available"


def _render_diff(
    session_dir: Path,
    before_turn: str,
    before: dict,
    after_turn: str,
    after: dict,
    path: str,
) -> dict:
    try:
        before_raw, before_state = _state_bytes(session_dir, before_turn, before)
        after_raw, after_state = _state_bytes(session_dir, after_turn, after)
    except OSError as exc:
        return {"diff": "", "diff_state": "unavailable", "error": str(exc)}
    state = before_state if before_state != "available" else after_state
    if state != "available":
        return {"diff": "", "diff_state": state}
    name = os.path.basename(path)
    text = "".join(difflib.unified_diff(
        before_raw.decode("utf-8", errors="replace").splitlines(keepends=True),
        after_raw.decode("utf-8", errors="replace").splitlines(keepends=True),
        fromfile=f"a/{name}", tofile=f"b/{name}", n=3,
    ))
    truncated = len(text) > _MAX_DIFF_CHARS
    return {
        "diff": text[:_MAX_DIFF_CHARS],
        "diff_state": "truncated" if truncated else "available",
        "truncated": truncated,
    }


def _turn_file_diff(session_id: str, turn_id: str, path: str) -> dict:
    opened = _open_session(session_id)
    if opened is None:
        return {"diff": "", "diff_state": "unavailable", "error": "unknown session"}
    _store, _git, _index, session_dir = opened
    mutation = next(
        (row for row in _manifest_mutations(session_dir, turn_id) if row.get("path") == path),
        None,
    )
    if mutation is None:
        return {"diff": "", "diff_state": "unavailable", "error": f"{path!r} not recorded for this turn"}
    return _render_diff(
        session_dir, turn_id, mutation.get("before") or {},
        turn_id, mutation.get("after") or {}, path,
    )


def _branch_file_diff(session_id: str, path: str) -> dict:
    opened = _open_session(session_id)
    if opened is None:
        return {"diff": "", "diff_state": "unavailable", "error": "unknown session"}
    _store, _git, index, session_dir = opened
    states = []
    for node in reversed(_active_nodes(index)):
        if node.role != "llm" or (node.metadata or {}).get("reverted"):
            continue
        mutation = next(
            (row for row in _manifest_mutations(session_dir, node.id) if row.get("path") == path),
            None,
        )
        if mutation:
            states.append((node.id, mutation))
    if not states:
        return {"diff": "", "diff_state": "unavailable", "error": f"{path!r} not on current branch"}
    for (_prior_turn, prior), (_next_turn, following) in zip(states, states[1:]):
        if not _same_state(prior.get("after") or {}, following.get("before") or {}):
            return {"diff": "", "diff_state": "unavailable", "error": "branch mutation journal is discontinuous"}
    first_turn, first = states[0]
    last_turn, last = states[-1]
    return _render_diff(
        session_dir, first_turn, first.get("before") or {},
        last_turn, last.get("after") or {}, path,
    )


def _workspace_file_diff(session_id: str, path: str) -> dict:
    root = _project_root(session_id)
    if root is None:
        return {"diff": "", "diff_state": "unavailable", "error": "workspace unavailable"}
    try:
        rel = str(Path(path).resolve().relative_to(root))
    except (OSError, ValueError):
        return {"diff": "", "diff_state": "unavailable", "error": "path is outside workspace"}
    try:
        tracked = bool(_git_output(root, "ls-files", "--error-unmatch", "--", rel))
    except OSError:
        tracked = False
    if tracked:
        try:
            raw = _git_output(root, "diff", "--no-ext-diff", "HEAD", "--", rel)
        except OSError as exc:
            return {"diff": "", "diff_state": "unavailable", "error": str(exc)}
        if len(raw) > _MAX_DIFF_BYTES:
            return {"diff": "", "diff_state": "large"}
        return {"diff": raw.decode("utf-8", errors="replace"), "diff_state": "available"}
    file_path = root / rel
    try:
        raw = file_path.read_bytes()
    except OSError as exc:
        return {"diff": "", "diff_state": "unavailable", "error": str(exc)}
    if len(raw) > _MAX_DIFF_BYTES:
        return {"diff": "", "diff_state": "large"}
    if b"\0" in raw:
        return {"diff": "", "diff_state": "binary"}
    text = "".join(difflib.unified_diff(
        [], raw.decode("utf-8", errors="replace").splitlines(keepends=True),
        fromfile="/dev/null", tofile=f"b/{rel}", n=3,
    ))
    return {"diff": text[:_MAX_DIFF_CHARS], "diff_state": "available", "truncated": len(text) > _MAX_DIFF_CHARS}


async def _run(fn) -> Any:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, fn)


async def handle_list_turn_files(ws, cmd: dict) -> None:
    session_id = (cmd.get("session_id") or "").strip()
    turn_id = (cmd.get("assistant_msg_id") or "").strip()
    result = (
        await _run(lambda: _list_files(session_id, turn_id))
        if session_id and turn_id
        else {"files": [], "paths": [], "error": "session_id and assistant_msg_id are required"}
    )
    await ws.send_text(json.dumps({
        "type": "list_turn_files_result",
        "data": {"session_id": session_id, "assistant_msg_id": turn_id, **result},
    }, default=str))


async def handle_turn_file_diff(ws, cmd: dict) -> None:
    session_id = (cmd.get("session_id") or "").strip()
    turn_id = (cmd.get("assistant_msg_id") or "").strip()
    path = (cmd.get("path") or "").strip()
    result = (
        await _run(lambda: _turn_file_diff(session_id, turn_id, path))
        if session_id and turn_id and path
        else {"diff": "", "diff_state": "unavailable", "error": "session_id, assistant_msg_id and path are required"}
    )
    await ws.send_text(json.dumps({
        "type": "turn_file_diff_result",
        "data": {
            "session_id": session_id, "assistant_msg_id": turn_id, "path": path,
            "approximate": False, **result,
        },
    }, default=str))


async def handle_review_scope(ws, cmd: dict) -> None:
    session_id = (cmd.get("session_id") or "").strip()
    scope = (cmd.get("scope") or "turn").strip()
    turn_id = (cmd.get("assistant_msg_id") or "").strip()
    if not session_id:
        result = {"status": "error", "error": "session_id is required"}
    elif scope == "turn":
        result = await _run(lambda: _turn_scope(session_id, turn_id))
    elif scope == "branch":
        result = await _run(lambda: _branch_scope(session_id))
    elif scope == "workspace":
        result = await _run(lambda: _workspace_scope(session_id))
    else:
        result = {"status": "error", "error": f"unknown review scope {scope!r}"}
    await ws.send_text(json.dumps({
        "type": "review_scope_result",
        "data": {"session_id": session_id, "assistant_msg_id": turn_id, **result},
    }, default=str))


async def handle_review_file_diff(ws, cmd: dict) -> None:
    session_id = (cmd.get("session_id") or "").strip()
    scope = (cmd.get("scope") or "turn").strip()
    turn_id = (cmd.get("assistant_msg_id") or "").strip()
    path = (cmd.get("path") or "").strip()
    if not session_id or not path:
        result = {"diff": "", "diff_state": "unavailable", "error": "session_id and path are required"}
    elif scope == "turn":
        result = await _run(lambda: _turn_file_diff(session_id, turn_id, path))
    elif scope == "branch":
        result = await _run(lambda: _branch_file_diff(session_id, path))
    elif scope == "workspace":
        result = await _run(lambda: _workspace_file_diff(session_id, path))
    else:
        result = {"diff": "", "diff_state": "unavailable", "error": f"unknown review scope {scope!r}"}
    await ws.send_text(json.dumps({
        "type": "review_file_diff_result",
        "data": {
            "session_id": session_id, "assistant_msg_id": turn_id,
            "scope": scope, "path": path, **result,
        },
    }, default=str))


async def handle_revert_turn(ws, cmd: dict) -> None:
    session_id = (cmd.get("session_id") or "").strip()
    msg_id = (cmd.get("msg_id") or "").strip()
    from openprogram.webui import server as server
    if session_id and server._is_run_active(session_id):
        result = {"status": "blocked", "restored_paths": [], "conflicts": [], "unavailable": [], "error": "run_active"}
    else:
        from openprogram.agent.internals._revert import revert_turn
        result = await _run(lambda: revert_turn(
            session_id, msg_id,
            idempotency_key=(cmd.get("idempotency_key") or None),
        ))
    await ws.send_text(json.dumps({
        "type": "revert_turn_result",
        "data": {
            "session_id": session_id, "msg_id": msg_id,
            "reverted_paths": result.get("restored_paths") or [],
            "status": result.get("status"),
            "transaction_id": result.get("transaction_id"),
            "conflicts": result.get("conflicts") or [],
            "unavailable": result.get("unavailable") or [],
            "errors": [result["error"]] if result.get("error") else [],
        },
    }, default=str))


async def handle_reapply_turn(ws, cmd: dict) -> None:
    session_id = (cmd.get("session_id") or "").strip()
    msg_id = (cmd.get("msg_id") or "").strip()
    from openprogram.webui import server as server
    if session_id and server._is_run_active(session_id):
        result = {"status": "blocked", "restored_paths": [], "conflicts": [], "unavailable": [], "error": "run_active"}
    else:
        from openprogram.agent.internals._revert import reapply_turn
        result = await _run(lambda: reapply_turn(
            session_id, msg_id,
            idempotency_key=(cmd.get("idempotency_key") or None),
        ))
    await ws.send_text(json.dumps({
        "type": "reapply_turn_result",
        "data": {
            "session_id": session_id, "msg_id": msg_id,
            "reapplied_paths": result.get("restored_paths") or [],
            "status": result.get("status"),
            "transaction_id": result.get("transaction_id"),
            "conflicts": result.get("conflicts") or [],
            "unavailable": result.get("unavailable") or [],
            "errors": [result["error"]] if result.get("error") else [],
        },
    }, default=str))


ACTIONS = {
    "list_turn_files": handle_list_turn_files,
    "turn_file_diff": handle_turn_file_diff,
    "review_scope": handle_review_scope,
    "review_file_diff": handle_review_file_diff,
    "revert_turn": handle_revert_turn,
    "reapply_turn": handle_reapply_turn,
}
