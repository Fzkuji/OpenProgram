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

    return Model(
        id=requested or "stub",
        name=requested or "stub",
        api="completion",
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
    wanted = override if override is not None else profile.get("tools")
    if wanted is None:
        # Default-on: when the agent profile didn't pin a tool list,
        # expose DEFAULT_TOOLS (filtered by ``source`` so channel
        # transports still drop unsafe ones). Set ``tools: []`` in
        # agent.json to opt out explicitly.
        try:
            from openprogram.functions import agent_tools as _agent_tools
            return _agent_tools(source=source, only_available=True)
        except Exception:
            return None
    if wanted == []:
        return []
    try:
        from openprogram.functions import DEFAULT_TOOLS, agent_tools
        if isinstance(wanted, dict):
            enabled = wanted.get("enabled")
            if isinstance(enabled, list):
                names = [str(n) for n in enabled]
            else:
                disabled = {str(n) for n in (wanted.get("disabled") or [])}
                names = [n for n in DEFAULT_TOOLS if n not in disabled]
            toolset = wanted.get("toolset")
            if isinstance(toolset, str) and not isinstance(enabled, list):
                return agent_tools(toolset=toolset, source=source, only_available=True)
            return agent_tools(names=names, source=source, only_available=True)

        if isinstance(wanted, list) and wanted and isinstance(wanted[0], str):
            return agent_tools(
                names=[str(n) for n in wanted],
                source=source,
                only_available=True,
            )
        return [t for t in wanted if hasattr(t, "name")]
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
