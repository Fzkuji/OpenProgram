"""Agent-profile → Model + tools + history resolution.

Pure resolution helpers lifted out of ``dispatcher.py``. Two
responsibilities:

* Model resolution (``load_agent_profile``, ``resolve_model``,
  ``is_anthropic_family``) — turns an agent_id + per-turn override
  into a concrete ``Model`` instance, with robust fallback if the
  profile points at something the registry doesn't know about.

* Tool resolution (``resolve_tools``, ``log_resolved_tools``) — turns
  the agent profile's ``tools`` field + per-turn override into an
  ``AgentTool[]`` list. The working directory reaches the prompt through
  the ``environment`` context component.

* ``history_to_agent_messages`` — convert SessionDB rows to
  AgentMessage list for AgentContext replay.

None of this touches the DB or the event loop, so it's safe to
unit-test in isolation.
"""
from __future__ import annotations

import logging
import time
from typing import Optional, TYPE_CHECKING

_log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from openprogram.runtime.agent.turn_request import TurnRequest


def load_agent_profile(agent_id: str) -> dict:
    """Load agent.json. Returns at least {"id": agent_id} so callers
    don't have to null-guard.

    A missing agent.json is never seeded with a model: there is no
    default model. A turn runs on its conversation's model (or the last
    used one), and with none ``resolve_model`` raises a clear "pick a
    model" error.
    """
    try:
        from openprogram.runtime.agent_registry import manager as _A
        agent = _A.get(agent_id) if hasattr(_A, "get") else None
        if agent and hasattr(agent, "to_dict"):
            return agent.to_dict()
        if agent and hasattr(agent, "__dict__"):
            return dict(agent.__dict__)
    except Exception:
        # Registry is optional here; the bare profile falls back to defaults.
        _log.debug("agent profile %s unavailable", agent_id, exc_info=True)
    return {"id": agent_id}


def is_anthropic_family(model_id: Optional[str], provider_id: Optional[str]) -> bool:
    """True if this message should be counted by Anthropic's count_tokens.

    Covers direct anthropic provider, the claude-max / claude-code
    proxy paths, and any model id starting with ``claude-``.
    """
    if provider_id == "anthropic":
        return True
    if model_id and model_id.lower().startswith("claude"):
        return True
    return False


def _resolve_custom_model(provider: str, model_id: str, get_model):
    """Resolve a community / fetched custom model from the provider's
    config rows and return its registered ``Model`` row, or None if it
    isn't a known custom model.

    Reuses the exact registry insert the picker-switch path uses
    (``register_model_from_config`` — derived api + normalised base), so
    the chat resolver and the switch agree and a community model routes
    correctly without depending on a prior switch in this process."""
    try:
        from openprogram.models.providers.catalog.enabled_models import register_model_from_config
        if register_model_from_config(provider, model_id):
            return get_model(provider, model_id)
    except Exception:
        return None
    return None


def resolve_model(profile: dict, override: Optional[str] = None):
    """Resolve a Model instance from the agent profile or per-turn override.

    The user's pick is honoured literally: a ``provider/model`` request
    resolves ONLY within that provider (static registry, then the
    provider's config ``custom_models``). If it isn't there — e.g. a
    fetched model the upstream catalogue has since dropped — this
    raises ``LLMError(invalid)`` so the turn fails with a clear chat
    error instead of silently routing through some other provider or a
    stub default. The only cross-provider walk left is for a BARE model
    id with no provider (legacy agent.json form) and for the
    claude-code / claude-max runtime prefixes, whose model rows
    legitimately live under ``anthropic/``.
    """
    try:
        from openprogram.models.providers.catalog.models import get_model
    except Exception:
        get_model = None  # type: ignore[assignment]

    requested = override or profile.get("model")
    # agent.json stores ``model`` either as the legacy "<provider>/<id>"
    # string or as the newer {"provider": ..., "id": ...} dict
    # (cli/chat.py and setup.py both write the dict form). Normalize
    # to a single string shape here.
    provider_hint: Optional[str] = None
    if isinstance(requested, dict):
        provider_hint = requested.get("provider") or None
        model_id = requested.get("id") or requested.get("model") or None
        if provider_hint and model_id:
            requested = f"{provider_hint}/{model_id}"
        else:
            requested = model_id

    if get_model and requested:
        model_id_only: Optional[str] = None
        if "/" in requested:
            provider, model_id_only = requested.split("/", 1)
            m = get_model(provider, model_id_only)
            if m:
                return m
            # Community / fetched custom model (no static enabled_models
            # row, e.g. minimax-cn-coding-plan/MiniMax-M3): resolve it from
            # the provider's config custom_models — derived api + the
            # normalised (Anthropic /v1-stripped) base — so the chat path
            # routes it correctly even in a FRESH process. The
            # picker-switch registers the same row, but a subprocess turn
            # or a worker restart resuming a conv wouldn't have run that.
            m = _resolve_custom_model(provider, model_id_only, get_model)
            if m:
                return m
            _raise_model_unavailable(requested)
        else:
            # Bare model id, no provider (legacy agent.json) — find the
            # provider that has this exact id. Same model, not a swap.
            model_id_only = requested
            order = ["openai", "anthropic", "google", "amazon-bedrock",
                     "cerebras", "github-copilot",
                     "openai-codex", "gemini-subscription", "openrouter"]
            if provider_hint and provider_hint not in order:
                order.insert(0, provider_hint)
            for provider in order:
                m = get_model(provider, model_id_only)
                if m:
                    return m
            _raise_model_unavailable(requested)

    # No model configured at all (fresh install whose agent.json is
    # missing/empty and no override). Fail with the same clear error —
    # the dispatcher surfaces it as a chat error bubble telling the
    # user to pick a model.
    _raise_model_unavailable(requested)


