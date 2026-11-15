"""Per-session run configuration shared by TUI, web, and channels.

工具集设计原则（见 docs/reference/design/runtime/agent-configuration/tool-toggle-management.html）：
工具范围由会话的 Agent 配置决定，会话自己不开关工具、不选 Access preset。
会话只存两种叠加意图，绝不存"展开后的工具名列表快照"：
- ``web_search``（bool）：在 Agent 工具集之上加 / 去掉 web_search，
  由 ``tools_override_from_config`` 组合成 dict 意图，运行时经
  ``_model_tools`` 的 dict 分支实时展开。
- ``tools_override``（list[str]）：/skill 声明的 allowed-tools 白名单，
  原样透传。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Union


VALID_THINKING = {"off", "minimal", "low", "medium", "high", "xhigh", "max"}
# Permission modes and rule sets belong to the permission package
# (``openprogram.runtime.permissions.modes``); session settings store them.
from openprogram.runtime.permissions.modes import (
    PermissionRules,
    as_permission_rules as _as_permission_rules,
    normalize_permission as _normalize_permission,
    resolve_mode,
)

# 工具意图的统一类型：dict（意图）/ list[str]（用户显式精选）/ None
ToolsOverride = Union[dict, list, None]


@dataclass
class SessionRunConfig:
    # /skill allowed-tools 白名单。见模块 docstring。
    tools_override: Optional[list[str]] = None
    # 叠加意图：在 Agent 工具集之上加 / 去掉 web_search。
    web_search: Optional[bool] = None
    thinking_effort: Optional[str] = None
    permission_mode: Optional[str] = None
    # ── 权限规则（见 docs/reference/design/runtime/security/permission-decision.html）──
    permission_rules: Optional[PermissionRules] = None
    # 路径安全的额外工作目录集（§3.5）。
    additional_working_dirs: list[str] = field(default_factory=list)
    # Plus-menu Sandbox switch. None = inherit project/global sandbox.mode.
    sandbox_enabled: Optional[bool] = None


@dataclass
class BranchSettings:
    """One branch's effective settings: what ``resolve_branch_settings`` answers.

    ``config`` is the branch's own run configuration (``None`` where the
    branch leaves a setting unset); ``values`` holds each setting as it
    resolves through ``FALLBACK_ORDER`` and ``sources`` the layer it came
    from: ``branch`` (the branch's own record), ``agent`` (the branch's
    Agent as it is now), ``project`` or ``global`` (nothing set anywhere:
    the product default, ``None`` for most settings).
    """

    branch_id: Optional[str]
    config: SessionRunConfig
    values: dict[str, Any]
    sources: dict[str, str]
    #: What each setting would be if the branch did not set it: the value
    #: "Agent default" in a menu puts back, and the value a switch compares
    #: with before it writes (equal: nothing is stored).
    inherited: dict[str, Any] = field(default_factory=dict)


#: Where each setting falls back when the branch leaves it unset, in order.
#: Permission and Sandbox describe what may happen in a folder, so they
#: follow the project; the model, effort, tools and Fast describe how the
#: assistant works, so they follow the Agent (Claude Code's split: an
#: Agent's effort applies, its permission mode yields to the environment).
FALLBACK_ORDER: dict[str, tuple[str, ...]] = {
    "agent_id": ("global",),
    "model": ("agent", "global"),
    "thinking_effort": ("agent", "project", "global"),
    "web_search": ("agent", "global"),
    "tools_override": ("agent",),
    "service_tier": ("agent", "global"),
    "permission_mode": ("project", "global"),
    "permission_rules": ("global",),
    "sandbox_enabled": ("project", "global"),
}

#: Settings a project can supply (``FALLBACK_ORDER`` lists where).
PROJECT_FALLBACK_FIELDS = tuple(name for name, order in FALLBACK_ORDER.items() if "project" in order)


def _agent_settings(agent_id: Optional[str]) -> dict[str, Any]:
    """What the Agent supplies now, read live from the registry: a branch
    that sets nothing follows its Agent's later edits."""
    try:
        from openprogram.runtime.agent_registry import manager
        spec = manager.get(agent_id) if agent_id else manager.get_default()
    except Exception:
        spec = None
    if spec is None:
        return {}
    provider = (spec.model.provider or "").strip()
    model = (spec.model.id or "").strip()
    from openprogram.runtime.conversation.model_tools import agent_web_search_choice
    choice = agent_web_search_choice(spec.tools)
    return {
        "agent_id": spec.id,
        "model": f"{provider}/{model}" if provider and model else (model or None),
        "thinking_effort": _normalize_thinking(spec.thinking_effort),
        # An automatic tool policy offers web search.
        "web_search": True if choice is None else choice,
    }


