"""Session creation owns ID admission, placement and registry publication."""
from __future__ import annotations

from . import shared
from ..placement import validate_session_id


def create_session(
    store,
    session_id: str,
    agent_id: str,
    *,
    title: str = "",
    source: shared.Optional[str] = None,
    channel: shared.Optional[str] = None,
    peer_display: shared.Optional[str] = None,
    peer_id: shared.Optional[str] = None,
    **other_fields: shared.Any,
) -> None:
    validate_session_id(session_id)
    with store._session_lock(session_id), shared.session_interprocess_lock(
        session_id, root=store.root_path if store._explicit_root else None,
        reentrant=True,
    ):
        if shared.is_deleted(store.root_path, session_id):
            return
        pair = store._open(session_id)
        if pair is not None:
            meta = pair[0].read_meta()
            if meta.get("id") == session_id:
                _publish_registry(store, session_id, meta)
                return
        # Resolve project hints only after ruling out an existing session ID.
        # They select the initial application-owned storage directory.
        project_id = other_fields.pop("project_id", None)
        project_path = other_fields.pop("project_path", None)
        # The per-session ``work_dir`` (set by the user via the picker
        # at the top of the chat, stored on the conversation meta) IS
        # the project directory. If the caller didn't pass an explicit
        # ``project_path``, treat ``work_dir`` as the project to bind.
        # NB: we ``get`` (not ``pop``) work_dir — it stays on the meta
        # so ``resolve_work_dir`` keeps reading it for agent file ops.
        if not project_path:
            _wd = other_fields.get("work_dir")
            if isinstance(_wd, str) and _wd.strip():
                project_path = _wd.strip()

        # Bound conversations live under the state root, grouped by
        # project id. Working folders are never the conversation store.
        # An explicitly supplied root is a standalone embedding boundary;
        # with no project hint it must not consult process-wide state.
        if store._explicit_root and not project_path and not project_id:
            project_id = None
        else:
            try:
                from openprogram.store.project import project_store as _projects
                if project_path:
                    proj = _projects.resolve_project(project_path)
                elif project_id and project_id != _projects.DEFAULT_PROJECT_ID:
                    proj = _projects.get_project(project_id)
                    if proj is None:
                        raise ValueError(f"unknown project: {project_id}")
                else:
                    proj = _projects.get_default_project()
                # Isolated callers may intentionally disable the registry's
                # default project; those sessions retain the historical default
                # placement. Explicit bound project resolution failures still
                # propagate below and never fall back silently.
                if proj is None and not project_path and not project_id:
                    project_id = shared._projects_default_id_safe()
                else:
                    project_id = proj.id
                if proj is not None and (not proj.is_default) and proj.path:
                    repo_dir = shared.nested_session_dir(store.root_path, proj.id, session_id)
                    store._record_location(session_id, repo_dir)
                if proj is not None and not proj.is_default:
                    store._project_ids[session_id] = proj.id
            except Exception as e:  # noqa: BLE001 — placement is authoritative
                shared._log.error("project resolution failed for %s: %s", session_id, e)
                raise

        extra: dict[str, shared.Any] = {}
        if channel:
            extra["channel"] = channel
        if peer_display:
            extra["peer_display"] = peer_display
        if peer_id:
            extra["peer_id"] = peer_id
        for k, v in other_fields.items():
            if v is not None:
                extra[k] = v
        now = shared.time.time()
        # Caller-supplied created_at/updated_at (e.g. channel replay)
        # take precedence over the default ``now``; explicitly pop
        # them from ``extra`` so the **extra spread doesn't collide
        # with the named kwargs.
        created_at = extra.pop("created_at", now)
        updated_at = extra.pop("updated_at", now)

        if project_id:
            extra["project_id"] = project_id

        def admit(meta, idx):
            if meta.get("id") == session_id:
                return None
            meta.update(
                id=session_id,
                agent_id=agent_id,
                title=title,
                source=source or "",
                created_at=created_at,
                updated_at=updated_at,
                **extra,
            )
            meta["head_id"] = idx.head_id
            return shared.json.loads(shared.json.dumps(meta, ensure_ascii=False, default=str))

        meta = store._transform_session_meta(session_id, admit)
        if meta is None:
            pair = store._open(session_id)
            if pair is None:
                return
            meta = pair[0].read_meta()
        _publish_registry(store, session_id, meta)


def _publish_registry(store, session_id, meta):
    """Rebuild this ID's derived entries from its committed metadata."""
    store._update_index_entry(session_id, **store._meta_to_entry(meta))
    store._save_index()
    project_id = meta.get("project_id")
    if project_id:
        try:
            from openprogram.store.project import project_store as projects
            projects.bind_session(session_id, project_id)
        except Exception as exc:  # noqa: BLE001 — reverse index is best-effort
            shared._log.warning(
                "session %s NOT bound to project %s: %s", session_id, project_id, exc,
            )