NO_MODEL_CONFIGURED = (
    "No model is configured. Pick a model from the model "
    "selector (or enable one in Settings → Providers) and send "
    "the message again."
)


def _raise_model_unavailable(requested) -> None:
    from openprogram.models.providers.utils.failures.errors import ErrorReason, LLMError
    if requested:
        msg = (
            f"Model {requested!r} is not available. It may have been "
            "removed from the provider's catalogue or the provider is "
            "disabled — re-fetch the provider's model list in Settings → "
            "Providers, or pick another model."
        )
    else:
        msg = NO_MODEL_CONFIGURED
    raise LLMError(message=msg, reason=ErrorReason.INVALID_REQUEST, retryable=False)


def log_resolved_tools(req: "TurnRequest", tools: Optional[list]) -> None:
    try:
        names = sorted(
            getattr(t, "name", "")
            for t in (tools or [])
            if getattr(t, "name", "")
        )
        override_state = "explicit" if req.tools_override is not None else "profile"
        _log.debug(
            "resolved tools source=%r agent=%r mode=%s tools=%s",
            req.source, req.agent_id, override_state, names,
        )
    except Exception:
        # Diagnostic print only.
        _log.debug("resolved-tools log failed", exc_info=True)


class RequiredMcpUnavailable(ValueError):
    """A configured required dependency is unavailable for this invocation."""