def _global_settings(name: str) -> Any:
    """The product default of a setting nobody set."""
    if name == "agent_id":
        try:
            from openprogram.runtime.agent_registry import manager
            spec = manager.get_default()
            return spec.id if spec is not None else "main"
        except Exception:
            return "main"
    if name == "model":
        try:
            from openprogram.models.providers.catalog.last_used import read_last_used_model
            pair = read_last_used_model()
        except Exception:
            pair = None
        return f"{pair[0]}/{pair[1]}" if pair and pair[0] and pair[1] else None
    if name == "permission_mode":
        return resolve_mode(None, None)
    return None


def _project_value(session_id: str, name: str, defaults: dict) -> Any:
    if name == "sandbox_enabled":
        return sandbox_override_from_config(SessionRunConfig(), session_id)
    return _normalize_thinking(defaults.get(name)) if name == "thinking_effort" else defaults.get(name)


def resolve_branch_settings(
    session_id: str,
    branch_id: Optional[str] = None,
    *,
    row: Optional[dict] = None,
) -> BranchSettings:
    """The one answer to "what is this setting on this branch, and from where".

    ``branch_id`` ``None`` resolves the selected branch. Every branch,
    selected or not, reads its own record in ``branch_refs`` (the top-level
    fields are the selected branch's projection); a conversation with no
    branch recorded yet reads its own fields. A named branch with no record
    raises ``BranchConfigConflict('branch_gone')``.

    Unset settings fall back layer by layer through ``FALLBACK_ORDER``, the
    same for every entry (web, channels, scheduled runs, always-on, Goals,
    ``--print``). ``config`` keeps the branch's own values.
    """
    if row is None:
        try:
            from openprogram.runtime.context.session_db import default_db
            row = default_db().get_session(session_id) or {}
        except Exception:
            row = {}
    target = branch_id or row.get("active_branch_id")
    merged = row
    if row and target:
        from openprogram.state.store.session.branch_config import BranchConfigConflict, config_for_branch
        try:
            merged = {**row, **config_for_branch(row, target)}
        except BranchConfigConflict:
            if branch_id:
                raise
            merged = row
    config = _config_from_row(merged)
    provider, model = merged.get("provider_override"), merged.get("model_override")
    own: dict[str, Any] = {
        "agent_id": merged.get("agent_id"),
        "model": f"{provider}/{model}" if provider and model else (model or None),
        "thinking_effort": config.thinking_effort,
        "web_search": config.web_search,
        "tools_override": config.tools_override,
        "service_tier": merged.get("service_tier") or None,
        "permission_mode": config.permission_mode,
        "permission_rules": config.permission_rules,
        "sandbox_enabled": config.sandbox_enabled,
    }
    values: dict[str, Any] = {}
    sources: dict[str, str] = {}
    layers: dict[str, Any] = {}

    def layer(name: str) -> Any:
        if name not in layers:
            if name == "agent":
                layers[name] = _agent_settings(own["agent_id"])
            elif name == "project":
                layers[name] = project_defaults(session_id) if session_id else {}
        return layers.get(name)

    inherited: dict[str, Any] = {}
    for name, order in FALLBACK_ORDER.items():
        value, source = None, order[-1]
        for fallback in order:
            if fallback == "agent":
                value = layer("agent").get(name)
            elif fallback == "project":
                value = _project_value(session_id, name, layer("project")) if session_id else None
            else:
                value = _global_settings(name)
            source = fallback
            if value is not None:
                break
        inherited[name] = value
        if own[name] is not None:
            value, source = own[name], "branch"
        values[name], sources[name] = value, source
    # The stored pair, for callers that pass an explicit override on.
    values["provider_override"], values["model_override"] = provider, model
    sources["provider_override"] = sources["model_override"] = sources["model"]
    return BranchSettings(branch_id=target, config=config, values=values, sources=sources,
                          inherited=inherited)


def fallback_value(session_id: str, name: str, *, agent: Any = None) -> tuple[Any, str]:
    """The fallback of one setting a turn left unset, past the branch:
    ``(value, source)`` through ``FALLBACK_ORDER``. ``agent`` is the
    Agent profile's value when the caller already holds it (a trial's
    frozen profile), so the same order applies without a registry read."""
    for fallback in FALLBACK_ORDER[name]:
        if fallback == "agent":
            value = agent
        elif fallback == "project":
            value = _project_value(session_id, name, project_defaults(session_id)) if session_id else None
        else:
            value = _global_settings(name)
        if value is not None:
            return value, fallback
    return None, FALLBACK_ORDER[name][-1]


def load_session_run_config(session_id: str, branch_id: Optional[str] = None) -> SessionRunConfig:
    """The run configuration of a branch (the selected one by default),
    through ``resolve_branch_settings``."""
    return resolve_branch_settings(session_id, branch_id).config


