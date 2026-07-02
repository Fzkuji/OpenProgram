"""Project & working-directory WS actions — the topbar project picker.

Exposes the project entity layer (``store.project_store``) plus the
per-session working-directory set over the WebSocket, so the web UI can
mirror Claude's composer chips:

  * a **main project** chip — which project this conversation belongs to
    (decides where the session repo is stored: ``<project>/.openprogram
    /sessions/<id>/``). One per session.
  * **additional directory** chips — extra folders the agent may read /
    write in this session, beyond the main project. Stored in the
    session meta as ``workdirs`` (the main project path is index 0;
    additional dirs follow).

Actions:
    list_projects          → all registered projects + current session's
                             project_id
    create_project         → bind a filesystem path as a project (git-
                             init if needed); returns the project
    remove_project         → unregister a project (does NOT delete files)
    set_session_project    → set the session's MAIN project (label +
                             reverse index)
    list_session_workdirs  → the session's working-directory set
    add_session_workdir    → add an additional directory
    remove_session_workdir → drop an additional directory

State / git work lives in ``store.project_store`` / the session store;
these handlers only marshal requests and shape JSON.
"""
from __future__ import annotations

import json
import os


def _project_dict(p, alive: set[str] | None = None) -> dict:
    # session_count 只数**存活**会话，不裸信可能含孤立引用的 session_ids。
    sids = p.session_ids or []
    count = len([s for s in sids if s in alive]) if alive is not None else len(sids)
    return {
        "id": p.id,
        "name": p.name,
        "path": p.path,
        "is_default": p.is_default,
        "session_count": count,
        "status": p.status,
    }


def _alive_session_ids() -> set[str]:
    """当前真实存在的会话 id 集（SessionDB 有内容的）。"""
    try:
        from openprogram.agent.session_db import default_db
        return {r.get("id") for r in default_db().list_sessions(limit=100_000) if r.get("id")}
    except Exception:
        return set()


def _session_meta(session_id: str) -> dict:
    try:
        from openprogram.agent.session_db import default_db
        return default_db().get_session(session_id) or {}
    except Exception:
        return {}


# Projects


async def handle_list_projects(ws, cmd: dict):
    session_id = (cmd.get("session_id") or "").strip() or None
    projects: list[dict] = []
    current_project_id: str | None = None
    try:
        from openprogram.store import project_store as _projects
        _projects.get_default_project()  # ensure the default label exists
        alive = _alive_session_ids()
        _projects.prune_sessions(alive)   # 清孤立引用（修 882-bug），只增不减的历史遗留
        projects = [_project_dict(p, alive) for p in _projects.list_projects()]
        if session_id:
            cur = _projects.project_for_session(session_id)
            current_project_id = cur.id if cur else None
    except Exception:
        projects = []
    await ws.send_text(json.dumps({
        "type": "projects_list",
        "data": {
            "projects": projects,
            "current_project_id": current_project_id,
            "session_id": session_id,
        },
    }, default=str))


async def handle_create_project(ws, cmd: dict):
    path = (cmd.get("path") or "").strip()
    name = (cmd.get("name") or "").strip() or None
    ok, error, proj_dict = False, None, None
    if not path:
        error = "path is required"
    elif not os.path.isdir(os.path.expanduser(path)):
        error = f"not a directory: {path}"
    else:
        try:
            from openprogram.store import project_store as _projects
            proj = _projects.resolve_project(path, name=name)
            proj_dict = _project_dict(proj)
            ok = True
        except Exception as e:  # noqa: BLE001
            error = f"{type(e).__name__}: {e}"
    await ws.send_text(json.dumps({
        "type": "project_created",
        "data": {"ok": ok, "project": proj_dict, "error": error},
    }, default=str))
    if ok:
        await handle_list_projects(ws, {"session_id": cmd.get("session_id")})


