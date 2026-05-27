"""Agent-profile → Model + tools + history resolution.

Pure resolution helpers lifted out of ``dispatcher.py``. Two
responsibilities:

* Model resolution (``load_agent_profile``, ``resolve_model``,
  ``is_anthropic_family``) — turns an agent_id + per-turn override
  into a concrete ``Model`` instance, with robust fallback if the
  profile points at something the registry doesn't know about.

* Tool resolution (``resolve_tools``, ``with_tool_runtime_prompt``,
  ``log_resolved_tools``) — turns the agent profile's ``tools``
  field + per-turn override into an ``AgentTool[]`` list and decorates
  the system prompt with runtime CWD + tool inventory.

* ``history_to_agent_messages`` — convert SessionDB rows to
  AgentMessage list for AgentContext replay.

None of this touches the DB or the event loop, so it's safe to
unit-test in isolation.
"""
from __future__ import annotations

import time
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from openprogram.agent.dispatcher import TurnRequest


def load_agent_profile(agent_id: str) -> dict:
    """Load agent.json. Returns at least {"id": agent_id} so callers
    don't have to null-guard."""
    try:
        from openprogram.agents import manager as _A
        agent = _A.get(agent_id) if hasattr(_A, "get") else None
        if agent and hasattr(agent, "to_dict"):
            return agent.to_dict()
        if agent and hasattr(agent, "__dict__"):
            return dict(agent.__dict__)
    except Exception:
        pass
    return {"id": agent_id}


def is_anthropic_family(model_id: Optional[str], provider_id: Optional[str]) -> bool:
    """True if this message should be counted by Anthropic's count_tokens.

    Covers direct anthropic provider, the claude-max / claude-code
    proxy paths, and any model id starting with ``claude-``.
    """
    if provider_id in ("anthropic", "claude-code", "claude-max"):
        return True
    if model_id and model_id.lower().startswith("claude"):
        return True
    return False


def resolve_model(profile: dict, override: Optional[str] = None):
    """Resolve a Model instance from the agent profile or per-turn override.

    Falls back to a stub Model if the profile's identifier doesn't
    map to anything in the registry — keeps tests / orphaned agents
    from blowing up at construction time. The actual provider call
    will fail later if the stub doesn't have a real backend, but the
    failure surface is then ``[error] ProviderNotFound: ...`` which
    the dispatcher persists as a system message — recoverable.
    """
    from openprogram.providers.types import Model
    try:
        from openprogram.providers.models import get_model
    except Exception:
        get_model = None  # type: ignore[assignment]

    requested = override or profile.get("model")
    # agent.json stores ``model`` either as the legacy "<provider>/<id>"
    # string or as the newer {"provider": ..., "id": ...} dict
    # (cli_chat.py and setup.py both write the dict form). Normalize
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
            # Legacy provider-prefix form may not match: e.g.
            # claude-code/claude-sonnet-4-6 is a RUNTIME prefix whose
            # actual model row lives under anthropic/. Fall through.
            if provider_hint is None:
                provider_hint = provider
        else:
            model_id_only = requested

        order = ["openai", "anthropic", "google", "amazon-bedrock",
                 "cerebras", "claude-code", "github-copilot",
                 "openai-codex", "gemini-subscription", "openrouter"]
        if provider_hint and provider_hint not in order:
            order.insert(0, provider_hint)
        for provider in order:
            m = get_model(provider, model_id_only)
            if m:
                return m

    # Registry lookup failed for every (provider, model_id) combo we
    # tried. Fall through to a stub Model so callers don't have to
    # null-guard, but use a *real* api id (``openai-completions``) — the
    # legacy value here was ``"completion"`` which isn't registered in
    # ``api_registry``, so the moment the dispatcher tried to stream
    # against this stub it crashed with
    # ``No stream function registered for API: 'completion'``. Picking
    # an api that's actually registered means the failure becomes
    # "OpenAI rejected your model id" — recoverable by switching the
    # agent's model — rather than a hard ImportError-shaped crash on a
    # fresh install whose ``agent.json`` happens to be missing.
    import sys
    sys.stderr.write(
        f"[_model_tools.resolve_model] WARN: no registry entry for "
        f"{requested!r}; falling back to openai-completions stub. "
        f"Set the agent's model explicitly via Settings → Agents.\n"
    )
    return Model(
        id=requested or "stub",
        name=requested or "stub",
        api="openai-completions",
        provider="openai",
        base_url="https://api.openai.com/v1",
    )