def _config_from_row(row: dict) -> SessionRunConfig:
    return SessionRunConfig(
        tools_override=_as_tools_override(row.get("tools_override")),
        web_search=_as_bool_or_none(row.get("web_search")),
        thinking_effort=_normalize_thinking(row.get("thinking_effort")),
        permission_mode=_normalize_permission((row.get("permission_state") or {}).get("mode") or row.get("permission_mode")),
        permission_rules=_as_permission_rules(row.get("permission_rules")),
        additional_working_dirs=_as_str_list(row.get("additional_working_dirs")),
        sandbox_enabled=_as_bool_or_none(row.get("sandbox_enabled")),
    )


_BRANCH_UNSET = object()


VALID_SERVICE_TIER = {"", "priority", "flex", "auto", "default"}


def save_session_run_config(
    session_id: str,
    *,
    agent_id: str | None = None,
    tools: Any = None,
    web_search: Any = None,
    thinking_effort: Any = None,
    permission_mode: Any = None,
    permission_rules: Any = None,
    additional_working_dirs: Any = None,
    sandbox_enabled: Any = None,
    service_tier: Any = None,
    expected_branch_id: Any = _BRANCH_UNSET,
    branch_id: Any = _BRANCH_UNSET,
) -> SessionRunConfig:
    """Store the given settings; ``None`` leaves a setting as it is.

    Branch settings go to ``branch_id`` (a named branch, whatever is
    selected) or, without one, to the selected branch, guarded by
    ``expected_branch_id`` when given. ``agent_id`` is written only when
    given: the Agent changes through its own switch, never as a side effect.
    """
    fields: dict[str, Any] = {}

    override = _as_tools_override(tools)
    if override is not None:
        fields["tools_override"] = override

    ws = _as_bool_or_none(web_search)
    if ws is not None:
        fields["web_search"] = ws

    thinking = _normalize_thinking(thinking_effort)
    if thinking is not None:
        fields["thinking_effort"] = thinking

    permission = _normalize_permission(permission_mode)
    if permission is not None:
        fields["permission_mode"] = permission

    if permission_rules is not None:
        rules = _as_permission_rules(permission_rules)
        # 存成 dict（schemaless meta 里放纯数据结构）。空规则也存（用于清空）。
        fields["permission_rules"] = (
            {"allow": rules.allow, "deny": rules.deny, "ask": rules.ask}
            if rules is not None else None
        )


    if additional_working_dirs is not None:
        fields["additional_working_dirs"] = _as_str_list(additional_working_dirs)

    sandbox = _as_bool_or_none(sandbox_enabled)
    if sandbox is not None:
        fields["sandbox_enabled"] = sandbox

    if isinstance(service_tier, str) and service_tier in VALID_SERVICE_TIER:
        fields["service_tier"] = service_tier

    from openprogram.runtime.context.session_db import default_db
    db = default_db()
    row = db.get_session(session_id)
    if row is not None and additional_working_dirs is None and "additional_working_dirs" not in row:
        from openprogram.state.store.project import project_store as projects
        project = projects.project_for_session(session_id)
        if project and project.source_folders:
            fields["additional_working_dirs"] = list(project.source_folders)
    if agent_id:
        fields["agent_id"] = agent_id
    if fields and row is not None:
        from openprogram.state.store.session.branch_config import BRANCH_CONFIG_FIELDS, check_branch
        branch_fields = {k: v for k, v in fields.items() if k in BRANCH_CONFIG_FIELDS}
        session_fields = {k: v for k, v in fields.items() if k not in BRANCH_CONFIG_FIELDS}
        if branch_id is _BRANCH_UNSET:
            guard = {} if expected_branch_id is _BRANCH_UNSET else {"expected_branch_id": expected_branch_id}
            db.update_session(session_id, **fields, **guard)
        else:
            if expected_branch_id is not _BRANCH_UNSET:
                check_branch(row, expected_branch_id)
            if session_fields:
                db.update_session(session_id, **session_fields)
            if branch_fields:
                db.update_branch_config(session_id, branch_id, branch_fields)
        row = db.get_session(session_id)
        if branch_id is not _BRANCH_UNSET and branch_id and branch_id != (row or {}).get("active_branch_id"):
            from openprogram.state.store.session.branch_config import config_for_branch
            row = {**row, **config_for_branch(row, branch_id)}
    # An unsent draft has no session row. Return its normalized settings to
    # admission; the first message persists them without creating a ghost.
    return _config_from_row({**(row or {}), **fields})


