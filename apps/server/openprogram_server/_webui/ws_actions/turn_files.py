"""Bounded Review scopes, exact journal diffs, Undo and Reapply actions."""
from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any


_MAX_SCOPE_FILES = 10_000
_SCOPE_PAGE_SIZE = 100
_MAX_DIFF_BYTES = 512 * 1024
_MAX_DIFF_PAGE_BYTES = 256 * 1024
_MAX_DIFF_LINES = 200
_MAX_DIFF_LINE_BYTES = 64 * 1024


class _OutputLimitError(OSError):
    pass


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

    if not _valid_turn_id(turn_id):
        return []
    return CheckpointStore(session_dir).list_mutations(turn_id)


def _valid_turn_id(turn_id: str) -> bool:
    return bool(
        turn_id
        and turn_id not in {".", ".."}
        and Path(turn_id).name == turn_id
        and "/" not in turn_id
        and "\\" not in turn_id
    )


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
        "file_count": (
            int(summary.get("file_count") or len(files))
            if isinstance(summary, dict)
            else len(files)
        ),
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
    snapshot_basis = extra.pop("_snapshot_basis", files)
    bounded = files[:_MAX_SCOPE_FILES]
    added, removed = _totals(bounded)
    payload = {
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
    payload["snapshot_id"] = "sha256:" + hashlib.sha256(
        json.dumps({
            "scope": scope,
            "source": source,
            "files": snapshot_basis,
            "head_id": extra.get("head_id"),
        }, sort_keys=True, default=str).encode(),
    ).hexdigest()
    return payload


def _page_scope(result: dict, cursor: int, limit: int) -> dict:
    if result.get("status") != "ready":
        return result
    files = result.get("files") or []
    start = max(0, cursor)
    size = max(1, min(limit, _SCOPE_PAGE_SIZE))
    page = files[start:start + size]
    return {
        **result,
        "files": page,
        "cursor": start,
        "next_cursor": start + size if start + size < len(files) else None,
        "prev_cursor": max(0, start - size) if start > 0 else None,
        "page_size": size,
    }


def _list_files(session_id: str, assistant_msg_id: str) -> dict:
    opened = _open_session(session_id)
    if opened is None:
        return {"files": [], "paths": [], "error": f"unknown session {session_id!r}"}
    _store, _git, index, session_dir = opened
    root = _project_root(session_id)
    mutations = _manifest_mutations(session_dir, assistant_msg_id)
    result = (
        {
            "files": [_normalise_file(row, root) for row in mutations],
            "file_count": len(mutations),
            "reverted": bool((index.nodes_by_id.get(assistant_msg_id).metadata or {}).get("reverted"))
                if index.nodes_by_id.get(assistant_msg_id) else False,
        }
        if mutations
        else _turn_summary(index, session_dir, assistant_msg_id, root)
    )
    return {
        **result,
        "files": result["files"][:20],
        "paths": [row["path"] for row in result["files"][:20]],
        "file_count": result.get("file_count", len(result["files"])),
        "truncated": result.get("file_count", len(result["files"])) > 20,
    }


def _history_eligibility(session_id: str, turn_id: str) -> dict:
    opened = _open_session(session_id)
    if opened is None:
        return {"status": "error", "action": None, "error": "unknown session"}
    _store, _git, index, session_dir = opened
    node = index.nodes_by_id.get(turn_id)
    if node is None:
        return {"status": "error", "action": None, "error": "unknown turn"}
    active = _active_nodes(index)
    if not any(candidate.id == turn_id for candidate in active):
        return {"status": "blocked", "action": None, "error": "turn is not on active branch"}
    file_turn_ids = []
    for candidate in active:
        if candidate.role != "llm":
            continue
        summary = (candidate.metadata or {}).get("turn_files") or {}
        if summary.get("file_count") or _manifest_mutations(session_dir, candidate.id):
            file_turn_ids.append(candidate.id)
    latest_file_turn = file_turn_ids[0] if file_turn_ids else None
    reverted = bool((node.metadata or {}).get("reverted"))
    direction = "reapply" if reverted else "revert"
    from openprogram.store.snapshot.checkpoint import CheckpointStore

    plan = CheckpointStore(session_dir).plan_history_operation(turn_id, direction)
    if plan.get("status") != "ready":
        return {
            "status": plan.get("status", "unavailable"),
            "action": None,
            "reverted": reverted,
            "latest_file_turn_id": latest_file_turn,
            "conflicts": plan.get("conflicts", []),
            "unavailable": plan.get("unavailable", []),
            "error": plan.get("error"),
        }
    return {
        "status": "ready",
        "action": (
            "redo" if reverted and turn_id == latest_file_turn
            else "reapply" if reverted
            else "undo" if turn_id == latest_file_turn
            else "revert"
        ),
        "reverted": reverted,
        "latest_file_turn_id": latest_file_turn,
        "conflicts": [],
        "unavailable": [],
    }


def _turn_scope(session_id: str, turn_id: str) -> dict:
    opened = _open_session(session_id)
    if opened is None:
        return {"status": "error", "error": f"unknown session {session_id!r}"}
    _store, _git, index, session_dir = opened
    if turn_id not in index.nodes_by_id:
        return {"status": "error", "error": f"unknown turn {turn_id!r}"}
    root = _project_root(session_id)
    mutations = _manifest_mutations(session_dir, turn_id)
    summary = (
        {
            "files": [_normalise_file(row, root) for row in mutations],
            "reverted": bool((index.nodes_by_id[turn_id].metadata or {}).get("reverted")),
        }
        if mutations
        else _turn_summary(index, session_dir, turn_id, root)
    )
    for row in summary["files"]:
        row["turn_ids"] = [turn_id]
    return _scope_payload(
        "turn", "mutation_journal", summary["files"],
        assistant_msg_id=turn_id,
        reverted=summary["reverted"],
        _snapshot_basis=[{
            "path": mutation.get("path"),
            "before": mutation.get("before"),
            "after": mutation.get("after"),
        } for mutation in mutations] if mutations else summary["files"],
    )


def _branch_scope(session_id: str) -> dict:
    opened = _open_session(session_id)
    if opened is None:
        return {"status": "error", "error": f"unknown session {session_id!r}"}
    _store, _git, index, session_dir = opened
    root = _project_root(session_id)
    lineages: dict[str, dict] = {}
    for node in reversed(_active_nodes(index)):
        if node.role != "llm" or (node.metadata or {}).get("reverted"):
            continue
        for mutation in _manifest_mutations(session_dir, node.id):
            path = mutation.get("path") or ""
            before = mutation.get("before") or {}
            after = mutation.get("after") or {}
            if not path:
                continue
            current = lineages.get(path)
            if current is None:
                lineages[path] = {
                    "path": path,
                    "first_turn": node.id,
                    "last_turn": node.id,
                    "before": before,
                    "after": after,
                    "turn_ids": [node.id],
                    "recoverability": mutation.get("recoverability", "exact"),
                    "unavailable_reason": mutation.get("unavailable_reason"),
                }
                continue
            if not _same_state(current["after"], before):
                current["recoverability"] = "unavailable"
                current["unavailable_reason"] = "discontinuous_journal"
            current["last_turn"] = node.id
            current["after"] = after
            current["turn_ids"].append(node.id)
    files = []
    stats_budget = [8 * 1024 * 1024]
    for lineage in lineages.values():
        if _same_state(lineage["before"], lineage["after"]):
            continue
        added, removed, binary, diff_state = _net_stats(
            session_dir,
            lineage["first_turn"],
            lineage["before"],
            lineage["last_turn"],
            lineage["after"],
            stats_budget,
        )
        before_kind = lineage["before"].get("kind")
        after_kind = lineage["after"].get("kind")
        operation = (
            "add" if before_kind == "absent" and after_kind == "regular"
            else "delete" if before_kind == "regular" and after_kind == "absent"
            else "modify"
        )
        files.append({
            "path": lineage["path"],
            "rel": _relative(lineage["path"], root),
            "op": operation,
            "added": added,
            "removed": removed,
            "binary": binary,
            "diff_state": diff_state,
            "recoverability": lineage["recoverability"],
            "unavailable_reason": lineage["unavailable_reason"],
            "turn_ids": lineage["turn_ids"],
        })
    return _scope_payload(
        "branch", "mutation_journal", files,
        head_id=index.head_id,
        _snapshot_basis=[{
            "path": lineage["path"],
            "before": lineage["before"],
            "after": lineage["after"],
            "turn_ids": lineage["turn_ids"],
        } for lineage in lineages.values()],
    )


def _git_output(
    root: Path,
    *args: str,
    timeout: float = 8.0,
    max_bytes: int = 8 * 1024 * 1024,
) -> bytes:
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(
            ["git", "-C", str(root), *args],
            stdout=stdout,
            stderr=stderr,
        )
        deadline = time.monotonic() + timeout
        while process.poll() is None:
            if stdout.tell() > max_bytes:
                process.kill()
                process.wait()
                raise _OutputLimitError("git output exceeds review limit")
            if time.monotonic() >= deadline:
                process.kill()
                process.wait()
                raise OSError("git command timed out")
            time.sleep(0.01)
        if process.returncode != 0:
            stderr.seek(0)
            raise OSError(stderr.read().decode("utf-8", errors="replace").strip())
        if stdout.tell() > max_bytes:
            raise _OutputLimitError("git output exceeds review limit")
        stdout.seek(0)
        return stdout.read(max_bytes + 1)


def _untracked_stats(path: Path) -> tuple[int | None, int | None, bool, str]:
    try:
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode):
            return None, None, False, "symlink"
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_DIFF_BYTES:
            return None, 0, False, "large"
        raw = path.read_bytes()
        if b"\0" in raw:
            return None, None, True, "binary"
        return len(raw.decode("utf-8", errors="replace").splitlines()), 0, False, "available"
    except OSError:
        return None, None, False, "unavailable"


