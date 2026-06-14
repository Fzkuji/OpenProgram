"""Per-turn revert.

Exposes ``revert_turn(session_id, assistant_msg_id)`` to roll back the
file edits a single turn made, without touching chat history or
context commits (both are append-only by design — only the on-disk
files this turn touched are reverted).

The ``assistant_msg_id`` IS the natural turn key: dispatcher mints it
as ``user_msg_id + "_reply"`` (see dispatcher.process_user_turn) and
the file backup store keys captures by that same id.

Mutation on the DAG side is minimal — we stamp
``metadata['reverted'] = True`` on the assistant node (plus a
``reverted_at`` timestamp). Cheaper than pushing a new "revert" node;
the UI can read the stamp and overlay a strikethrough / badge.

Kept in a sibling module so ``dispatcher.py`` doesn't grow.
"""
from __future__ import annotations

import time
from typing import Any


def revert_turn(session_id: str, assistant_msg_id: str) -> dict[str, Any]:
    """Roll back the files this turn edited.

    Returns a dict::

        {
          "session_id": str,
          "assistant_msg_id": str,
          "restored_paths": [abs_path, ...],
          "metadata_stamped": bool,
          "error": Optional[str],   # only present on failure
        }

    Never raises — failure is reported via the ``error`` field so the
    WS layer can forward it to the UI verbatim.
    """
    if not session_id or not assistant_msg_id:
        return {
            "session_id": session_id or "",
            "assistant_msg_id": assistant_msg_id or "",
            "restored_paths": [],
            "metadata_stamped": False,
            "error": "session_id and assistant_msg_id are required",
        }

    try:
        from openprogram.store.session.session_store import default_store
        from openprogram.store.snapshot.file_backup import BackupStore
    except Exception as e:  # noqa: BLE001
        return {
            "session_id": session_id,
            "assistant_msg_id": assistant_msg_id,
            "restored_paths": [],
            "metadata_stamped": False,
            "error": f"import failed: {type(e).__name__}: {e}",
        }

    store = default_store()
    pair = store._open(session_id)
    if pair is None:
        return {
            "session_id": session_id,
            "assistant_msg_id": assistant_msg_id,
            "restored_paths": [],
            "metadata_stamped": False,
            "error": f"unknown session {session_id!r}",
        }
    git, idx = pair
    session_dir = git.path if hasattr(git, "path") else store._session_dir(session_id)

    # ── git-aware undo (if this turn produced a project commit) ──
    # When auto-commit landed a real git commit for this turn, undo it in
    # git too — choosing the safest op (clean reset if it's the tip and
    # unpushed, else an additive revert). This keeps the project's git
    # history consistent with the file state instead of leaving a
    # "phantom" commit whose changes the snapshot just rolled back.
    # Done BEFORE the snapshot restore: a successful git reset/revert
    # already sets the correct file state, and in the revert case the
    # snapshot must NOT then re-clobber the user's other commits — so we
    # only fall back to the snapshot when git couldn't do it.
    git_undo: dict[str, Any] | None = None
    node_for_meta = idx.nodes_by_id.get(assistant_msg_id)
    pc_meta = (node_for_meta.metadata or {}).get("project_commit") if node_for_meta else None
    if isinstance(pc_meta, dict) and pc_meta.get("sha") and pc_meta.get("repo"):
        try:
            from openprogram.store.project.project_store import ProjectGit
            git_undo = ProjectGit(pc_meta["repo"]).revert_agent_commit(pc_meta["sha"])
        except Exception as e:  # noqa: BLE001
            git_undo = {"action": "error", "ok": False, "detail": f"{type(e).__name__}: {e}"}

    # ── file-snapshot restore ──
    # The always-available fallback (works even for gitignored files and
    # non-git folders). We run it unless git ALREADY restored the exact
    # file state: a clean ``reset`` makes the snapshot redundant, and an
    # additive ``revert`` produced the correct merged state that the
    # snapshot would wrongly overwrite. For skipped/absent/error git
    # outcomes, the snapshot is what actually does the revert.
    git_did_files = bool(git_undo and git_undo.get("ok")
                         and git_undo.get("action") in ("reset", "revert"))
    backup = BackupStore(session_dir)
    restored: list[str] = []
    if not git_did_files:
        try:
            restored = backup.restore_turn(assistant_msg_id)
        except Exception as e:  # noqa: BLE001
            return {
                "session_id": session_id,
                "assistant_msg_id": assistant_msg_id,
                "restored_paths": [],
                "metadata_stamped": False,
                "git_undo": git_undo,
                "error": f"restore failed: {type(e).__name__}: {e}",
            }

    # Stamp the assistant node's metadata so the UI can show
    # "reverted" without re-querying the file backup manifest.
    metadata_stamped = False
    try:
        node = idx.nodes_by_id.get(assistant_msg_id)
        if node is not None:
            node.metadata = {
                **(node.metadata or {}),
                "reverted": True,
                "reverted_at": time.time(),
                "reverted_paths": list(restored),
            }
            # Rewrite the on-disk history file so a worker restart
            # picks up the stamp.
            try:
                role_letter = (node.role or "x")[0]
                fname = f"{node.seq:04d}-{role_letter}-{node.id}.json"
                fpath = git.path / "history" / fname
                if fpath.exists():
                    import json as _json
                    tmp = fpath.with_suffix(".json.tmp")
                    tmp.write_text(
                        _json.dumps(node.to_dict(), ensure_ascii=False,
                                    default=str)
                    , encoding="utf-8")
                    tmp.replace(fpath)
            except Exception:
                # In-memory stamp still works for the live process;
                # disk rewrite is best-effort.
                pass
            metadata_stamped = True
    except Exception:
        metadata_stamped = False

    return {
        "session_id": session_id,
        "assistant_msg_id": assistant_msg_id,
        "restored_paths": list(restored),
        "metadata_stamped": metadata_stamped,
        "git_undo": git_undo,
    }
