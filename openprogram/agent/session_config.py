"""Per-session run configuration shared by TUI, web, and channels."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


VALID_THINKING = {"off", "minimal", "low", "medium", "high", "xhigh"}
VALID_PERMISSION = {"ask", "auto", "bypass"}


@dataclass
class SessionRunConfig:
    tools_enabled: Optional[bool] = None
    tools_override: Optional[list[str]] = None
    thinking_effort: Optional[str] = None
    permission_mode: Optional[str] = None


def load_session_run_config(session_id: str) -> SessionRunConfig:
    try:
        from openprogram.agent.session_db import default_db
        row = default_db().get_session(session_id) or {}
    except Exception:
        row = {}

    tools_enabled = _as_bool_or_none(row.get("tools_enabled"))
    tools_override = _as_tool_list(row.get("tools_override"))
    thinking = _normalize_thinking(row.get("thinking_effort"))
    permission = _normalize_permission(row.get("permission_mode"))
    return SessionRunConfig(
        tools_enabled=tools_enabled,
        tools_override=tools_override,
        thinking_effort=thinking,
        permission_mode=permission,
    )


def save_session_run_config(
    session_id: str,
    *,
    agent_id: str,
    tools: Any = None,
    thinking_effort: Any = None,
    permission_mode: Any = None,
) -> SessionRunConfig:
    fields: dict[str, Any] = {}

    if tools is not None:
        enabled, override = _normalize_tools_value(tools)
        fields["tools_enabled"] = enabled
        fields["tools_override"] = override

    thinking = _normalize_thinking(thinking_effort)
    if thinking is not None:
        fields["thinking_effort"] = thinking

    permission = _normalize_permission(permission_mode)
    if permission is not None:
        fields["permission_mode"] = permission

    if fields:
        try:
            from openprogram.agent.session_db import default_db
            db = default_db()
            # Only persist config when the session row already exists.
            # Pre-creating an empty session just to hold tool / thinking
            # prefs leaves a "ghost" row in SessionDB if the user never
            # sends an actual message (refreshes / abandons the chat).
            # The caller (server's _append_msg) folds these fields into
            # create_session() when the first real message arrives, so
            # nothing is lost — the config still lands on disk, just
            # atomically with the first persisted message.
            if db.get_session(session_id) is not None:
                db.update_session(session_id, agent_id=agent_id, **fields)
        except Exception:
            pass

    return load_session_run_config(session_id)


def tools_override_from_config(cfg: SessionRunConfig) -> Optional[list[str]]:
    if cfg.tools_enabled is False:
        return []
    if cfg.tools_override:
        return list(cfg.tools_override)
    if cfg.tools_enabled is True:
        try:
            from openprogram.tools import DEFAULT_TOOLS
            return list(DEFAULT_TOOLS)
        except Exception:
            return []
    return None


def reasoning_from_config(cfg: SessionRunConfig) -> Optional[str]:
    effort = _normalize_thinking(cfg.thinking_effort)
    if not effort or effort == "off":
        return None
    return effort


def permission_from_config(
    cfg: SessionRunConfig,
    *,
    default: str,
) -> str:
    return _normalize_permission(cfg.permission_mode) or default


def _normalize_tools_value(value: Any) -> tuple[Optional[bool], Optional[list[str]]]:
    if isinstance(value, list):
        return True, [str(v) for v in value if str(v)]
    if isinstance(value, bool):
        return value, None
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True, None
        if lowered in {"0", "false", "no", "off"}:
            return False, None
    return None, None


def _as_tool_list(value: Any) -> Optional[list[str]]:
    if isinstance(value, list):
        out = [str(v) for v in value if str(v)]
        return out or None
    return None


def _as_bool_or_none(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return None


def _normalize_thinking(value: Any) -> Optional[str]:
    if value is None:
        return None
    effort = str(value).strip().lower()
    if effort == "none":
        effort = "off"
    if effort == "max":
        effort = "xhigh"
    return effort if effort in VALID_THINKING else None


def _normalize_permission(value: Any) -> Optional[str]:
    if value is None:
        return None
    mode = str(value).strip().lower()
    return mode if mode in VALID_PERMISSION else None
