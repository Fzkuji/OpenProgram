"""Runtime detection + first-run wizard prompt for the CLI chat."""
from __future__ import annotations


def _get_chat_runtime():
    """Return (provider_name, runtime) for the configured chat agent.

    Also applies the user's stored default thinking effort so
    ``rt.exec()`` picks it up without callers having to pass it.
    """
    from openprogram.webui import _runtime_management as rm
    rm._init_providers()
    rt = rm._chat_runtime
    if rt is None:
        return None, None
    try:
        from openprogram.setup import read_agent_prefs
        effort = read_agent_prefs().get("thinking_effort")
        if effort:
            rt.thinking_level = effort
    except Exception:
        pass
    return rm._chat_provider, rt


def _reset_provider_cache() -> None:
    """Force _init_providers to re-detect the default runtime.

    Used after an inline setup run so the newly-imported credentials
    get picked up without restarting the process.
    """
    from openprogram.webui import _runtime_management as rm
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
    from openprogram.setup import run_full_setup

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
