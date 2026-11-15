"""Turn-context helpers used by trusted file-mutating tools."""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from .prompt_checkpoints import PromptCheckpoints, writers_after
from .store import CheckpointStore

_log = logging.getLogger(__name__)


# Graph isolation does not transfer ownership of the user turn's file edits.
_checkpoint_owner = ContextVar("checkpoint_owner", default=None)


@contextmanager
def _locked_checkpoint():
    from openprogram.state.store import _current_turn_id, _store
    from openprogram.state.store.session.session_lock import session_interprocess_lock

    shim = _store.get()
    turn_id = _current_turn_id.get()
    if _checkpoint_owner.get() is not None:
        shim, turn_id = _checkpoint_owner.get()
    if shim is None or not turn_id:
        yield None
        return
    with session_interprocess_lock(
        shim.session_id,
        root=shim.store.root_path if getattr(shim.store, "_explicit_root", False)
        else None,
    ):
        yield CheckpointStore(shim.store._session_dir(shim.session_id)), turn_id, shim


def _project_locator(shim, abs_path: str) -> dict | None:
    """Bind only to a registered project whose authoritative root owns path."""
    from openprogram.state.store.project import project_for_session
    from openprogram.state.store.project.location import bound_execution_state

    project = project_for_session(shim.session_id)
    if project is None or not getattr(project, "id", "") or not getattr(project, "path", ""):
        return None
    if bound_execution_state(project) is not None:
        return None
    root = Path(str(project.path)).expanduser()
    try:
        root = Path(os.path.normpath(os.path.abspath(root)))
        target = Path(os.path.normpath(os.path.abspath(abs_path)))
        relative = target.relative_to(root)
        resolved_root = root.resolve()
        resolved_target = target.resolve(strict=False)
        resolved_target.relative_to(resolved_root)
    except (OSError, ValueError):
        return None
    if not root.is_dir() or not relative.parts:
        return None
    return {
        "project_id": str(project.id),
        "path": relative.as_posix(),
        "recorded_root": str(root),
        "directory_identity": str(getattr(project, "directory_identity", "") or ""),
        "location_revision": int(getattr(project, "location_revision", 0) or 0),
    }


def _register(shim, turn_id: str, *, propagate: bool) -> None:
    try:
        from openprogram.state.store.document_history import DocumentHistory
        DocumentHistory().register_model_turn(
            shim.session_id, turn_id, session_store=shim.store,
        )
    except Exception:
        if propagate:
            raise


def _prompt_lineage(shim):
    """``follows(prompt, earlier)``: whether a prompt comes after another on one line."""
    def follows(prompt: str, earlier: str) -> bool:
        try:
            line = shim.store.get_branch(shim.session_id, prompt)
        except Exception:
            return False
        return any(message.get("id") == earlier for message in line[:-1])
    return follows


def _written_by_another_prompt(store: CheckpointStore, abs_path: str, follows=None):
    """Whether a turn of another prompt wrote the path after a checkpoint read it.

    Two branches run turns at once, so a file a checkpoint read when its
    prompt arrived may be edited by the other branch before this prompt's turn
    first edits it. Turns no checkpoint names are not counted. Neither are
    those of a prompt that comes after this one on its own line
    (``follows(prompt, earlier)``): a rewind to this prompt takes their work
    back too, so what the checkpoint read before them is the state to keep.
    """
    def check(owner: dict, others: list[dict], entry: dict) -> bool:
        since = int(entry.get("captured_sequence") or 0)
        session = store.session_dir.name
        if not any(writer == session for writer, _ in writers_after(abs_path, since)):
            return False

        def wrote(turn_ids, after: int) -> bool:
            return any(
                row.get("path") == abs_path and int(row.get("mutation_sequence") or 0) > after
                for turn in turn_ids for row in store.list_file_history(turn)
            )

        # Once this prompt's own turns have written the file, what the
        # checkpoint holds is the state from before them.
        if wrote(owner.get("turn_ids") or [], -1):
            return False
        mine = str(owner.get("user_msg_id"))
        return any(
            wrote(row.get("turn_ids") or [], since)
            and not (follows and follows(str(row.get("user_msg_id")), mine))
            for row in others
        )
    return check


def _track_for_prompt(
    store: CheckpointStore, abs_path: str, content_src: str | None, turn_id: str | None = None,
    follows=None,
) -> None:
    try:
        PromptCheckpoints(store.session_dir).track(
            abs_path, content_src, turn_id=turn_id,
            written_since=_written_by_another_prompt(store, abs_path, follows),
        )
    except Exception:
        _log.warning("prompt checkpoint did not track %s", abs_path, exc_info=True)


def capture_prompt_checkpoint(
    store, session_id: str, user_msg_id: str, turn_id: str | None = None,
) -> bool:
    """Record a prompt's recovery point and the turn it starts. Failure never reaches the turn."""
    try:
        PromptCheckpoints(store._session_dir(session_id)).capture(user_msg_id, turn_id)
        return True
    except Exception:
        _log.warning("prompt checkpoint failed for %s", session_id, exc_info=True)
        return False


def join_prompt_checkpoint(
    store, session_id: str, turn_id: str, *, spawned: bool = False,
    spawned_by: str | None = None, ancestors=None,
) -> str | None:
    """Attach a turn that has no checkpoint of its own to the one it belongs to.

    A sub-agent's turn (``spawned``) joins the turn that spawned it, named by
    ``spawned_by`` or found in the calling context. Any other turn joins the
    nearest earlier prompt on its branch; ``ancestors`` returns message ids,
    nearest first. Returns the prompt's id, if there is one.
    """
    try:
        checkpoints = PromptCheckpoints(store._session_dir(session_id))
        if not checkpoints.ids():
            return None
        candidates: list[str] = []
        if spawned:
            from openprogram.state.store import _current_turn_id

            owner = _checkpoint_owner.get()
            ambient = owner[1] if owner is not None else _current_turn_id.get()
            candidates = [turn for turn in (spawned_by, ambient) if turn and turn != turn_id]
        return checkpoints.bind_turn(turn_id, candidates) or (
            checkpoints.bind_turn(turn_id, ancestors()) if ancestors else None
        )
    except Exception:
        _log.warning("prompt checkpoint not joined for %s", session_id, exc_info=True)
        return None


def checkpoint_before_edit(abs_path: str, content_src: str | None = None) -> bool:
    """Persist a prepared receipt before a trusted mutator writes."""
    if not abs_path or not os.path.isabs(abs_path):
        return False
    with _locked_checkpoint() as active:
        if active is None:
            return False
        store, turn_id, shim = active
        _track_for_prompt(store, abs_path, content_src, turn_id, _prompt_lineage(shim))
        store.backup_before_edit(
            turn_id, abs_path, content_src=content_src,
            project_locator=_project_locator(shim, abs_path),
        )
        _register(shim, turn_id, propagate=True)
        return True


def checkpoint_after_edit(abs_path: str, operation: str | None = None) -> bool:
    """Commit a prepared receipt after the filesystem mutation succeeds."""
    if not abs_path or not os.path.isabs(abs_path):
        return False
    with _locked_checkpoint() as active:
        if active is None:
            return False
        store, turn_id, shim = active
        store.commit_after_edit(turn_id, abs_path, operation=operation)
        _register(shim, turn_id, propagate=True)
        return True


def checkpoint_abort_edit(abs_path: str, error: str | None = None) -> None:
    if not abs_path:
        return
    with _locked_checkpoint() as active:
        if active is None:
            return
        store, turn_id, shim = active
        store.abort_edit(turn_id, abs_path, error)
        _register(shim, turn_id, propagate=False)
