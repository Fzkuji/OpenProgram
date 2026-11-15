"""Project permission rules: list, add, remove."""
from __future__ import annotations

import json


# 权限规则跟着**项目**走（见 docs/reference/design/runtime/security/permission-decision.html）。请求可直接带
# project_id（Projects 页知道项目）；只带 session_id 时反查项目（composer 路径）。

def _resolve_project_id(cmd: dict) -> str | None:
    pid = (cmd.get("project_id") or "").strip()
    if pid:
        return pid
    sid = (cmd.get("session_id") or "").strip()
    if not sid:
        return None
    try:
        from openprogram.state.store.project import project_store as _projects
        proj = _projects.project_for_session(sid) or _projects.get_default_project()
        return proj.id if proj else None
    except Exception:
        return None


def _project_rules(project_id: str) -> dict:
    from openprogram.state.store.project import project_store as _projects
    settings = _projects.load_project_settings(project_id)
    r = settings.get("permission_rules") or {}
    return {"allow": list(r.get("allow") or []),
            "deny": list(r.get("deny") or []),
            "ask": list(r.get("ask") or [])}


def _broadcast_permission_rules(project_id: str) -> None:
    """把某项目当前的权限规则广播给前端（规则面板刷新）。"""
    from openprogram.interfaces.server import server as _s
    r = _project_rules(project_id)
    _s._broadcast(json.dumps({"type": "permission_rules", "data": {
        "project_id": project_id, **r,
    }}))


async def _reply_permission_rules(ws, cmd: dict, *, add: bool | None = None):
    from openprogram.state.store.project import project_store as _projects
    from openprogram.runtime.permissions.rule import parse_rule
    import re

    pid = _resolve_project_id(cmd)
    response = {"project_id": pid, "request_id": cmd.get("request_id"),
                "action": cmd.get("action"), "status": "ok"}
    try:
        if not pid or _projects.get_project(pid) is None:
            raise ValueError("Project not found")
        if add is not None:
            behavior, raw = cmd.get("behavior"), cmd.get("rule")
            if behavior not in ("allow", "deny", "ask") or not isinstance(raw, str):
                raise ValueError("Invalid permission rule")
            rule = raw.strip()
            if not rule or len(rule) > 8192:
                raise ValueError("Invalid permission rule")
            if add and not re.fullmatch(r"[\w.*:-]+", parse_rule(rule).tool_name):
                raise ValueError("Use ToolName or ToolName(pattern)")
            settings = _projects.load_project_settings(pid)
            rules = _project_rules(pid)
            entries = rules[behavior]
            if add and rule not in entries:
                entries.append(rule)
            elif not add and rule in entries:
                entries.remove(rule)
            settings["permission_rules"] = rules
            _projects.save_project_settings(pid, settings)
            if _project_rules(pid) != rules:
                raise OSError("Permission rules could not be saved")
            _broadcast_permission_rules(pid)
        response.update(_project_rules(pid))
    except (ValueError, OSError, TypeError) as exc:
        response.update(status="error", error=str(exc))
    await ws.send_text(json.dumps({"type": "permission_rules", "data": response}))


async def handle_list_permission_rules(ws, cmd: dict):
    await _reply_permission_rules(ws, cmd)


async def handle_add_permission_rule(ws, cmd: dict):
    await _reply_permission_rules(ws, cmd, add=True)


async def handle_remove_permission_rule(ws, cmd: dict):
    await _reply_permission_rules(ws, cmd, add=False)
