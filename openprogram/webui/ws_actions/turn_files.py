"""Per-turn file change review: list, diff, and revert (task H).

Wire format::

    in:  {"action": "list_turn_files", "session_id", "assistant_msg_id"}
    out: {"type": "list_turn_files_result",
          "data": {"session_id", "assistant_msg_id",
                   "files": [{"path", "rel", "op", "added", "removed"}],
                   "paths": [...],          # legacy, absolute paths
                   "reverted": bool,        # turn already undone
                   "error"?}}

    in:  {"action": "turn_file_diff", "session_id", "assistant_msg_id", "path"}
    out: {"type": "turn_file_diff_result",
          "data": {"session_id", "assistant_msg_id", "path",
                   "diff": str, "approximate": bool, "error"?}}

    in:  {"action": "revert_turn", "session_id", "msg_id"}
    out: {"type": "revert_turn_result",
          "data": {"session_id", "msg_id", "reverted_paths": [...],
                   "errors": [...]}}

Stats and diffs come from the shadow git repo when the turn was
recorded there (``metadata['shadow_git'] = {repo, before, after}``,
stamped by dispatcher/finalize step 6.93). Sessions predating that
wiring fall back to ``difflib`` over the checkpoint "before" copy vs
the file's current on-disk content, flagged ``approximate: true`` —
the current content may include later turns' edits.
"""
from __future__ import annotations

import asyncio
import difflib
import json
import os
from pathlib import Path
from typing import Any, Optional


# git's canonical empty tree — diffing against it yields "everything added",
# which is what a first-ever shadow commit should show.
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _open_session(session_id: str):
    """``(git, idx, session_dir)`` for a session, or None."""
    from openprogram.store.session.session_store import default_store

    store = default_store()
    pair = store._open(session_id)
    if pair is None:
        return None
    git, idx = pair
    session_dir = git.path if hasattr(git, "path") else store._session_dir(session_id)
    return git, idx, session_dir


def _shadow_meta(idx, assistant_msg_id: str) -> Optional[dict]:
    """The ``shadow_git`` stamp on the assistant node, if any."""
    node = idx.nodes_by_id.get(assistant_msg_id)
    if node is None:
        return None
    meta = (node.metadata or {}).get("shadow_git")
    if isinstance(meta, dict) and meta.get("after") and meta.get("repo"):
        return meta
    return None


def _relative_to(path: str, root: str) -> str:
    """``path`` relative to ``root``, or the basename when outside it."""
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve()))
    except (ValueError, OSError):
        return os.path.basename(path)


def _manifest_entries(session_dir, assistant_msg_id: str) -> list[dict]:
    """Checkpoint manifest rows: ``{path, pre_existing, backup}``."""
    from openprogram.store.snapshot.checkpoint import manifest
    from openprogram.store.snapshot.checkpoint.paths import turn_manifest_path

    man_path = turn_manifest_path(Path(session_dir), assistant_msg_id)
    rows = []
    for backup_name, entry in manifest.entries(man_path):
        path = entry.get("path") or ""
        if not path:
            continue
        rows.append({
            "path": path,
            "pre_existing": bool(entry.get("pre_existing")),
            "backup": backup_name,
        })
    return rows


def _op_for(row: dict) -> str:
    """add / delete / modify from the manifest row + current disk state."""
    exists = Path(row["path"]).exists()
    if not row["pre_existing"]:
        # Didn't exist at turn start: created (or created-then-removed).
        return "add" if exists else "delete"
    return "modify" if exists else "delete"


def _list_files(session_id: str, assistant_msg_id: str) -> dict:
    opened = _open_session(session_id)
    if opened is None:
        return {"files": [], "paths": [], "error": f"unknown session {session_id!r}"}
    _git, idx, session_dir = opened

    try:
        rows = _manifest_entries(session_dir, assistant_msg_id)
    except Exception as e:  # noqa: BLE001
        return {"files": [], "paths": [], "error": f"{type(e).__name__}: {e}"}

    meta = _shadow_meta(idx, assistant_msg_id)
    stats: dict[str, tuple[int, int]] = {}
    root = meta.get("repo") if meta else None
    if meta:
        try:
            from openprogram.store.shadow_git import ShadowGitStore
            before = meta.get("before") or _EMPTY_TREE
            stats = ShadowGitStore(meta["repo"]).numstat(before, meta["after"])
        except Exception:  # noqa: BLE001
            stats = {}

    files = []
    for row in rows:
        rel = _relative_to(row["path"], root) if root else os.path.basename(row["path"])
        added, removed = stats.get(rel, (0, 0))
        if not stats:
            # No shadow record — count from the checkpoint copy instead so
            # the chips still show real numbers on legacy sessions.
            added, removed = _difflib_counts(session_dir, assistant_msg_id, row)
        files.append({
            "path": row["path"],
            "rel": rel,
            "op": _op_for(row),
            "added": added,
            "removed": removed,
        })

    # `revert_turn` stamps metadata['reverted'] on the assistant node.
    # Replaying it here is what makes an undone turn still read as
    # "Reverted" after a reload, instead of offering Undo a second time.
    node = idx.nodes_by_id.get(assistant_msg_id)
    reverted = bool((getattr(node, "metadata", None) or {}).get("reverted"))

    return {
        "files": files,
        "paths": [f["path"] for f in files],
        "reverted": reverted,
    }


