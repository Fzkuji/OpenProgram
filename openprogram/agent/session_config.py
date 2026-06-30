"""Per-session run configuration shared by TUI, web, and channels.

工具集设计原则（见 docs/design/runtime/tool-toggle-management.md）：
**会话只存"开关意图"，绝不存"展开后的工具名列表快照"。** 工具表每次运行时
由 registry 实时展开，所以新增/删除工具对所有历史会话自动生效。

历史上有一个 bug：webui 开 Web Search 或选非 full 工具 profile 时，会把当时
展开的工具名列表物化进 ``tools_override``（list[str]），把会话钉死在那批工具上
——之后新增的工具（如 list_sessions/message_branch）这些会话永远看不到。

现在的形态：
- ``tools_override`` 优先存 **dict 意图**（``{enabled, toolset, disabled, web_search}``），
  运行时经 ``_model_tools`` 的 dict 分支实时展开。
- ``tools_enabled``（bool）+ ``web_search``/``toolset`` 意图列，组合成 dict 意图。
- 仍接受 ``list[str]``（存量 / 第三方数据）——但读时若发现它等价于某个已知
  preset 的展开产物（DEFAULT_TOOLS / toolset，允许 ±web_search），就**当场归一**
  降级成意图，让老会话自愈、跟上工具集演进。区分不出的（用户真实精选）原样保留。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Union


VALID_THINKING = {"off", "minimal", "low", "medium", "high", "xhigh", "max"}
VALID_PERMISSION = {"ask", "auto", "bypass"}

# 工具意图的统一类型：dict（意图）/ list[str]（存量快照）/ None
ToolsOverride = Union[dict, list, None]


@dataclass
class SessionRunConfig:
    tools_enabled: Optional[bool] = None
    # dict（意图，推荐）或 list[str]（存量）。见模块 docstring。
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
      * ``list``  — a legacy snapshot we couldn't normalize (kept as-is)
      * ``None``  — no session-level override; fall back to the agent profile

    Never returns a freshly-materialized full tool-name list — that's the
    bug this design fixes.
    """
    if cfg.tools_enabled is False:
        return []

    # Legacy snapshot path: try to heal it back into an intent so the
    # session follows the live tool set again.
    if isinstance(cfg.tools_override, list) and cfg.tools_override:
        healed = _heal_snapshot(cfg.tools_override)
        if healed is not None:
            return _with_web_search(healed, cfg.web_search)
        # Couldn't recognize it as a known-preset expansion → it's most
        # likely a genuine user selection; keep the explicit list.
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


def _heal_snapshot(names: list[str]) -> Optional[dict]:
    """If ``names`` is set-equal to a known preset's expansion (DEFAULT_TOOLS
    or any toolset, allowing ±web_search), return the equivalent dict intent;
    else None. This is how a legacy materialized snapshot self-heals back
    into an intent that follows the live registry.

    Conservative by construction: anything that doesn't set-match a known
    preset is treated as a real user selection and NOT healed (returns None
    → caller keeps the explicit list)."""
    try:
        from openprogram.functions import DEFAULT_TOOLS, TOOLSETS, agent_tools
    except Exception:
        return None

    target = set(n for n in names if n != "web_search")
    default_set = set(DEFAULT_TOOLS)

    # DEFAULT_TOOLS full-snapshot — exact OR an older-version snapshot.
    # A snapshot frozen before tools were added is a STRICT SUBSET of the
    # current DEFAULT_TOOLS that still covers most of it (the only diff is
    # the newly-added tools the old snapshot couldn't know about). We must
    # NOT require exact equality, or every snapshot taken before any tool
    # was added stays frozen forever (that's the very bug). Distinguish
    # "old full snapshot" from "user picked a few tools" by coverage: a
    # genuine hand-pick is a small handful, a stale full snapshot covers
    # the large majority of today's defaults.
    if target == default_set:
        return {"enabled": True}
    if target and target <= default_set:
        coverage = len(target) / max(1, len(default_set))
        # ≥70% of current defaults AND within 5 of full → treat as a stale
        # full snapshot (the gap = recently-added tools). A real selection
        # of a few tools falls well below this.
        if coverage >= 0.70 and (len(default_set) - len(target)) <= 5:
            return {"enabled": True}

    # toolset equivalence → {toolset: name}
    for preset in TOOLSETS:
        try:
            expanded = {t.name for t in agent_tools(toolset=preset)}
        except Exception:
            continue
        if target == expanded:
            return {"enabled": True, "toolset": preset}

    return None


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