def tools_override_from_config(cfg: SessionRunConfig) -> ToolsOverride:
    """Turn the stored INTENT into the override the dispatcher consumes.

    Output is one of:
      * ``list``  — a /skill allowed-tools selection (kept as-is)
      * ``dict``  — the web_search intent over the Agent's tool set,
                    expanded live by ``_model_tools``
      * ``None``  — no session-level override; the Agent profile decides
    """
    if cfg.tools_override:
        return _with_web_search(list(cfg.tools_override), cfg.web_search)
    if cfg.web_search is not None:
        return {"inherit": True, "web_search": cfg.web_search}
    return None


def reasoning_from_config(cfg: SessionRunConfig) -> Optional[str]:
    # ``off`` stays ``off``: the request layer decides whether the provider
    # has a wire value for it (OpenAI Responses sends ``effort: "none"``).
    return _normalize_thinking(cfg.thinking_effort) or None


def permission_from_config(cfg: SessionRunConfig, *, default: str | None = None) -> str:
    """The conversation's mode, else ``default`` (usually its project's), else ``DEFAULT_PERMISSION``."""
    return resolve_mode(cfg.permission_mode, default)


def resolved_permission_mode(cfg: SessionRunConfig, session_id: str) -> str:
    """A branch's permission mode, else its fallback through ``FALLBACK_ORDER``
    (the project's, then the product default)."""
    if cfg.permission_mode:
        return resolve_mode(cfg.permission_mode, None)
    return fallback_value(session_id, "permission_mode")[0]


def conversation_permission(session_id: str, *, branch_id: str | None = None) -> dict[str, Any]:
    """The ``permission_mode`` and ``permission_rules`` of a turn the runtime starts in this conversation."""
    from openprogram.runtime.permissions.rule import load_merged_rules
    if branch_id:
        from openprogram.runtime.permissions.state import permission_state
        return {
            "permission_mode": permission_state(session_id, branch_id=branch_id)["mode"],
            "permission_rules": load_merged_rules(session_id, branch_id=branch_id),
        }
    return {
        "permission_mode": permission_from_config(
            load_session_run_config(session_id), default=project_defaults(session_id).get("permission_mode")),
        "permission_rules": load_merged_rules(session_id),
    }


def project_defaults(session_id: str) -> dict:
    """会话所属项目的默认设置（permission_mode / toolset / thinking_effort）。
    新会话没自己设这些时的回落值。规则见 docs/reference/design/runtime/security/permission-decision.html；这里是
    档位默认（非规则）。项目没设 → {}。"""
    try:
        from openprogram.state.store.project import project_store as _projects
        proj = _projects.project_for_session(session_id) or _projects.get_default_project()
        s = _projects.load_project_settings(proj.id)
        return {
            k: s[k] for k in (
                "permission_mode", "thinking_effort", "sandbox_mode",
            )
            if s.get(k)
        }
    except Exception:
        return {}


def sandbox_override_from_config(
    cfg: SessionRunConfig, session_id: str = "",
) -> Optional[bool]:
    """Session switch, else project ``sandbox_mode``, else None (inherit)."""
    if cfg.sandbox_enabled is not None:
        return cfg.sandbox_enabled
    if not session_id:
        return None
    mode = project_defaults(session_id).get("sandbox_mode")
    if not mode:
        return None
    from openprogram.host.sandbox import MODE_WORKSPACE_WRITE
    return str(mode).strip().lower() == MODE_WORKSPACE_WRITE


# ── intent helpers ──

def _with_web_search(override: list[str], web_search: Optional[bool]) -> list[str]:
    """Overlay the web_search intent onto a /skill selection."""
    if web_search is None:
        return override
    if web_search is False:
        return [name for name in override if name != "web_search"]
    return override if "web_search" in override else [*override, "web_search"]


def _as_tools_override(value: Any) -> Optional[list[str]]:
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
    return effort if effort in VALID_THINKING else None


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value if str(v)]
    return []


def load_agent_session_binding(session_id: str) -> dict[str, Any]:
    """Read an accepted trial's immutable profile from existing session storage.

    Shared by every dispatcher entry, including retry/edit and restored turns.
    The read-only memory restriction belongs to the session, not to a renderer
    flag which could disappear after refresh or a later normal chat message.
    """
    from copy import deepcopy
    from openprogram.runtime.context.session_db import default_db

    row = default_db().get_session(session_id) or {}
    if not row.get("agent_trial"):
        return {}
    profile = row.get("agent_profile_snapshot")
    if not isinstance(profile, dict) or profile.get("id") != row.get("agent_id"):
        raise ValueError("The saved Agent trial configuration is unavailable")
    return {
        "profile_snapshot": deepcopy(profile),
        "memory_policy_override": {
            "mode": "off" if (profile.get("memory") or {}).get("mode") == "off" else "read_only",
        },
    }