def _read_text(path: Path) -> Optional[list[str]]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    except (OSError, UnicodeDecodeError):
        return None


def _checkpoint_before(session_dir, assistant_msg_id: str, row: dict) -> list[str]:
    """The turn's "before" copy of one file, as lines (empty if created)."""
    from openprogram.store.snapshot.checkpoint.paths import turn_backup_dir

    if not row["pre_existing"]:
        return []
    src = Path(turn_backup_dir(Path(session_dir), assistant_msg_id)) / row["backup"]
    return _read_text(src) or []


def _difflib_counts(session_dir, assistant_msg_id: str, row: dict) -> tuple[int, int]:
    before = _checkpoint_before(session_dir, assistant_msg_id, row)
    after = _read_text(Path(row["path"])) or []
    added = removed = 0
    for line in difflib.unified_diff(before, after, n=0):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


def _file_diff(session_id: str, assistant_msg_id: str, path: str) -> dict:
    opened = _open_session(session_id)
    if opened is None:
        return {"diff": "", "approximate": False,
                "error": f"unknown session {session_id!r}"}
    _git, idx, session_dir = opened

    meta = _shadow_meta(idx, assistant_msg_id)
    if meta:
        try:
            from openprogram.store.shadow_git import ShadowGitStore
            rel = _relative_to(path, meta["repo"])
            before = meta.get("before") or _EMPTY_TREE
            text = ShadowGitStore(meta["repo"]).diff(before, meta["after"], rel)
            if text.strip():
                return {"diff": text, "approximate": False}
        except Exception:  # noqa: BLE001
            pass

    # Fallback: checkpoint "before" vs current disk content.
    try:
        rows = [r for r in _manifest_entries(session_dir, assistant_msg_id)
                if r["path"] == path]
    except Exception as e:  # noqa: BLE001
        return {"diff": "", "approximate": True, "error": f"{type(e).__name__}: {e}"}
    if not rows:
        return {"diff": "", "approximate": True,
                "error": f"{path!r} not recorded for this turn"}

    row = rows[0]
    before = _checkpoint_before(session_dir, assistant_msg_id, row)
    after = _read_text(Path(row["path"])) or []
    name = os.path.basename(path)
    text = "".join(difflib.unified_diff(
        before, after, fromfile=f"a/{name}", tofile=f"b/{name}",
    ))
    return {"diff": text, "approximate": True}


async def _run(fn) -> Any:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, fn)


async def handle_list_turn_files(ws, cmd: dict) -> None:
    session_id = (cmd.get("session_id") or "").strip()
    assistant_msg_id = (cmd.get("assistant_msg_id") or "").strip()

    if not session_id or not assistant_msg_id:
        payload = {
            "session_id": session_id,
            "assistant_msg_id": assistant_msg_id,
            "files": [],
            "paths": [],
            "error": "session_id and assistant_msg_id are required",
        }
    else:
        result = await _run(lambda: _list_files(session_id, assistant_msg_id))
        payload = {
            "session_id": session_id,
            "assistant_msg_id": assistant_msg_id,
            "files": result.get("files") or [],
            "paths": result.get("paths") or [],
            "reverted": bool(result.get("reverted")),
        }
        if result.get("error"):
            payload["error"] = result["error"]

    await ws.send_text(json.dumps({
        "type": "list_turn_files_result",
        "data": payload,
    }, default=str))


async def handle_turn_file_diff(ws, cmd: dict) -> None:
    session_id = (cmd.get("session_id") or "").strip()
    assistant_msg_id = (cmd.get("assistant_msg_id") or "").strip()
    path = (cmd.get("path") or "").strip()

    if not session_id or not assistant_msg_id or not path:
        payload = {
            "session_id": session_id,
            "assistant_msg_id": assistant_msg_id,
            "path": path,
            "diff": "",
            "approximate": False,
            "error": "session_id, assistant_msg_id and path are required",
        }
    else:
        result = await _run(lambda: _file_diff(session_id, assistant_msg_id, path))
        payload = {
            "session_id": session_id,
            "assistant_msg_id": assistant_msg_id,
            "path": path,
            "diff": result.get("diff") or "",
            "approximate": bool(result.get("approximate")),
        }
        if result.get("error"):
            payload["error"] = result["error"]

    await ws.send_text(json.dumps({
        "type": "turn_file_diff_result",
        "data": payload,
    }, default=str))


async def handle_revert_turn(ws, cmd: dict) -> None:
    session_id = (cmd.get("session_id") or "").strip()
    msg_id = (cmd.get("msg_id") or "").strip()

    from openprogram.agent.internals._revert import revert_turn

    result = await _run(lambda: revert_turn(session_id, msg_id))
    errors = []
    if result.get("error"):
        errors.append(result["error"])
    git_undo = result.get("git_undo")
    if isinstance(git_undo, dict) and git_undo.get("ok") is False:
        detail = git_undo.get("detail") or git_undo.get("action") or "git undo failed"
        errors.append(str(detail))

    await ws.send_text(json.dumps({
        "type": "revert_turn_result",
        "data": {
            "session_id": session_id,
            "msg_id": msg_id,
            "reverted_paths": result.get("restored_paths") or [],
            "errors": errors,
        },
    }, default=str))


ACTIONS = {
    "list_turn_files": handle_list_turn_files,
    "turn_file_diff": handle_turn_file_diff,
    "revert_turn": handle_revert_turn,
}
