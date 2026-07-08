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
    don't have to null-guard.

    On a fresh install ``agent.json`` for the default agent doesn't
    exist yet — neither ``openprogram setup`` (which the README
    suggests but is skippable) nor the implicit first-chat path
    creates it. The dispatcher then dispatches against an empty
    profile, ``resolve_model`` finds nothing, and the stub crashes the
    very first turn. To make first-launch chats Just Work we seed a
    minimal default agent here: pick the first enabled model from
    ``~/.openprogram/config.json`` (whatever the user (or
    ``providers adopt``) configured) and write it as the agent's
    default model. Subsequent loads return the persisted profile.
    """
    try:
        from openprogram.agents import manager as _A
        agent = _A.get(agent_id) if hasattr(_A, "get") else None
        if agent and hasattr(agent, "to_dict"):
            return agent.to_dict()
        if agent and hasattr(agent, "__dict__"):
            return dict(agent.__dict__)
        # No agent.json on disk. Seed one if this is the default agent
        # and we can pick a model from existing provider config. We
        # only auto-create for the canonical default agent id ("main")
        # — other agent_ids stay as empty profiles, since a user who
        # asked for an agent_id we've never heard of is probably mid-
        # typo and we don't want to silently create stub records.
        if agent_id == getattr(_A, "DEFAULT_AGENT_ID", "main"):
            seeded = _seed_default_agent(_A, agent_id)
            if seeded is not None:
                return seeded
    except Exception:
        pass
    return {"id": agent_id}


def _seed_default_agent(_A, agent_id: str) -> dict | None:
    """Best-effort: write a minimal agent.json so first-chat resolution
    finds a valid model. Returns the profile dict on success, ``None``
    on any failure (caller falls back to empty profile).

    Model selection priority:
      1. ``default_provider`` + ``default_model`` from top-level
         ``~/.openprogram/config.json``
      2. First entry of ``providers.<pid>.enabled_models`` for any
         provider with ``enabled: true``
      3. None — return None so callers continue with stub behavior

    Uses only the public-ish manager helpers so this stays decoupled
    from the on-disk schema; if ``AgentSpec`` / ``_write_agent`` aren't
    importable for any reason we bail out and let the legacy stub
    path run.
    """
    try:
        from openprogram.agents.manager import (
            AgentSpec, AgentModelRef, _write_agent,
        )
        from openprogram.webui._model_catalog import _read_providers_cfg
        from openprogram.paths import get_config_path
        import json as _json
    except Exception:
        return None

    provider_id: str | None = None
    model_id: str | None = None

    # 1. Top-level default_provider / default_model
    try:
        with open(get_config_path(), "r", encoding="utf-8") as f:
            root_cfg = _json.load(f)
        dp = root_cfg.get("default_provider")
        dm = root_cfg.get("default_model")
        if dp and dm:
            provider_id, model_id = dp, dm
    except Exception:
        root_cfg = {}

    # 2. Walk enabled providers for the first one with an enabled model.
    #    Enabled = the spec rows under ``providers.<p>.models`` (source of
    #    truth), falling back to the legacy ``enabled_models`` id list for a
    #    not-yet-migrated config.
    if not (provider_id and model_id):
        try:
            providers_cfg = _read_providers_cfg()
            for pid, pcfg in providers_cfg.items():
                if not pcfg.get("enabled"):
                    continue
                spec_ids = [r.get("id") for r in (pcfg.get("models") or []) if r.get("id")]
                enabled_models = spec_ids or list(pcfg.get("enabled_models") or [])
                if enabled_models:
                    provider_id, model_id = pid, enabled_models[0]
                    break
        except Exception:
            pass

    if not (provider_id and model_id):
        return None

    try:
        spec = AgentSpec(
            id=agent_id,
            name=agent_id,
            default=True,
            model=AgentModelRef(provider=provider_id, id=model_id),
        )
        _write_agent(spec)
        import sys
        sys.stderr.write(
            f"[load_agent_profile] seeded default agent {agent_id!r} "
            f"with model {provider_id}/{model_id}\n"
        )
        return spec.to_dict()
    except Exception:
        return None


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


def _resolve_custom_model(provider: str, model_id: str, get_model):
    """Resolve a community / fetched custom model from the provider's
    config ``custom_models`` and return its registered ``Model`` row, or
    None if it isn't a known custom model.

    Reuses the exact registry insert the picker-switch path uses
    (``_register_custom_model_in_registry`` — derived api + normalised
    base), so the chat resolver and the switch agree and a community model
    routes correctly without depending on a prior switch in this process.
    Lazy + guarded import: the webui layer isn't always present (pure
    agent/test contexts), in which case there's simply no custom model."""
    try:
        from openprogram.webui._runtime_management import (
            _register_custom_model_in_registry,
        )
    except Exception:
        return None
    try:
        if _register_custom_model_in_registry(provider, model_id):
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
            # Community / fetched custom model (no static models_generated
            # row, e.g. minimax-cn-coding-plan/MiniMax-M3): resolve it from
            # the provider's config custom_models — derived api + the
            # normalised (Anthropic /v1-stripped) base — so the chat path
            # routes it correctly even in a FRESH process. The
            # picker-switch registers the same row, but a subprocess turn
            # or a worker restart resuming a conv wouldn't have run that.
            m = _resolve_custom_model(provider, model_id_only, get_model)
            if m:
                return m
            # claude-code / claude-max are RUNTIME prefixes whose model
            # rows live under anthropic/ — the one legitimate
            # cross-provider alias. Anything else: the user picked
            # provider X and X doesn't have this model — fail honestly
            # below instead of routing through some other provider.
            if provider in ("claude-code", "claude-max"):
                m = get_model("anthropic", model_id_only)
                if m:
                    return m
            _raise_model_unavailable(requested)
        else:
            # Bare model id, no provider (legacy agent.json) — find the
            # provider that has this exact id. Same model, not a swap.
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
            _raise_model_unavailable(requested)

    # No model configured at all (fresh install whose agent.json is
    # missing/empty and no override). Fail with the same clear error —
    # the dispatcher surfaces it as a chat error bubble telling the
    # user to pick a model.
    _raise_model_unavailable(requested)


def _raise_model_unavailable(requested) -> None:
    from openprogram.providers.utils.errors import ErrorReason, LLMError
    if requested:
        msg = (
            f"Model {requested!r} is not available. It may have been "
            "removed from the provider's catalogue or the provider is "
            "disabled — re-fetch the provider's model list in Settings → "
            "Providers, or pick another model."
        )
    else:
        msg = (
            "No model is configured. Pick a model from the model "
            "selector (or enable one in Settings → Providers) and send "
            "the message again."
        )
    raise LLMError(message=msg, reason=ErrorReason.INVALID_REQUEST, retryable=False)


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
            # web_search overlay: the intent may ask for web_search ON TOP of
            # whatever the enabled/toolset resolves to (it isn't in
            # DEFAULT_TOOLS, so it must be added explicitly). Required for the
            # "store intent, not snapshot" design — see
            # docs/design/runtime/tool-toggle-management.md §5.1 改 C.
            want_web_search = bool(wanted.get("web_search"))

            def _overlay_web_search(tools):
                if not want_web_search:
                    return tools
                if any(t.name == "web_search" for t in tools):
                    return tools
                extra = agent_tools(names=["web_search"], source=source, only_available=True)
                return [*tools, *extra]

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
                return _apply_mcp_gate(_overlay_web_search(resolved))
            return _apply_mcp_gate(_overlay_web_search(
                agent_tools(names=names, source=source, only_available=True)))

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