def resolve_tools(
    profile: dict,
    override: object = None,
    *,
    source: Optional[str] = None,
) -> Optional[list]:
    """Resolve the AgentTool list for this turn.

    ``override`` (per-turn) > ``profile.tools`` (per-agent).
    ``source`` hides tools marked unsafe for channel transports.
    Returns None when no tools are configured (caller gives agent_loop
    a tools-free context — it's a pure chat then).
    """
    # MCP-level gating helper. MCP tools come out of agent_tools()
    # with the ``<server>__<tool>`` naming convention, so we filter
    # by the ``<server>`` prefix against the agent's ``mcp.disabled/
    # allowed`` patterns. Required servers fail before provider execution.
    def _apply_profile_gates(tool_list):
        from openprogram.extensions.gating import match_any, check_required
        agent_tools_config = (profile or {}).get("tools")
        if isinstance(agent_tools_config, dict) and (
            agent_tools_config.get("mode") == "none"
            or match_any("web_search", agent_tools_config.get("disabled") or [])
        ):
            tool_list = [t for t in tool_list or [] if t.name != "web_search"]
        # An explicit Search selection must be callable even without ToolSearch.
        # Copy only the selected tool; never change shared registry deferral.
        search_selected = (
            isinstance(wanted, dict) and wanted.get("web_search") is True
        ) or (isinstance(wanted, list) and "web_search" in wanted)
        if search_selected:
            from openprogram.programs._host.runtime import as_resident

            tool_list = [as_resident(tool) if tool.name == "web_search" else tool
                         for tool in tool_list or []]
        mcp_cfg = (profile or {}).get("mcp") or {}
        disabled = list(mcp_cfg.get("disabled") or [])
        allowed = list(mcp_cfg.get("allowed") or [])
        required = list(mcp_cfg.get("required") or [])
        if not (disabled or allowed or required):
            return tool_list
        def _server_of(name: str) -> str:
            return name.split("__", 1)[0] if "__" in name else ""
        out = []
        for t in tool_list or []:
            srv = _server_of(t.name)
            if not srv:
                out.append(t)
                continue
            if disabled and match_any(srv, disabled):
                continue
            if allowed and not match_any(srv, allowed):
                continue
            out.append(t)
        seen_servers = {_server_of(t.name) for t in out if _server_of(t.name)}
        missing = check_required(seen_servers, required)
        if missing:
            raise RequiredMcpUnavailable(
                "Required MCP servers unavailable: " + ", ".join(missing)
            )
        return out

    def _with_loader(tool_list, disabled=()):
        """``tool_list`` with ``tool_search`` in front when it holds a
        deferred tool: without the loader such a tool could never be
        called, and the catalog would name tools nothing can load."""
        from openprogram.extensions.gating import match_any
        tool_list = list(tool_list or [])
        if (not any(getattr(t, "_defer", False) for t in tool_list)
                or any(t.name == "tool_search" for t in tool_list)
                or match_any("tool_search", list(disabled))):
            return tool_list
        from openprogram.programs import agent_tools as _agent_tools
        return [*(_agent_tools(names=["tool_search"], source=source, only_available=True) or []),
                *tool_list]

    profile_tools = (profile or {}).get("tools")
    wanted = override if override is not None else profile_tools
    if isinstance(wanted, dict) and wanted.get("inherit"):
        inherited = (
            dict(profile_tools)
            if isinstance(profile_tools, dict)
            else {"mode": "automatic"}
        )
        if isinstance(inherited, dict):
            inherited.pop("inherit", None)
            if "web_search" in wanted:
                inherited["web_search"] = wanted["web_search"]
        wanted = inherited
    if wanted is None:
        try:
            from openprogram.programs import agent_tools as _agent_tools
            return _apply_profile_gates(_with_loader(_agent_tools(source=source, only_available=True)))
        except RequiredMcpUnavailable:
            raise
        except Exception:
            _apply_profile_gates([])
            return None
    if wanted == []:
        return _apply_profile_gates([])
    try:
        from openprogram.programs import DEFAULT_TOOLS, agent_tools
        from openprogram.extensions.gating import match_any
        if isinstance(wanted, dict):
            mode = wanted.get("mode")
            if mode is None and not any(
                wanted.get(key) for key in ("enabled", "toolset", "preset", "allowed")
            ):
                # AgentSpec before access modes stored only empty
                # disabled/allowed lists. Treat that shape as the new
                # automatic discovery default without rewriting every agent.
                mode = "automatic"
            if mode == "none":
                return _apply_profile_gates([])
            enabled = wanted.get("enabled")
            disabled_patterns = list(wanted.get("disabled") or [])
            allowed_patterns = list(wanted.get("allowed") or [])
            # web_search overlay: the intent may ask for web_search ON TOP of
            # whatever the enabled/toolset resolves to (it isn't in
            # DEFAULT_TOOLS, so it must be added explicitly). Required for the
            # "store intent, not snapshot" design — see
            # docs/reference/design/runtime/agent-configuration/tool-toggle-management.html §5.1 改 C.
            want_web_search = bool(wanted.get("web_search"))

            def _overlay_web_search(tools):
                if wanted.get("web_search") is False:
                    return [t for t in tools if t.name != "web_search"]
                if not want_web_search:
                    return tools
                if any(t.name == "web_search" for t in tools):
                    return tools
                if match_any("web_search", disabled_patterns):
                    return tools
                extra = agent_tools(names=["web_search"], source=source, only_available=True)
                return [*tools, *extra]

            preset = wanted.get("preset")
            if mode == "automatic":
                resolved = agent_tools(
                    toolset="full", source=source, only_available=True
                ) or []
                if disabled_patterns:
                    resolved = [
                        t for t in resolved if not match_any(t.name, disabled_patterns)
                    ]
                return _apply_profile_gates(_overlay_web_search(resolved))
            if mode == "selected" or isinstance(preset, str):
                names = _selected_tool_names(wanted)
                resolved = agent_tools(
                    names=names, source=source, only_available=True
                ) or []
                resolved = _with_loader(resolved)
                if disabled_patterns:
                    resolved = [
                        t for t in resolved if not match_any(t.name, disabled_patterns)
                    ]
                return _apply_profile_gates(_overlay_web_search(resolved))
            if isinstance(enabled, list):
                names = [str(n) for n in enabled]
            else:
                # Wildcard-aware filter over DEFAULT_TOOLS — matches the
                # same semantics the shared gating helper uses for skills.
                names = [
                    n for n in DEFAULT_TOOLS
                    if not match_any(n, disabled_patterns)
                    and (not allowed_patterns or match_any(n, allowed_patterns))
                ]
            toolset = wanted.get("toolset")
            if isinstance(toolset, str) and not isinstance(enabled, list):
                resolved = agent_tools(toolset=toolset, source=source, only_available=True) or []
                if disabled_patterns or allowed_patterns:
                    resolved = [
                        t for t in resolved
                        if not match_any(t.name, disabled_patterns)
                        and (not allowed_patterns or match_any(t.name, allowed_patterns))
                    ]
                return _apply_profile_gates(_overlay_web_search(resolved))
            return _apply_profile_gates(_overlay_web_search(_with_loader(
                agent_tools(names=names, source=source, only_available=True), disabled_patterns)))

        if isinstance(wanted, list) and wanted and isinstance(wanted[0], str):
            # A per-turn name list pins exactly these tools; without
            # tool_search a deferred one could never be loaded, so each
            # ships with its full schema.
            from openprogram.programs._host.runtime import as_resident
            named = agent_tools(names=[str(n) for n in wanted], source=source, only_available=True) or []
            if not any(t.name == "tool_search" for t in named):
                named = [as_resident(t) for t in named]
            return _apply_profile_gates(named)
        resolved = [t for t in wanted if hasattr(t, "name")]
        if source in {"self_update_verify", "self_update_diagnose", "self_update_repair"}:
            from openprogram.programs import apply_tool_policy
            resolved = apply_tool_policy(resolved, source=source, exposure_filter=False)
        return _apply_profile_gates(resolved)
    except RequiredMcpUnavailable:
        raise
    except Exception:
        _apply_profile_gates([])
        return None