async def handle_remove_project(ws, cmd: dict):
    project_id = (cmd.get("project_id") or "").strip()
    ok, error = False, None
    try:
        from openprogram.store import project_store as _projects
        if project_id == _projects.DEFAULT_PROJECT_ID:
            error = "cannot remove the default project"
        else:
            reg_p = _projects._registry_path()
            reg = json.loads(reg_p.read_text(encoding="utf-8")) if reg_p.exists() else {}
            if project_id in reg:
                reg.pop(project_id, None)
                reg_p.write_text(json.dumps(reg, indent=2, ensure_ascii=False), encoding="utf-8")
                ok = True
            else:
                error = "unknown project"
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"
    await ws.send_text(json.dumps({
        "type": "project_removed",
        "data": {"ok": ok, "project_id": project_id, "error": error},
    }, default=str))
    if ok:
        await handle_list_projects(ws, {"session_id": cmd.get("session_id")})


async def handle_set_session_project(ws, cmd: dict):
    session_id = (cmd.get("session_id") or "").strip()
    project_id = (cmd.get("project_id") or "").strip()
    ok, error = False, None
    if not session_id or not project_id:
        error = "session_id and project_id are required"
    else:
        try:
            from openprogram.store import project_store as _projects
            from openprogram.agent.session_db import default_db
            if _projects.get_project(project_id) is None:
                error = "unknown project"
            else:
                default_db().update_session(session_id, project_id=project_id)
                _projects.bind_session(session_id, project_id)
                ok = True
        except Exception as e:  # noqa: BLE001
            error = f"{type(e).__name__}: {e}"
    await ws.send_text(json.dumps({
        "type": "session_project_set",
        "data": {"ok": ok, "session_id": session_id, "project_id": project_id, "error": error},
    }, default=str))


# Additional working directories


def _resolve_workdirs(session_id: str) -> list[str]:
    """The session's working-directory set: main project path first
    (if any), then the explicit additional dirs from meta.workdirs."""
    meta = _session_meta(session_id)
    dirs: list[str] = []
    # main project path
    try:
        from openprogram.store import project_store as _projects
        proj = _projects.project_for_session(session_id)
        if proj and proj.path:
            dirs.append(proj.path)
    except Exception:
        pass
    for d in (meta.get("workdirs") or []):
        if d and d not in dirs:
            dirs.append(d)
    return dirs


async def handle_list_session_workdirs(ws, cmd: dict):
    session_id = (cmd.get("session_id") or "").strip()
    await ws.send_text(json.dumps({
        "type": "session_workdirs",
        "data": {"session_id": session_id, "workdirs": _resolve_workdirs(session_id)},
    }, default=str))


async def handle_add_session_workdir(ws, cmd: dict):
    session_id = (cmd.get("session_id") or "").strip()
    path = (cmd.get("path") or "").strip()
    ok, error = False, None
    if not session_id or not path:
        error = "session_id and path are required"
    elif not os.path.isdir(os.path.expanduser(path)):
        error = f"not a directory: {path}"
    else:
        try:
            from openprogram.agent.session_db import default_db
            db = default_db()
            meta = db.get_session(session_id) or {}
            dirs = list(meta.get("workdirs") or [])
            ap = os.path.abspath(os.path.expanduser(path))
            if ap not in dirs:
                dirs.append(ap)
            db.update_session(session_id, workdirs=dirs)
            ok = True
        except Exception as e:  # noqa: BLE001
            error = f"{type(e).__name__}: {e}"
    await ws.send_text(json.dumps({
        "type": "session_workdir_added",
        "data": {"ok": ok, "session_id": session_id, "path": path, "error": error},
    }, default=str))
    if ok:
        await handle_list_session_workdirs(ws, {"session_id": session_id})


