"""Runtime detection + first-run wizard prompt for the CLI chat."""
from __future__ import annotations
import logging

_log = logging.getLogger(__name__)


def _get_chat_runtime():
    """Return (provider_name, runtime) for the active chat agent.

    Resolution order (first match wins):

    1. The default agent's own model (``agent.model.provider`` /
       ``agent.model.id`` from ``agents/<id>/agent.json``).
    2. The model the user ran last.

    There is no auto-detected default: with neither, this returns
    ``(None, None)`` and the caller offers setup, which picks a model.

    Also applies the user's stored default thinking effort.
    """
    from openprogram.interfaces.server import runtime_management as rm
    rm._init_providers()

    # Try agent-pinned provider first.
    agent_provider = None
    agent_model = None
    try:
        from openprogram.runtime.agent_registry import manager as _A
        spec = _A.get_default()
        if spec is not None and spec.model:
            agent_provider = (spec.model.provider or "").strip() or None
            agent_model = (spec.model.id or "").strip() or None
    except Exception:
        spec = None

    if not (agent_provider and agent_model):
        agent_provider, agent_model = rm._last_used_chat_model()

    rt = None
    provider_name = None
    if agent_provider:
        try:
            rt = rm._create_runtime_for_visualizer(agent_provider, model=agent_model)
            provider_name = agent_provider
        except Exception:
            # The model's provider couldn't be built (auth missing,
            # daemon down, etc.). Never swap in another model.
            rt = None
            provider_name = None

    if rt is None:
        return None, None

    try:
        from openprogram.runtime.agent_registry import manager as _agents
        agent = _agents.get_default()
        effort = (agent.thinking_effort if agent else None) or "medium"
        rt.thinking_level = effort
    except Exception:
        # The runtime keeps its own default thinking level.
        _log.debug("default agent thinking effort unavailable", exc_info=True)
    return provider_name, rt


def _reset_provider_cache() -> None:
    """Force _init_providers to re-detect the default runtime.

    Used after an inline setup run so the newly-imported credentials
    get picked up without restarting the process.
    """
    from openprogram.interfaces.server import runtime_management as rm
    rm._providers_initialized = False
    rm._rest_probe_started = False
    rm._chat_runtime = None
    rm._chat_provider = None
    rm._chat_model = None
    rm._default_runtime = None
    rm._default_provider = None
    rm._available_providers.clear()


def _prompt_first_run_setup(console) -> bool:
    """No-provider first-run flow: offer the full setup inline.

    Returns True if a provider is now configured (wizard succeeded),
    False if the user declined / wizard failed.
    """
    import sys as _sys
    from openprogram.interfaces.cli.setup_sections.wizard import run_full_setup

    console.print()
    console.print(
        "[yellow]OpenProgram isn't configured yet.[/] "
        "The setup will connect a provider, pick your default "
        "model, and let you customize tools + agent defaults."
    )
    console.print()

    if not _sys.stdin.isatty():
        console.print(
            "[dim]Non-interactive stdin detected. Run "
            "`openprogram setup` manually, then re-run.[/]"
        )
        return False

    try:
        reply = input("Run setup now? [Y/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        reply = "n"
    if reply not in ("", "y", "yes"):
        console.print(
            "[dim]Skipped. Run `openprogram setup` when ready.[/]"
        )
        return False

    rc = run_full_setup()
    _reset_provider_cache()
    _, rt = _get_chat_runtime()
    if rt is None:
        console.print(
            f"[red]Setup finished (exit {rc}) but no provider was detected. "
            "Check `openprogram providers list` for status.[/]"
        )
        return False
    console.print()
    return True