def _selected_tool_names(policy: dict) -> list[str]:
    """The names a selected tool policy or its Access preset lists."""
    preset = policy.get("preset")
    if isinstance(preset, str):
        return _access_preset_names(preset)
    return [str(name) for name in policy.get("allowed") or []]


def agent_web_search_choice(policy: object) -> Optional[bool]:
    """Whether an Agent's tool policy settles web search, as ``resolve_tools`` reads it.

    The explicit switch wins; otherwise a selected list decides by whether it
    names the tool. ``None`` leaves it to the policy's remaining rules.
    """
    if not isinstance(policy, dict):
        return None
    if isinstance(policy.get("web_search"), bool):
        return policy["web_search"]
    mode = policy.get("mode")
    if mode == "selected" or (mode != "automatic" and isinstance(policy.get("preset"), str)):
        return "web_search" in _selected_tool_names(policy)
    return None


def _access_preset_names(name: str) -> list[str]:
    """Resolve a stored Access preset to live Program names."""
    from openprogram.programs import agent_tools
    if name == "FULL":
        return [
            tool.name for tool in agent_tools(
                toolset="full", include_disabled=True
            )
        ]
    if name == "BUILT-IN":
        return [
            tool.name for tool in agent_tools(
                toolset="full", include_disabled=True
            )
            if not getattr(tool, "_is_agent_method", False)
        ]
    from openprogram.programs._host.meta_storage import load_functions_meta
    data = load_functions_meta({"profiles": {}, "active": "FULL"})
    profiles = data.get("profiles") if isinstance(data, dict) else {}
    values = profiles.get(name) if isinstance(profiles, dict) else None
    return [str(value) for value in values] if isinstance(values, list) else []


def history_to_agent_messages(history: list[dict]) -> list:
    """Turn SessionDB rows into AgentMessage list (for AgentContext)."""
    from openprogram.models.providers.types import (
        AssistantMessage, TextContent, UserMessage,
    )
    out: list = []
    for m in history:
        role = m.get("role")
        content = m.get("content") or ""
        ts = int((m.get("timestamp") or time.time()) * 1000)
        if role == "user":
            if m.get("id") == "ROOT" or not content.strip():
                continue
            from openprogram.runtime.permissions.authority import render_model_input_from
            out.append(UserMessage(
                content=[TextContent(text=render_model_input_from(m, content))],
                timestamp=ts,
            ))
        elif role == "assistant":
            try:
                out.append(AssistantMessage(
                    content=[TextContent(text=content)],
                    api="completion",
                    provider="openai",
                    model="gpt-5",
                    timestamp=ts,
                ))
            except Exception:
                # One unconvertible history row must not fail the whole turn.
                _log.warning("assistant history message dropped from model input", exc_info=True)
    return out
