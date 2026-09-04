"""Review diff and bounded workspace content implementation."""
from __future__ import annotations

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

from . import turn_files_shared as _shared
from .turn_files_shared import (
    _MAX_DIFF_BYTES, _MAX_DIFF_LINE_BYTES, _MAX_DIFF_LINES,
    _MAX_DIFF_PAGE_BYTES, _MAX_REVIEW_SNAPSHOT_BYTES,
    _MAX_REVIEW_SNAPSHOT_ITEMS, _REVIEW_CATEGORIES, _REVIEW_SCOPES,
    _REVIEW_SNAPSHOT_TTL, _setting, _valid_turn_id,
)


def _project_root(session_id: str) -> Path | None:
    return _shared._project_root(session_id)


from .turn_files_diff_shared import (
    _net_stats, _open_directory_no_symlinks, _same_state, _state_bytes,
)
from .turn_files_scope import (
    _OutputLimitError, _ReviewContentBudget, _active_nodes, _branch_scope,
    _get_review_cursor, _get_review_snapshot, _history_eligibility,
    _manifest_mutations, _new_review_cursor, _open_session, _page_scope,
    _relative, _review_category, _review_filter_files, _review_value_bytes,
    _scope_payload, _snapshot_instance_id, _tombstone_review_snapshot,
    _totals, _turn_scope, _turn_summary,
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


def _git_blob_state(
    root: Path, tree: str, rel: str, budget: _ReviewContentBudget | None = None,
) -> tuple[dict, bytes | None]:
    try:
        raw = _git_output(root, "ls-tree", "-z", tree, "--", rel)
        entry = raw.split(b"\0", 1)[0]
        if not entry:
            return {"kind": "absent"}, None
        header, _path = entry.split(b"\t", 1)
        mode, _kind, object_id = header.split()
        size = int(_git_output(root, "cat-file", "-s", object_id.decode()).strip())
        content = None
        if size <= _MAX_DIFF_BYTES:
            if budget is not None:
                budget.reserve(size)
            try:
                content = _git_output(
                    root, "cat-file", "blob", object_id.decode(), max_bytes=size,
                )
            except Exception:
                if budget is not None:
                    budget.release(size)
                raise
        digest = (
            "sha256:" + hashlib.sha256(content).hexdigest()
            if content is not None
            else "git:" + object_id.decode()
        )
        return {
            "kind": "blob", "digest": digest, "mode": mode.decode(), "size": size,
        }, content
    except _OutputLimitError:
        raise
    except (OSError, ValueError):
        return {"kind": "unavailable"}, None


def _index_blob_state(
    root: Path, rel: str, budget: _ReviewContentBudget | None = None,
) -> tuple[dict, bytes | None]:
    try:
        raw = _git_output(root, "ls-files", "--stage", "-z", "--", rel)
        entry = raw.split(b"\0", 1)[0]
        if not entry:
            return {"kind": "absent"}, None
        header, _path = entry.split(b"\t", 1)
        mode, object_id, _stage = header.split()
        size = int(_git_output(root, "cat-file", "-s", object_id.decode()).strip())
        state = {
            "kind": "blob", "digest": "git:" + object_id.decode(),
            "mode": mode.decode(), "size": size,
        }
        content = None
        if size <= _MAX_DIFF_BYTES:
            if budget is not None:
                budget.reserve(size)
            try:
                content = _git_output(
                    root, "cat-file", "blob", object_id.decode(), max_bytes=size,
                )
            except Exception:
                if budget is not None:
                    budget.release(size)
                raise
        return state, content
    except _OutputLimitError:
        raise
    except (OSError, ValueError):
        return {"kind": "unavailable"}, None


def _workspace_content_state(
    root: Path, rel: str, budget: _ReviewContentBudget | None = None,
) -> tuple[dict, bytes | None]:
    try:
        relative = Path(rel)
        if relative.is_absolute() or ".." in relative.parts or not relative.name:
            raise OSError("unsafe workspace path")
        descriptor = os.open(
            root,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
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
                identity = {
                    "kind": (
                        "regular" if stat.S_ISREG(info.st_mode)
                        else "symlink" if stat.S_ISLNK(info.st_mode) else "other"
                    ),
                    "dev": info.st_dev, "ino": info.st_ino,
                    "size": info.st_size, "mtime_ns": info.st_mtime_ns,
                }
                if identity["kind"] != "regular":
                    return identity, None
                reserved = info.st_size <= _MAX_DIFF_BYTES
                reserved_size = info.st_size if reserved and budget is not None else 0
                if reserved and budget is not None:
                    budget.reserve(info.st_size)
                chunks: list[bytes] = []
                digest = hashlib.sha256()
                total = 0
                try:
                    while True:
                        chunk = os.read(file_descriptor, 64 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)
                        total += len(chunk)
                        if reserved and total <= info.st_size:
                            chunks.append(chunk)
                        elif reserved:
                            chunks.clear()
                            reserved = False
                except Exception:
                    if reserved_size:
                        budget.release(reserved_size)
                    raise
                state = {
                    **identity,
                    "digest": "sha256:" + digest.hexdigest(),
                    "mode": format(stat.S_IMODE(info.st_mode), "04o"),
                }
                if total != info.st_size:
                    if reserved_size:
                        budget.release(reserved_size)
                    return state, None
                return state, b"".join(chunks) if reserved else None
            finally:
                os.close(file_descriptor)
        finally:
            os.close(descriptor)
    except _OutputLimitError:
        raise
    except FileNotFoundError:
        return {"kind": "absent"}, None
    except OSError:
        return {"kind": "unavailable"}, None


def _workspace_scope(
    session_id: str, *, category: str = "All", query: str = "", sort: str = "path",
) -> dict:
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
    if len(files) > _setting("_MAX_REVIEW_SNAPSHOT_ITEMS"):
        return {
            "status": "unavailable", "scope": "workspace", "source": "git",
            "files": [], "file_count": 0, "error": "REVIEW_SNAPSHOT_LIMIT",
        }
    try:
        head_identity = _git_output(root, "rev-parse", "HEAD").decode().strip()
        head_tree_identity = _git_output(root, "rev-parse", "HEAD^{tree}").decode().strip()
        index_vector = _git_output(root, "ls-files", "--stage", "-z")
        index_identity = "sha256:" + hashlib.sha256(index_vector).hexdigest()
    except OSError as exc:
        return {
            "status": "unavailable", "scope": "workspace", "source": "git",
            "files": [], "file_count": 0, "error": str(exc),
        }
    candidate_states: dict[str, dict] = {}
    snapshot_basis = []
    content_budget = _ReviewContentBudget(_setting("_MAX_REVIEW_SNAPSHOT_BYTES"))
    try:
        for row in files:
            base_rel = row.get("old_rel") or row["rel"]
            base, base_content = _git_blob_state(root, "HEAD", base_rel, content_budget)
            index, index_content = _index_blob_state(root, row["rel"], content_budget)
            worktree, worktree_content = _workspace_content_state(
                root, row["rel"], content_budget,
            )
            row.update({"base": base, "index": index, "worktree": worktree})
            if (
                (base.get("kind") == "blob" and base_content is None)
                or (index.get("kind") == "blob" and index_content is None)
                or (worktree.get("kind") == "regular" and worktree_content is None)
            ):
                row["diff_state"] = "large"
                row["binary"] = False
            # Preserve the old field used by the workspace diff boundary.
            row["workspace_identity"] = {
                key: value for key, value in worktree.items()
                if key in {"kind", "dev", "ino", "size", "mtime_ns"}
            }
            candidate_states[row["path"]] = {
                "base": b"" if base.get("kind") == "absent" else base_content,
                "index": b"" if index.get("kind") == "absent" else index_content,
                "worktree": b"" if worktree.get("kind") == "absent" else worktree_content,
            }
            snapshot_basis.append({
                key: row.get(key)
                for key in ("path", "rel", "old_rel", "status_code", "base", "index", "worktree")
            })
    except _OutputLimitError:
        # Discard every retained pair before returning; do not construct a
        # partially reproducible snapshot after the budget is exhausted.
        candidate_states.clear()
        snapshot_basis.clear()
        files.clear()
        return {
            "status": "unavailable", "scope": "workspace", "source": "git",
            "files": [], "file_count": 0, "error": "REVIEW_SNAPSHOT_LIMIT",
        }
    return _scope_payload(
        "workspace", "git", files,
        root=str(root), ignored_policy="exclude_standard",
        category=category,
        query=query,
        sort=sort,
        _snapshot_owner={"session_id": session_id, "root": str(root)},
        _snapshot_basis={
            "head_identity": head_identity,
            "head_tree_identity": head_tree_identity,
            "index_identity": index_identity,
            "candidates": snapshot_basis,
        },
        _snapshot_store={
            "workspace": {
                "root": str(root),
                "head_identity": head_identity,
                "head_tree_identity": head_tree_identity,
                "index_identity": index_identity,
                "candidates": candidate_states,
            },
        },
    )


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


def _review_turn_file_diff(
    session_id: str, member: dict, path: str, cursor: int = 0,
) -> dict:
    first_turn = str(member.get("first_turn_id") or "")
    last_turn = str(member.get("last_turn_id") or "")
    if not first_turn or not last_turn:
        return {"diff": "", "diff_state": "unavailable", "error": "turn lineage is unavailable"}
    opened = _open_session(session_id)
    if opened is None:
        return {"diff": "", "diff_state": "unavailable", "error": "unknown session"}
    _store, _git, _index, session_dir = opened
    first = next(
        (row for row in _manifest_mutations(session_dir, first_turn) if row.get("path") == path),
        None,
    )
    last = next(
        (row for row in _manifest_mutations(session_dir, last_turn) if row.get("path") == path),
        None,
    )
    if first is None or last is None:
        return {"diff": "", "diff_state": "unavailable", "error": "turn lineage is unavailable"}
    return _render_diff(
        session_dir,
        first_turn,
        first.get("before") or {},
        last_turn,
        last.get("after") or {},
        path,
        cursor,
    )


def _branch_file_diff(session_id: str, path: str, cursor: int = 0) -> dict:
    opened = _open_session(session_id)
    if opened is None:
        return {"diff": "", "diff_state": "unavailable", "error": "unknown session"}
    _store, _git, index, session_dir = opened
    states = []
    active_llm = [
        node for node in reversed(_active_nodes(index))
        if node.role == "llm" and not (node.metadata or {}).get("reverted")
    ]
    from openprogram.agent.history_ownership import owned_change_set_closure

    ownership = owned_change_set_closure(
        session_id, [node.id for node in active_llm],
    )
    producer_nodes = active_llm + [
        index.nodes_by_id[turn_id]
        for turn_id in ownership["owned_turn_ids"]
        if turn_id in index.nodes_by_id
    ]
    producer_nodes.sort(key=lambda node: node.seq)
    states = [
        (node.id, mutation)
        for node in producer_nodes
        if node.role == "llm"
        for mutation in _manifest_mutations(session_dir, node.id)
        if mutation.get("path") == path
    ]
    if states and all(
        isinstance(mutation.get("mutation_sequence"), int)
        for _turn_id, mutation in states
    ):
        states.sort(key=lambda item: item[1]["mutation_sequence"])
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


def _render_content_diff(
    before: bytes | None, after: bytes | None, path: str, cursor: int = 0,
) -> dict:
    if before is None or after is None:
        return {"diff": "", "diff_state": "large"}
    if b"\0" in before or b"\0" in after:
        return {"diff": "", "diff_state": "binary"}
    lines = difflib.unified_diff(
        before.decode("utf-8", errors="replace").splitlines(keepends=True),
        after.decode("utf-8", errors="replace").splitlines(keepends=True),
        fromfile=f"a/{os.path.basename(path)}",
        tofile=f"b/{os.path.basename(path)}",
        n=3,
    )
    return _page_diff_lines(lines, cursor)


def _render_workspace_content_diff(
    content: dict, path: str, cursor: int = 0,
) -> dict:
    """Render both base→index and index→worktree segments."""
    before = content.get("base")
    index = content.get("index")
    worktree = content.get("worktree")
    if before is None or index is None or worktree is None:
        return {"diff": "", "diff_state": "large"}
    if any(b"\0" in value for value in (before, index, worktree)):
        return {"diff": "", "diff_state": "binary"}

    name = os.path.basename(path)
    segments: list[str] = []
    for label, left, right in (
        ("base-to-index", before, index),
        ("index-to-worktree", index, worktree),
    ):
        if left == right:
            continue
        segments.extend(difflib.unified_diff(
            left.decode("utf-8", errors="replace").splitlines(keepends=True),
            right.decode("utf-8", errors="replace").splitlines(keepends=True),
            fromfile=f"a/{name} [{label}]",
            tofile=f"b/{name} [{label}]",
            n=3,
        ))
    return _page_diff_lines(segments, cursor)


def _diff_blob_pair(member: dict) -> str:
    return hashlib.sha256(json.dumps({
        key: member.get(key)
        for key in (
            "path", "first_turn_id", "last_turn_id", "before", "after",
            "base", "index", "worktree", "diff_state",
        )
    }, sort_keys=True, default=str).encode()).hexdigest()


def _resolve_diff_cursor(
    cursor: Any, snapshot_id: str, snapshot_epoch: int | None,
    path: str, member: dict, limit: int,
) -> tuple[bool, int]:
    if not cursor:
        return True, 0
    token = _get_review_cursor(cursor, "diff")
    if token is None or any((
        token.get("snapshot_id") != snapshot_id,
        token.get("epoch") != snapshot_epoch,
        token.get("path") != path,
        token.get("blob_pair") != _diff_blob_pair(member),
        token.get("limit") != limit,
    )):
        return False, 0
    return True, token.get("offset", 0)


def _bind_diff_page(
    result: dict, cursor: Any, snapshot_id: str, path: str, member: dict,
    limit: int, snapshot_epoch: int | None,
) -> dict:
    if result.get("diff_state") != "available":
        return result
    result = {**result, "cursor": cursor or None}
    pair = _diff_blob_pair(member)
    if isinstance(result.get("next_cursor"), int):
        result["next_cursor"] = _new_review_cursor({
            "kind": "diff", "snapshot_id": snapshot_id, "epoch": snapshot_epoch,
            "path": path,
            "blob_pair": pair, "limit": limit,
            "offset": result["next_cursor"],
        })
    return result


def _workspace_file_diff(
    session_id: str,
    path: str,
    snapshot_id: str,
    cursor: int = 0,
) -> dict:
    root = _project_root(session_id)
    snapshot = _get_review_snapshot(snapshot_id)
    if root is None or snapshot is None:
        return {"diff": "", "diff_state": "unavailable", "error": "workspace unavailable"}
    root = Path(os.path.abspath(root))
    if snapshot.get("scope") != "workspace" or snapshot.get("owner", {}).get("session_id") != session_id:
        return {"diff": "", "diff_state": "unavailable", "error": "STALE_SNAPSHOT"}
    candidate = Path(os.path.abspath(path))
    try:
        rel = str(candidate.relative_to(root))
    except (OSError, ValueError):
        return {"diff": "", "diff_state": "unavailable", "error": "path is outside workspace"}
    scope = _workspace_scope(
        session_id,
        category=snapshot.get("category", "All"),
        query=snapshot.get("query", ""),
        sort=snapshot.get("sort", "path"),
    )
    if scope.get("status") != "ready" or scope.get("snapshot_id") != snapshot_id:
        return {"diff": "", "diff_state": "unavailable", "error": "STALE_SNAPSHOT"}
    member = next(
        (row for row in snapshot.get("files", []) if Path(os.path.abspath(row["path"])) == candidate),
        None,
    )
    if member is None:
        return {"diff": "", "diff_state": "unavailable", "error": "path is not in workspace scope"}
    if (member.get("worktree") or {}).get("kind") not in {"regular", "absent"}:
        return {
            "diff": "", "diff_state": "unavailable",
            "error": "workspace path is not a regular file",
        }
    content = snapshot.get("workspace", {}).get("candidates", {}).get(member["path"])
    if content is None:
        return {"diff": "", "diff_state": "unavailable", "error": "STALE_SNAPSHOT"}
    return _render_workspace_content_diff(content, rel, cursor)


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


__all__ = [
    name for name in globals()
    if name.startswith("_") and not name.startswith("__")
]
