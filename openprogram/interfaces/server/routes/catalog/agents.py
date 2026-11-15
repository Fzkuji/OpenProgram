"""HTTP management API for persisted Agent profiles."""
from __future__ import annotations

import math
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
_THINKING_EFFORTS = {"", "minimal", "low", "medium", "high", "xhigh", "max"}
_DAILY_RESET = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_PATCH_FIELDS = {
    "always_on",
    "avatar",
    "name",
    "description",
    "memory",
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
    "hold_outbound",
    "delegation",
    "isolation",
    "permissions",
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
    if "memory" in raw:
        from openprogram.runtime.agent_registry.manager import validate_memory_config
        out["memory"] = validate_memory_config(raw["memory"], partial=True)
    if "always_on" in raw:
        from openprogram.runtime.agent_registry.manager import validate_always_on
        out["always_on"] = validate_always_on(raw["always_on"], partial=True)
    if "name" in raw:
        out["name"] = _short_text(raw["name"], "name", required=True, limit=80)
    if "description" in raw:
        out["description"] = _short_text(raw["description"], "description", limit=2000)
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
        if not isinstance(effort, str) or effort not in _THINKING_EFFORTS:
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
    if "hold_outbound" in raw:
        if not isinstance(raw["hold_outbound"], bool):
            raise ValueError("hold_outbound must be true or false")
        out["hold_outbound"] = raw["hold_outbound"]
    for key, allowed in (("delegation", ("", "on", "off")), ("isolation", ("", "worktree", "none"))):
        if key in raw:
            if raw[key] not in allowed:
                raise ValueError(f"{key} must be one of " + ", ".join(repr(v) for v in allowed))
            out[key] = raw[key]
    if "permissions" in raw:
        from openprogram.runtime.agent_registry.manager import validate_permissions
        out["permissions"] = validate_permissions(raw["permissions"])
    if "avatar" in raw:
        from openprogram.runtime.agent_registry.manager import validate_avatar
        out["avatar"] = validate_avatar(raw["avatar"])
    return out


def register(app: FastAPI) -> None:
    @app.get("/api/agents")
    def list_agents():
        from openprogram.runtime.agent_registry import manager as _agents

        rows = [agent.to_dict() for agent in _agents.list_all()]
        return JSONResponse(content={"agents": rows})

    @app.post("/api/agents")
    def create_agent(body: dict | None = None):
        from openprogram.runtime.agent_registry import manager as _agents

        raw = body or {}
        try:
            options = _validated_patch({key: raw[key] for key in ("model", "thinking_effort") if key in raw})
            model = options.get("model", {})
            creation = {"provider": model.get("provider", ""),
                        "model_id": model.get("id", ""), "thinking_effort": options.get("thinking_effort")}
            explicit_id = raw.get("id")
            if explicit_id is not None:
                name = _short_text(raw.get("name", ""), "name", limit=80)
                agent = _agents.create(
                    _short_text(explicit_id, "id", required=True, limit=40),
                    name=name,
                    **creation,
                )
            else:
                name = _short_text(raw.get("name"), "name", required=True, limit=80)
                agent = _agents.create_from_name(name, **creation)
        except (TypeError, ValueError) as exc:
            return JSONResponse(content={"error": str(exc)}, status_code=400)
        return JSONResponse(content={"agent": agent.to_dict()}, status_code=201)

    @app.get("/api/agents/{agent_id}")
    def get_agent(agent_id: str):
        from openprogram.runtime.agent_registry import manager as _agents

        agent = _agents.get(agent_id)
        if agent is None:
            return JSONResponse(content={"error": "agent not found"}, status_code=404)
        return JSONResponse(content={"agent": agent.to_dict()})

    @app.patch("/api/agents/{agent_id}")
    def update_agent(agent_id: str, body: dict | None = None):
        from openprogram.runtime.agent_registry import manager as _agents

        raw = dict(body or {})
        expected_revision = raw.pop("expected_revision", None)
        expected_updated_at = raw.pop("updated_at", None)
        try:
            if expected_revision is not None and (
                isinstance(expected_revision, bool)
                or not isinstance(expected_revision, int) or expected_revision < 1
            ):
                raise ValueError("expected_revision must be a positive integer")
            if expected_updated_at is not None and (
                isinstance(expected_updated_at, bool)
                or not isinstance(expected_updated_at, (int, float))
                or not math.isfinite(expected_updated_at)
            ):
                raise ValueError("updated_at must be a finite timestamp")
            patch = _validated_patch(raw)
            agent = _agents.update(
                agent_id, patch,
                expected_revision=expected_revision,
                expected_updated_at=expected_updated_at,
                require_precondition=True,
                replace_tool_policy=True,
            )
        except _agents.AgentNotFound:
            return JSONResponse(content={"error": "agent not found"}, status_code=404)
        except _agents.AgentRevisionConflict as exc:
            return JSONResponse(
                content={"error": str(exc), "agent": exc.current.to_dict()}, status_code=409,
            )
        except _agents.AgentPreconditionRequired as exc:
            return JSONResponse(content={"error": str(exc)}, status_code=428)
        except ValueError as exc:
            return JSONResponse(content={"error": str(exc)}, status_code=400)
        return JSONResponse(content={"agent": agent.to_dict()})

    @app.post("/api/agents/{agent_id}/default")
    def set_default(agent_id: str):
        from openprogram.runtime.agent_registry import manager as _agents

        if _agents.get(agent_id) is None:
            return JSONResponse(content={"error": "agent not found"}, status_code=404)
        agent = _agents.set_default(agent_id)
        return JSONResponse(content={"agent": agent.to_dict()})

    @app.post("/api/agents/{agent_id}/duplicate")
    def duplicate_agent(agent_id: str, body: dict | None = None):
        from openprogram.runtime.agent_registry import manager as _agents

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

    @app.get("/api/agents-md")
    def get_agents_md():
        """The AGENTS.md files the next turn reads at the working directory."""
        from openprogram.runtime.context import agents_md

        cwd = agents_md.working_dir().resolve()
        root = agents_md.project_root(cwd)
        glob = agents_md.global_path()
        return JSONResponse(content={
            "global": {"path": str(glob), "exists": glob.is_file()},
            "working_dir": str(cwd),
            "project_root": str(root) if root else None,
            "loaded": [str(p) for p in agents_md.chain(cwd)],
            "budget_bytes": agents_md.MAX_BYTES,
        })

    @app.get("/api/agents/{agent_id}/always-on")
    def get_always_on(agent_id: str):
        """The saved configuration and the channel conversations reports can go to.

        Wake state lives with each conversation (``/api/sessions/{id}/work``)."""
        from openprogram.runtime.conversation import always_on
        from openprogram.runtime.agent_registry import manager as _agents

        agent = _agents.get(agent_id)
        if agent is None:
            return JSONResponse(content={"error": "agent not found"}, status_code=404)
        return JSONResponse(content={
            "agent_id": agent_id,
            "config": dict(agent.always_on or {}),
            "channel_targets": always_on.channel_targets(agent_id),
        })

    @app.delete("/api/agents/{agent_id}")
    def delete_agent(agent_id: str):
        from openprogram.runtime.agent_registry import manager as _agents

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