def with_tool_runtime_prompt(system_prompt: str, tools: Optional[list]) -> str:
    if not tools:
        return system_prompt

    names = [getattr(t, "name", "") for t in tools]
    names = [n for n in names if n]
    if not names:
        return system_prompt

    from openprogram.paths import get_default_workdir
    cwd = get_default_workdir()
    has_bash = "bash" in names
    lines = [
        "Runtime tool context:",
        f"- Current working directory: {cwd}",
        f"- Available tools for this turn: {', '.join(names)}",
        "- Scope every filesystem operation (read/list/glob/grep/bash) "
        "to the smallest known target, ideally under the working "
        "directory above. Do NOT recurse over `$HOME` or `/`; recursive "
        "`**` walks over a home directory take minutes and exhaust the "
        "turn budget.",
        "- If the user asks for the current directory, answer from the Current working directory line above.",
        "- If the user asks to list the current directory, call the list tool with that absolute path.",
        "- When the user asks to inspect files, directories, or program state, call the relevant available tool instead of saying no tools are available.",
    ]
    if has_bash:
        lines.append("- Shell command execution is available through the bash tool.")
    else:
        lines.append("- Shell command execution is not available in this transport; use filesystem/search tools such as list, read, glob, and grep when possible.")

    tool_prompt = "\n".join(lines)
    return f"{system_prompt.rstrip()}\n\n{tool_prompt}".strip()


def log_resolved_tools(req: "TurnRequest", tools: Optional[list]) -> None:
    try:
        names = sorted(
            getattr(t, "name", "")
            for t in (tools or [])
            if getattr(t, "name", "")
        )
        override_state = "explicit" if req.tools_override is not None else "profile"
        print(
            f"[dispatcher tools] source={req.source!r} agent={req.agent_id!r} "
            f"mode={override_state} tools={names}",
            flush=True,
        )
    except Exception:
        pass


def resolve_tools(
    profile: dict,
    override: Optional[list[str]] = None,
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
    # allowed`` patterns. Required-server check returns None to abort
    # the turn cleanly.
    def _apply_mcp_gate(tool_list):
        from openprogram.agents.gating import match_any, check_required
        mcp_cfg = (profile or {}).get("mcp") or {}
        disabled = list(mcp_cfg.get("disabled") or [])
        allowed = list(mcp_cfg.get("allowed") or [])
        required = list(mcp_cfg.get("required") or [])
        if not (disabled or allowed or required):
            return tool_list
        def _server_of(name: str) -> str:
            return name.split("__", 1)[0] if "__" in name else ""
        seen_servers = {_server_of(t.name) for t in (tool_list or []) if _server_of(t.name)}
        missing = check_required(seen_servers, required)
        if missing:
            # Hard fail — return None so caller treats this turn as
            # tools-disabled with a clear log line.
            from openprogram.webui import server as _srv
            try:
                _srv._log(f"[mcp-gate] required servers missing: {missing}")
            except Exception:
                pass
            return None
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
        return out

    wanted = override if override is not None else profile.get("tools")
    if wanted is None:
        try:
            from openprogram.functions import agent_tools as _agent_tools
            return _apply_mcp_gate(_agent_tools(source=source, only_available=True))
        except Exception:
            return None
    if wanted == []:
        return []
    try:
        from openprogram.functions import DEFAULT_TOOLS, agent_tools
        from openprogram.agents.gating import match_any
        if isinstance(wanted, dict):
            enabled = wanted.get("enabled")
            disabled_patterns = list(wanted.get("disabled") or [])
            allowed_patterns = list(wanted.get("allowed") or [])
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
                return _apply_mcp_gate(resolved)
            return _apply_mcp_gate(agent_tools(names=names, source=source, only_available=True))

        if isinstance(wanted, list) and wanted and isinstance(wanted[0], str):
            return _apply_mcp_gate(agent_tools(
                names=[str(n) for n in wanted],
                source=source,
                only_available=True,
            ))
        return _apply_mcp_gate([t for t in wanted if hasattr(t, "name")])
    except Exception:
        return None


def history_to_agent_messages(history: list[dict]) -> list:
    """Turn SessionDB rows into AgentMessage list (for AgentContext)."""
    from openprogram.providers.types import (
        AssistantMessage, TextContent, UserMessage,
    )
    out: list = []
    for m in history:
        role = m.get("role")
        content = m.get("content") or ""
        ts = int((m.get("timestamp") or time.time()) * 1000)
        if role == "user":
            out.append(UserMessage(
                content=[TextContent(text=content)],
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
                pass
    return out
