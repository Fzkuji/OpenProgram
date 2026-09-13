"""HTTP management API for persisted Agent profiles."""
from __future__ import annotations

import re
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response


_TOOL_MODES = {"automatic", "selected", "none"}
_SESSION_SCOPES = {
    "main",
    "per-peer",
    "per-channel-peer",
    "per-account-channel-peer",
}
_THINKING_EFFORTS = {"minimal", "low", "medium", "high", "xhigh"}
_DAILY_RESET = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_PATCH_FIELDS = {
    "name",
    "model",
    "thinking_effort",
    "system_prompt",
    "skills",
    "tools",
    "mcp",
    "identity",
    "session_scope",
    "session_idle_minutes",
    "session_daily_reset",
}


def _name_list(raw: object, field: str) -> list[str]:
    if not isinstance(raw, list) or not all(
        isinstance(value, str) and value.strip() for value in raw
    ):
        raise ValueError(f"{field} must be a list of names")
    return list(dict.fromkeys(value.strip() for value in raw))


def _validated_tools(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("tools must be an object")
    mode = raw.get("mode")
    if mode not in _TOOL_MODES:
        raise ValueError("tools.mode must be automatic, selected, or none")
    out: dict[str, Any] = {"mode": mode}
    disabled = raw.get("disabled")
    if disabled is not None:
        out["disabled"] = _name_list(disabled, "tools.disabled")
    if raw.get("web_search") is not None:
        out["web_search"] = bool(raw["web_search"])
    if mode == "selected":
        preset = raw.get("preset")
        allowed = raw.get("allowed")
        if preset is not None:
            if not isinstance(preset, str) or not preset.strip():
                raise ValueError("tools.preset must be a non-empty string")
            out["preset"] = preset.strip()
        elif allowed is not None:
            out["allowed"] = _name_list(allowed, "tools.allowed")
        else:
            out["allowed"] = []
    return out


def _validated_gate(raw: object, field: str, *, required: bool = False) -> dict[str, list[str]]:
    if not isinstance(raw, dict):
        raise ValueError(f"{field} must be an object")
    keys = ["allowed", "disabled"]
    if field == "skills":
        keys.append("categories")
    if required:
        keys.append("required")
    out = {
        key: _name_list(raw.get(key, []), f"{field}.{key}")
        for key in keys
    }
    if required:
        conflicts = set(out["disabled"]) & set(out["required"])
        if conflicts:
            raise ValueError(
                "mcp.required cannot also be disabled: " + ", ".join(sorted(conflicts))
            )
    return out


def _short_text(raw: object, field: str, *, required: bool = False, limit: int = 200) -> str:
    if not isinstance(raw, str):
        raise ValueError(f"{field} must be a string")
    value = raw.strip() if required else raw
    if required and not value:
        raise ValueError(f"{field} must not be empty")
    if len(value) > limit:
        raise ValueError(f"{field} is too long")
    return value


def _validated_patch(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("request body must be an object")
    unknown = set(raw) - _PATCH_FIELDS
    if unknown:
        raise ValueError("unsupported fields: " + ", ".join(sorted(unknown)))
    out: dict[str, Any] = {}
    if "name" in raw:
        out["name"] = _short_text(raw["name"], "name", required=True, limit=80)
    if "model" in raw:
        model = raw["model"]
        if not isinstance(model, dict):
            raise ValueError("model must be an object")
        out["model"] = {
            "provider": _short_text(model.get("provider", ""), "model.provider", limit=120),
            "id": _short_text(model.get("id", ""), "model.id", limit=200),
        }
    if "thinking_effort" in raw:
        effort = raw["thinking_effort"]
        if effort not in _THINKING_EFFORTS:
            raise ValueError("thinking_effort is invalid")
        out["thinking_effort"] = effort
    if "system_prompt" in raw:
        out["system_prompt"] = _short_text(
            raw["system_prompt"], "system_prompt", limit=100_000
        )
    if "identity" in raw:
        identity = raw["identity"]
        if not isinstance(identity, dict):
            raise ValueError("identity must be an object")
        out["identity"] = {
            "name": _short_text(identity.get("name", ""), "identity.name", limit=80),
            "mention_patterns": _name_list(
                identity.get("mention_patterns", []), "identity.mention_patterns"
            ),
        }
    if "skills" in raw:
        out["skills"] = _validated_gate(raw["skills"], "skills")
    if "tools" in raw:
        out["tools"] = _validated_tools(raw["tools"])
    if "mcp" in raw:
        out["mcp"] = _validated_gate(raw["mcp"], "mcp", required=True)
    if "session_scope" in raw:
        scope = raw["session_scope"]
        if scope not in _SESSION_SCOPES:
            raise ValueError("session_scope is invalid")
        out["session_scope"] = scope
    if "session_idle_minutes" in raw:
        idle = raw["session_idle_minutes"]
        if isinstance(idle, bool) or not isinstance(idle, int) or not 0 <= idle <= 525_600:
            raise ValueError("session_idle_minutes must be between 0 and 525600")
        out["session_idle_minutes"] = idle
    if "session_daily_reset" in raw:
        reset = raw["session_daily_reset"]
        if not isinstance(reset, str) or (reset and not _DAILY_RESET.fullmatch(reset)):
            raise ValueError("session_daily_reset must be empty or HH:MM")
        out["session_daily_reset"] = reset
    return out


def register(app: FastAPI) -> None:
    @app.get("/api/agents")
    def list_agents():
        from openprogram.agent.management import manager as _agents

        try:
            rows = [agent.to_dict() for agent in _agents.list_all()]
        except Exception:
            rows = []
        return JSONResponse(content={"agents": rows})

    @app.post("/api/agents")
    def create_agent(body: dict | None = None):
        from openprogram.agent.management import manager as _agents

        raw = body or {}
        try:
            explicit_id = raw.get("id")
            if explicit_id is not None:
                name = _short_text(raw.get("name", ""), "name", limit=80)
                agent = _agents.create(
                    _short_text(explicit_id, "id", required=True, limit=40),
                    name=name,
                )
            else:
                name = _short_text(raw.get("name"), "name", required=True, limit=80)
                agent = _agents.create_from_name(name)
        except (TypeError, ValueError) as exc:
            return JSONResponse(content={"error": str(exc)}, status_code=400)
        return JSONResponse(content={"agent": agent.to_dict()}, status_code=201)

    @app.get("/api/agents/{agent_id}")
    def get_agent(agent_id: str):
        from openprogram.agent.management import manager as _agents

        agent = _agents.get(agent_id)
        if agent is None:
            return JSONResponse(content={"error": "agent not found"}, status_code=404)
        return JSONResponse(content={"agent": agent.to_dict()})

    @app.patch("/api/agents/{agent_id}")
    def update_agent(agent_id: str, body: dict | None = None):
        from openprogram.agent.management import manager as _agents

        current = _agents.get(agent_id)
        if current is None:
            return JSONResponse(content={"error": "agent not found"}, status_code=404)
        raw = dict(body or {})
        expected = raw.pop("updated_at", None)
        if expected is not None and expected != current.updated_at:
            return JSONResponse(
                content={"error": "agent configuration changed; reload before saving"},
                status_code=409,
            )
        try:
            patch = _validated_patch(raw)
        except ValueError as exc:
            return JSONResponse(content={"error": str(exc)}, status_code=400)
        tools = patch.pop("tools", None)
        agent = _agents.update(agent_id, patch) if patch else current
        if tools is not None:
            agent = _agents.replace_tools(agent_id, tools)
        return JSONResponse(content={"agent": agent.to_dict()})

    @app.post("/api/agents/{agent_id}/default")
    def set_default(agent_id: str):
        from openprogram.agent.management import manager as _agents

        if _agents.get(agent_id) is None:
            return JSONResponse(content={"error": "agent not found"}, status_code=404)
        agent = _agents.set_default(agent_id)
        return JSONResponse(content={"agent": agent.to_dict()})

    @app.post("/api/agents/{agent_id}/duplicate")
    def duplicate_agent(agent_id: str, body: dict | None = None):
        from openprogram.agent.management import manager as _agents

        if _agents.get(agent_id) is None:
            return JSONResponse(content={"error": "agent not found"}, status_code=404)
        raw = body or {}
        try:
            explicit_id = raw.get("id")
            if explicit_id is not None:
                target_name = _short_text(raw.get("name", ""), "name", limit=80)
                target_id = _short_text(explicit_id, "id", required=True, limit=40)
            else:
                target_name = _short_text(raw.get("name"), "name", required=True, limit=80)
                target_id = ""
            agent = _agents.duplicate(
                agent_id, target_id=target_id, name=target_name,
            )
        except (TypeError, ValueError) as exc:
            return JSONResponse(content={"error": str(exc)}, status_code=400)
        return JSONResponse(content={"agent": agent.to_dict()}, status_code=201)

    @app.get("/api/agents/{agent_id}/workspace")
    def get_workspace(agent_id: str):
        from openprogram.agent.management import manager as _agents
        from openprogram.agent.management import workspace as _workspace

        if _agents.get(agent_id) is None:
            return JSONResponse(content={"error": "agent not found"}, status_code=404)
        root = _workspace.bootstrap(agent_id)
        files = []
        for name in (
            _workspace.AGENTS_FILE,
            _workspace.SOUL_FILE,
            _workspace.USER_FILE,
            _workspace.TOOLS_FILE,
        ):
            path = root / name
            files.append({"name": name, "path": str(path), "exists": path.is_file()})
        return JSONResponse(content={"path": str(root), "files": files})

    @app.delete("/api/agents/{agent_id}")
    def delete_agent(agent_id: str):
        from openprogram.agent.management import manager as _agents

        agent = _agents.get(agent_id)
        if agent is None:
            return JSONResponse(content={"error": "agent not found"}, status_code=404)
        if agent.default:
            return JSONResponse(
                content={"error": "the default agent cannot be deleted"},
                status_code=409,
            )
        _agents.delete(agent_id)
        return Response(status_code=204)