async def handle_remove_session_workdir(ws, cmd: dict):
    session_id = (cmd.get("session_id") or "").strip()
    path = (cmd.get("path") or "").strip()
    ok, error = False, None
    try:
        from openprogram.agent.session_db import default_db
        db = default_db()
        meta = db.get_session(session_id) or {}
        ap = os.path.abspath(os.path.expanduser(path))
        dirs = [d for d in (meta.get("workdirs") or []) if d not in (path, ap)]
        db.update_session(session_id, workdirs=dirs)
        ok = True
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"
    await ws.send_text(json.dumps({
        "type": "session_workdir_removed",
        "data": {"ok": ok, "session_id": session_id, "path": path, "error": error},
    }, default=str))
    if ok:
        await handle_list_session_workdirs(ws, {"session_id": session_id})


# 项目级默认设置（新会话回落值）。只留已接通生效的键——见
# session_config.project_defaults 的读取方。
_PROJECT_CONFIG_KEYS = ("permission_mode", "thinking_effort")


async def handle_get_project_config(ws, cmd: dict):
    """返回项目级默认配置（作为新会话默认值）。存 project settings.json。"""
    project_id = (cmd.get("project_id") or "").strip()
    cfg: dict = {}
    try:
        from openprogram.store import project_store as _projects
        s = _projects.load_project_settings(project_id)
        cfg = {k: s.get(k) for k in _PROJECT_CONFIG_KEYS if s.get(k) is not None}
    except Exception:
        cfg = {}
    await ws.send_text(json.dumps({
        "type": "project_config",
        "data": {"project_id": project_id, "config": cfg},
    }, default=str))


async def handle_set_project_config(ws, cmd: dict):
    """设一个项目级配置字段（key ∈ _PROJECT_CONFIG_KEYS）。value=None 清除。"""
    project_id = (cmd.get("project_id") or "").strip()
    key = cmd.get("key")
    value = cmd.get("value")
    if project_id and key in _PROJECT_CONFIG_KEYS:
        try:
            from openprogram.store import project_store as _projects
            s = _projects.load_project_settings(project_id)
            if value in (None, "", "inherit"):
                s.pop(key, None)
            else:
                s[key] = value
            _projects.save_project_settings(project_id, s)
        except Exception:
            pass
    await handle_get_project_config(ws, {"project_id": project_id})


async def handle_list_project_sessions(ws, cmd: dict):
    """返回某项目下的**存活**会话摘要（id/title/created_at），供项目详情页
    的 Sessions tab 列出并跳转。孤立引用（已删会话）自动过滤。"""
    project_id = (cmd.get("project_id") or "").strip()
    sessions: list[dict] = []
    try:
        from openprogram.store import project_store as _projects
        from openprogram.agent.session_db import default_db
        proj = _projects.get_project(project_id)
        db = default_db()
        rows = {r.get("id"): r for r in db.list_sessions(limit=100_000)}
        for sid in (proj.session_ids if proj else []):
            row = rows.get(sid)
            if not row:
                continue  # 孤立引用，跳过
            title = (row.get("title") or "").strip()
            preview = (row.get("preview") or "").strip()
            if title in ("", "New conversation", "Untitled"):
                title = preview or sid
            sessions.append({
                "id": sid,
                "title": title,
                "created_at": row.get("created_at") or 0,
                "preview": preview or None,
            })
        sessions.sort(key=lambda s: s.get("created_at") or 0, reverse=True)
    except Exception:
        sessions = []
    await ws.send_text(json.dumps({
        "type": "project_sessions",
        "data": {"project_id": project_id, "sessions": sessions},
    }, default=str))


ACTIONS = {
    "list_projects": handle_list_projects,
    "list_project_sessions": handle_list_project_sessions,
    "get_project_config": handle_get_project_config,
    "set_project_config": handle_set_project_config,
    "create_project": handle_create_project,
    "remove_project": handle_remove_project,
    "set_session_project": handle_set_session_project,
    "list_session_workdirs": handle_list_session_workdirs,
    "add_session_workdir": handle_add_session_workdir,
    "remove_session_workdir": handle_remove_session_workdir,
}
