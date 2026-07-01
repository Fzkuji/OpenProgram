"""Per-session run configuration shared by TUI, web, and channels.

工具集设计原则（见 docs/design/runtime/tool-toggle-management.md）：
**会话只存"开关意图"，绝不存"展开后的工具名列表快照"。** 工具表每次运行时
由 registry 实时展开，所以新增/删除工具对所有历史会话自动生效。

存储形态：
- ``tools_enabled``（bool）+ ``web_search`` / ``toolset`` 意图列 →
  ``tools_override_from_config`` 组合成一个 **dict 意图**
  （``{enabled, toolset, disabled, web_search}``），运行时经 ``_model_tools``
  的 dict 分支实时展开。
- ``tools_override`` 也可直接存一个 dict 意图。
- ``list[str]`` 只用于用户显式精选的少数工具（如 web-search-only 的
  ``["web_search"]``），原样透传。绝不把"全部工具"物化成 list。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Union


VALID_THINKING = {"off", "minimal", "low", "medium", "high", "xhigh", "max"}
VALID_PERMISSION = {"ask", "auto", "bypass"}

# 工具意图的统一类型：dict（意图）/ list[str]（用户显式精选）/ None
ToolsOverride = Union[dict, list, None]


@dataclass
class SessionRunConfig:
    tools_enabled: Optional[bool] = None
    # dict（意图，推荐）或 list[str]（用户显式精选）。见模块 docstring。
    tools_override: ToolsOverride = None
    # 叠加意图：在主开关结果之上加一个 web_search 工具。
    web_search: Optional[bool] = None
    # 选中的 toolset preset 名（如 "research"），运行时展开。
    toolset: Optional[str] = None
    thinking_effort: Optional[str] = None
    permission_mode: Optional[str] = None


def load_session_run_config(session_id: str) -> SessionRunConfig:
    try:
        from openprogram.agent.session_db import default_db
        row = default_db().get_session(session_id) or {}
    except Exception:
        row = {}

    return SessionRunConfig(
        tools_enabled=_as_bool_or_none(row.get("tools_enabled")),
        tools_override=_as_tools_override(row.get("tools_override")),
        web_search=_as_bool_or_none(row.get("web_search")),
        toolset=_as_nonempty_str(row.get("toolset")),
        thinking_effort=_normalize_thinking(row.get("thinking_effort")),
        permission_mode=_normalize_permission(row.get("permission_mode")),
    )


def save_session_run_config(
    session_id: str,
    *,
    agent_id: str,
    tools: Any = None,
    web_search: Any = None,
    toolset: Any = None,
    thinking_effort: Any = None,
    permission_mode: Any = None,
) -> SessionRunConfig:
    fields: dict[str, Any] = {}

    if tools is not None:
        enabled, override = _normalize_tools_value(tools)
        fields["tools_enabled"] = enabled
        fields["tools_override"] = override

    ws = _as_bool_or_none(web_search)
    if ws is not None:
        fields["web_search"] = ws

    ts = _as_nonempty_str(toolset)
    if ts is not None:
        fields["toolset"] = ts

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


def tools_override_from_config(cfg: SessionRunConfig) -> ToolsOverride:
    """Turn the stored INTENT into the override the dispatcher consumes.

    Output is one of:
      * ``[]``    — all tools off
      * ``dict``  — an intent (enabled / toolset / disabled / web_search),
                    expanded live by ``_model_tools`` against the registry
      * ``list``  — an explicit user selection of tool names (kept as-is)
      * ``None``  — no session-level override; fall back to the agent profile

    Never returns a freshly-materialized full tool-name list — that's the
    bug this design fixes.
    """
    if cfg.tools_enabled is False:
        return []

    # Explicit name list = a genuine user selection (e.g. web-search-only
    # ``["web_search"]``). Passed through verbatim + web_search overlay.
    if isinstance(cfg.tools_override, list) and cfg.tools_override:
        return _with_web_search(list(cfg.tools_override), cfg.web_search)

    # Dict intent stored directly → pass through (+ web_search overlay).
    if isinstance(cfg.tools_override, dict):
        return _with_web_search(dict(cfg.tools_override), cfg.web_search)

    # Bool / toolset / web_search intent → build a dict intent, expanded live.
    if cfg.tools_enabled is True or cfg.toolset or cfg.web_search:
        intent: dict[str, Any] = {"enabled": True}
        if cfg.toolset:
            intent["toolset"] = cfg.toolset
        return _with_web_search(intent, cfg.web_search)

    return None


def reasoning_from_config(cfg: SessionRunConfig) -> Optional[str]:
    effort = _normalize_thinking(cfg.thinking_effort)
    if not effort or effort == "off":
        return None
    return effort


def permission_from_config(cfg: SessionRunConfig, *, default: str) -> str:
    return _normalize_permission(cfg.permission_mode) or default


# ── intent helpers ──

def _with_web_search(override: ToolsOverride, web_search: Optional[bool]) -> ToolsOverride:
    """Overlay the web_search intent onto an override. For a dict intent we
    set the ``web_search`` key (the expander adds the tool); for a list we
    append the name. ``[]`` (all off) is left untouched."""
    if not web_search:
        return override
    if isinstance(override, dict):
        out = dict(override)
        out["web_search"] = True
        return out
    if isinstance(override, list):
        return override if "web_search" in override else [*override, "web_search"]
    return override


def _normalize_tools_value(value: Any) -> tuple[Optional[bool], ToolsOverride]:
    """Normalize a caller-supplied tools value into (tools_enabled, override).

    Accepts:
      * dict  → stored verbatim as a dict INTENT (enabled=True implied)
      * list  → stored as an explicit name list (legacy / genuine selection)
      * bool  → on/off, no override list
      * str   → "true"/"false"/… → bool
    """
    if isinstance(value, dict):
        return True, dict(value)
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


def _as_tools_override(value: Any) -> ToolsOverride:
    if isinstance(value, dict):
        return dict(value)
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


def _as_nonempty_str(value: Any) -> Optional[str]:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _normalize_thinking(value: Any) -> Optional[str]:
    if value is None:
        return None
    effort = str(value).strip().lower()
    if effort == "none":
        effort = "off"
    return effort if effort in VALID_THINKING else None


def _normalize_permission(value: Any) -> Optional[str]:
    if value is None:
        return None
    mode = str(value).strip().lower()
    return mode if mode in VALID_PERMISSION else None