def _workspace_identity(path: Path) -> dict:
    try:
        info = os.lstat(path)
        kind = (
            "regular" if stat.S_ISREG(info.st_mode)
            else "symlink" if stat.S_ISLNK(info.st_mode)
            else "other"
        )
        return {
            "kind": kind,
            "dev": info.st_dev,
            "ino": info.st_ino,
            "size": info.st_size,
            "mtime_ns": info.st_mtime_ns,
        }
    except FileNotFoundError:
        return {"kind": "absent"}
    except OSError:
        return {"kind": "unavailable"}


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
        raw_stats = _git_output(root, "diff", "--numstat", "-z", "HEAD", "--")
    except OSError as exc:
        return {
            "status": "unavailable", "scope": "workspace", "source": "git",
            "files": [], "file_count": 0, "error": str(exc),
        }
    stats: dict[str, tuple[int | None, int | None, str | None]] = {}
    stat_chunks = raw_stats.decode("utf-8", errors="replace").split("\0")
    stat_position = 0
    while stat_position < len(stat_chunks):
        header = stat_chunks[stat_position]
        stat_position += 1
        if not header:
            continue
        parts = header.split("\t", 2)
        if len(parts) != 3:
            continue
        added = int(parts[0]) if parts[0].isdigit() else None
        removed = int(parts[1]) if parts[1].isdigit() else None
        if parts[2]:
            stats[parts[2]] = (added, removed, None)
            continue
        if stat_position + 1 >= len(stat_chunks):
            break
        old_rel = stat_chunks[stat_position]
        new_rel = stat_chunks[stat_position + 1]
        stat_position += 2
        stats[new_rel] = (added, removed, old_rel)
    chunks = raw_status.decode("utf-8", errors="replace").split("\0")
    files = []
    position = 0
    while position < len(chunks):
        chunk = chunks[position]
        position += 1
        if len(chunk) < 4:
            continue
        code, rel = chunk[:2], chunk[3:]
        old_rel = None
        if "R" in code or "C" in code:
            old_rel = chunks[position] if position < len(chunks) else None
            position += 1
        added, removed, stat_old_rel = stats.get(rel, (0, 0, old_rel))
        old_rel = stat_old_rel or old_rel
        binary = added is None or removed is None
        diff_state = "binary" if binary else "available"
        if code == "??":
            added, removed, binary, diff_state = _untracked_stats(root / rel)
        op = (
            "rename" if "R" in code
            else "add" if code == "??" or "A" in code
            else "delete" if "D" in code
            else "modify"
        )
        files.append({
            "path": str(root / rel), "rel": rel, "op": op,
            "added": added, "removed": removed, "binary": binary,
            "diff_state": diff_state, "recoverability": "unavailable",
            "unavailable_reason": "workspace_scope", "turn_ids": [],
            "status_code": code,
            "old_rel": old_rel,
            "workspace_identity": _workspace_identity(root / rel),
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

    if not _valid_turn_id(turn_id):
        raise OSError("unsafe turn id")

    blob_ref = str(state["blob_ref"])
    if (
        not blob_ref
        or Path(blob_ref).is_absolute()
        or Path(blob_ref).name != blob_ref
        or "/" in blob_ref
        or "\\" in blob_ref
    ):
        raise OSError("unsafe recovery blob reference")
    directory = turn_backup_dir(session_dir, turn_id)
    directory_fd = _open_directory_no_symlinks(directory)
    try:
        descriptor = os.open(
            blob_ref,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError("unsafe recovery blob")
            if state.get("size") is not None and info.st_size != state.get("size"):
                raise OSError("recovery blob size mismatch")
            if info.st_size > _MAX_DIFF_BYTES:
                return b"", "large"
            raw = b""
            digest = hashlib.sha256()
            while True:
                chunk = os.read(descriptor, 64 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                raw += chunk
            if f"sha256:{digest.hexdigest()}" != state.get("digest"):
                raise OSError("recovery blob digest mismatch")
        finally:
            os.close(descriptor)
    finally:
        os.close(directory_fd)
    if b"\0" in raw:
        return b"", "binary"
    return raw, "available"


def _open_directory_no_symlinks(path: Path) -> int:
    absolute = Path(os.path.abspath(path))
    if not absolute.is_absolute():
        raise OSError("recovery directory must be absolute")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    parts = absolute.parts
    descriptor = os.open(parts[0], flags)
    try:
        for component in parts[1:]:
            child = os.open(component, flags, dir_fd=descriptor)
            info = os.fstat(child)
            if not stat.S_ISDIR(info.st_mode):
                os.close(child)
                raise OSError("unsafe recovery directory")
            os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _net_stats(
    session_dir: Path,
    before_turn: str,
    before: dict,
    after_turn: str,
    after: dict,
    budget: list[int],
) -> tuple[int | None, int | None, bool, str]:
    try:
        before_raw, before_state = _state_bytes(session_dir, before_turn, before)
        after_raw, after_state = _state_bytes(session_dir, after_turn, after)
    except OSError:
        return None, None, False, "unavailable"
    state = before_state if before_state != "available" else after_state
    if state != "available":
        return None, None, state == "binary", state
    cost = len(before_raw) + len(after_raw)
    if cost > budget[0]:
        return None, None, False, "timeout"
    budget[0] -= cost
    before_lines = before_raw.decode("utf-8", errors="replace").splitlines()
    after_lines = after_raw.decode("utf-8", errors="replace").splitlines()
    if len(before_lines) + len(after_lines) > 5_000:
        return None, None, False, "timeout"
    added = removed = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
        a=before_lines, b=after_lines, autojunk=True,
    ).get_opcodes():
        if tag in {"insert", "replace"}:
            added += j2 - j1
        if tag in {"delete", "replace"}:
            removed += i2 - i1
    return added, removed, False, "available"


def _render_diff(
    session_dir: Path,
    before_turn: str,
    before: dict,
    after_turn: str,
    after: dict,
    path: str,
    cursor: int = 0,
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
    lines = difflib.unified_diff(
        before_raw.decode("utf-8", errors="replace").splitlines(keepends=True),
        after_raw.decode("utf-8", errors="replace").splitlines(keepends=True),
        fromfile=f"a/{name}", tofile=f"b/{name}", n=3,
    )
    return _page_diff_lines(lines, cursor)


def _page_diff_lines(lines, cursor: int = 0) -> dict:
    start = max(0, cursor)
    page: list[str] = []
    byte_count = 0
    has_more = False
    consumed = 0
    old_line: int | None = None
    new_line: int | None = None
    hunk_pattern = re.compile(r"^@@\s+-(\d+)(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@")
    for index, line in enumerate(lines):
        if index < start:
            match = hunk_pattern.match(line)
            if match:
                old_line, new_line = int(match.group(1)), int(match.group(2))
            elif old_line is not None and new_line is not None:
                if line.startswith("-") and not line.startswith("---"):
                    old_line += 1
                elif line.startswith("+") and not line.startswith("+++"):
                    new_line += 1
                elif line.startswith(" "):
                    old_line += 1
                    new_line += 1
            continue
        if not page and start > 0 and old_line is not None and new_line is not None \
                and not line.startswith("@@"):
            synthetic = f"@@ -{old_line} +{new_line} @@\n"
            page.append(synthetic)
            byte_count += len(synthetic)
        encoded_size = len(line.encode("utf-8", errors="replace"))
        if encoded_size > _MAX_DIFF_LINE_BYTES:
            return {"diff": "", "diff_state": "large_line", "cursor": start}
        if len(page) >= _MAX_DIFF_LINES or byte_count + encoded_size > _MAX_DIFF_PAGE_BYTES:
            has_more = True
            break
        page.append(line)
        byte_count += encoded_size
        consumed += 1
    return {
        "diff": "".join(page),
        "diff_state": "available",
        "cursor": start,
        "next_cursor": start + consumed if has_more else None,
        "prev_cursor": None,
        "line_count": len(page),
    }


def _turn_file_diff(
    session_id: str, turn_id: str, path: str, cursor: int = 0,
) -> dict:
    opened = _open_session(session_id)
    if opened is None:
        return {"diff": "", "diff_state": "unavailable", "error": "unknown session"}
    _store, _git, index, session_dir = opened
    if not _valid_turn_id(turn_id) or turn_id not in index.nodes_by_id:
        return {"diff": "", "diff_state": "unavailable", "error": "unknown or unsafe turn"}
    mutation = next(
        (row for row in _manifest_mutations(session_dir, turn_id) if row.get("path") == path),
        None,
    )
    if mutation is None:
        return {"diff": "", "diff_state": "unavailable", "error": f"{path!r} not recorded for this turn"}
    return _render_diff(
        session_dir, turn_id, mutation.get("before") or {},
        turn_id, mutation.get("after") or {}, path, cursor,
    )


def _branch_file_diff(session_id: str, path: str, cursor: int = 0) -> dict:
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
        last_turn, last.get("after") or {}, path, cursor,
    )


def _workspace_file_diff(
    session_id: str,
    path: str,
    snapshot_id: str,
    cursor: int = 0,
) -> dict:
    root = _project_root(session_id)
    if root is None:
        return {"diff": "", "diff_state": "unavailable", "error": "workspace unavailable"}
    root = Path(os.path.abspath(root))
    candidate = Path(os.path.abspath(path))
    try:
        rel = str(candidate.relative_to(root))
    except (OSError, ValueError):
        return {"diff": "", "diff_state": "unavailable", "error": "path is outside workspace"}
    scope = _workspace_scope(session_id)
    if scope.get("status") != "ready" or scope.get("snapshot_id") != snapshot_id:
        return {"diff": "", "diff_state": "unavailable", "error": "stale workspace snapshot"}
    member = next(
        (row for row in scope.get("files", []) if Path(os.path.abspath(row["path"])) == candidate),
        None,
    )
    if member is None:
        return {"diff": "", "diff_state": "unavailable", "error": "path is not in workspace scope"}
    tracked = member.get("status_code") != "??"
    if tracked:
        try:
            raw = _git_output(
                root, "diff", "--no-ext-diff", "HEAD", "--", rel,
                max_bytes=_MAX_DIFF_BYTES,
            )
        except _OutputLimitError:
            return {"diff": "", "diff_state": "large"}
        except OSError as exc:
            return {"diff": "", "diff_state": "unavailable", "error": str(exc)}
        if len(raw) > _MAX_DIFF_BYTES:
            return {"diff": "", "diff_state": "large"}
        return _page_diff_lines(
            raw.decode("utf-8", errors="replace").splitlines(keepends=True),
            cursor,
        )
    try:
        raw = _read_workspace_file(root, rel, member.get("workspace_identity") or {})
    except OSError as exc:
        return {"diff": "", "diff_state": "unavailable", "error": str(exc)}
    if len(raw) > _MAX_DIFF_BYTES:
        return {"diff": "", "diff_state": "large"}
    if b"\0" in raw:
        return {"diff": "", "diff_state": "binary"}
    lines = difflib.unified_diff(
        [], raw.decode("utf-8", errors="replace").splitlines(keepends=True),
        fromfile="/dev/null", tofile=f"b/{rel}", n=3,
    )
    return _page_diff_lines(lines, cursor)


def _read_workspace_file(root: Path, rel: str, identity: dict) -> bytes:
    relative = Path(rel)
    if relative.is_absolute() or ".." in relative.parts or not relative.name:
        raise OSError("unsafe workspace path")
    if identity.get("kind") != "regular":
        raise OSError("workspace path is not a regular file")
    descriptor = os.open(
        root,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        for component in relative.parts[:-1]:
            child = os.open(
                component,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
        file_descriptor = os.open(
            relative.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=descriptor,
        )
        try:
            info = os.fstat(file_descriptor)
            if not stat.S_ISREG(info.st_mode) or (
                info.st_dev,
                info.st_ino,
                info.st_size,
                info.st_mtime_ns,
            ) != (
                identity.get("dev"),
                identity.get("ino"),
                identity.get("size"),
                identity.get("mtime_ns"),
            ):
                raise OSError("stale workspace snapshot")
            if info.st_size > _MAX_DIFF_BYTES:
                raise _OutputLimitError("workspace file exceeds review limit")
            return os.read(file_descriptor, _MAX_DIFF_BYTES + 1)
        finally:
            os.close(file_descriptor)
    finally:
        os.close(descriptor)


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
    cursor = int(cmd.get("cursor") or 0)
    result = (
        await _run(lambda: _turn_file_diff(session_id, turn_id, path, cursor))
        if session_id and turn_id and path
        else {"diff": "", "diff_state": "unavailable", "error": "session_id, assistant_msg_id and path are required"}
    )
    await ws.send_text(json.dumps({
        "type": "turn_file_diff_result",
        "data": {
            "session_id": session_id, "assistant_msg_id": turn_id, "path": path,
            "request_id": cmd.get("request_id"), "approximate": False, **result,
        },
    }, default=str))


async def handle_review_scope(ws, cmd: dict) -> None:
    session_id = (cmd.get("session_id") or "").strip()
    scope = (cmd.get("scope") or "turn").strip()
    turn_id = (cmd.get("assistant_msg_id") or "").strip()
    cursor = int(cmd.get("cursor") or 0)
    limit = int(cmd.get("limit") or _SCOPE_PAGE_SIZE)
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
    result = _page_scope(result, cursor, limit)
    await ws.send_text(json.dumps({
        "type": "review_scope_result",
        "data": {
            "session_id": session_id,
            "assistant_msg_id": turn_id,
            "request_id": cmd.get("request_id"),
            **result,
        },
    }, default=str))


async def handle_review_file_diff(ws, cmd: dict) -> None:
    session_id = (cmd.get("session_id") or "").strip()
    scope = (cmd.get("scope") or "turn").strip()
    turn_id = (cmd.get("assistant_msg_id") or "").strip()
    path = (cmd.get("path") or "").strip()
    snapshot_id = (cmd.get("snapshot_id") or "").strip()
    cursor = int(cmd.get("cursor") or 0)
    if not session_id or not path:
        result = {"diff": "", "diff_state": "unavailable", "error": "session_id and path are required"}
    else:
        current_scope = (
            await _run(lambda: _turn_scope(session_id, turn_id))
            if scope == "turn"
            else await _run(lambda: _branch_scope(session_id))
            if scope == "branch"
            else await _run(lambda: _workspace_scope(session_id))
            if scope == "workspace"
            else {"status": "error", "error": f"unknown review scope {scope!r}"}
        )
        member = next(
            (row for row in current_scope.get("files", []) if row.get("path") == path),
            None,
        )
        if (
            current_scope.get("status") != "ready"
            or not snapshot_id
            or current_scope.get("snapshot_id") != snapshot_id
        ):
            result = {"diff": "", "diff_state": "unavailable", "error": "stale review snapshot"}
        elif member is None:
            result = {"diff": "", "diff_state": "unavailable", "error": "path is not in review scope"}
        elif scope == "turn":
            result = await _run(lambda: _turn_file_diff(
                session_id, turn_id, path, cursor,
            ))
        elif scope == "branch":
            result = await _run(lambda: _branch_file_diff(
                session_id, path, cursor,
            ))
        else:
            result = await _run(lambda: _workspace_file_diff(
                session_id, path, snapshot_id, cursor,
            ))
    await ws.send_text(json.dumps({
        "type": "review_file_diff_result",
        "data": {
            "session_id": session_id, "assistant_msg_id": turn_id,
            "request_id": cmd.get("request_id"),
            "scope": scope, "path": path, "snapshot_id": snapshot_id, **result,
        },
    }, default=str))


async def handle_turn_history_state(ws, cmd: dict) -> None:
    session_id = (cmd.get("session_id") or "").strip()
    turn_id = (cmd.get("assistant_msg_id") or "").strip()
    from openprogram.webui import server as server
    if not session_id or not turn_id:
        result = {"status": "error", "action": None, "error": "session_id and assistant_msg_id are required"}
    elif server._is_run_active(session_id):
        result = {"status": "blocked", "action": None, "error": "run_active"}
    else:
        result = await _run(lambda: _history_eligibility(session_id, turn_id))
    await ws.send_text(json.dumps({
        "type": "turn_history_state_result",
        "data": {
            "session_id": session_id,
            "assistant_msg_id": turn_id,
            "request_id": cmd.get("request_id"),
            **result,
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
    "turn_history_state": handle_turn_history_state,
    "revert_turn": handle_revert_turn,
    "reapply_turn": handle_reapply_turn,
}
