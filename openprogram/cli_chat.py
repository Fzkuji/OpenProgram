"""Terminal chat for ``openprogram`` / ``openprogram --cli``.

Hermes-style welcome banner (tools + skills inventory) followed by a
REPL. Slash commands (``/help``, ``/web``, ``/quit``, ...) are handled
locally; non-slash input goes through the same chat runtime the Web UI
uses, so behaviour stays aligned.

The bulk of this module's logic — banner inventory, slash-command
handlers, per-turn exec — lives in ``openprogram/_cli_chat/`` and is
re-exported here so existing call sites (``_timing.py``,
``openprogram.setup``, ``openprogram._cli_cmds.chat``, tests) keep
working unchanged.
"""
from __future__ import annotations

import os
import sys


# Re-exports — every external caller imports through ``openprogram.cli_chat``.
from openprogram._cli_chat.setup import (  # noqa: E402,F401
    _get_chat_runtime,
    _reset_provider_cache,
    _prompt_first_run_setup,
)
from openprogram._cli_chat.banner import (  # noqa: E402,F401
    _tool_inventory,
    _skill_inventory,
    _function_inventory,
    _application_inventory,
    _section_text,
    _print_banner,
)
from openprogram._cli_chat.handlers import (  # noqa: E402,F401
    SLASH_HELP,
    _parse_kv_args,
    _handle_slash,
    _handle_login,
    _handle_model,
    _handle_agent_switch,
    _handle_new_session,
    _handle_copy,
    _handle_attach,
    _handle_detach,
    _handle_connections,
)
from openprogram._cli_chat.turn import _run_turn_with_history  # noqa: E402,F401


def run_cli_chat(oneshot: str | None = None,
                 resume: str | None = None,
                 tui: bool = True) -> None:
    """Launch the terminal chat.

    ``oneshot`` runs one turn and exits (still persisted so it shows
    up in the sidebar of a later Web UI session).

    ``resume`` picks up a prior session id under the current default
    agent instead of starting a fresh one.

    ``tui`` defaults True: launches the full-screen Textual UI. Set
    False (or pass ``--no-tui``) to stay on the Rich REPL — useful
    for recording asciinema sessions or terminals without alt-screen
    support. ``oneshot`` always uses the Rich path.
    """
    import uuid as _uuid
    from rich.console import Console
    from openprogram.agents import manager as _A
    console = Console()

    # Provider detection probes 5+ providers (CLI binaries + API hosts)
    # on cold cache; that takes several seconds. Tell the user something
    # is happening so the TUI launch doesn't look frozen.
    with console.status("Detecting providers…", spinner="dots"):
        provider, rt = _get_chat_runtime()
    if rt is None:
        if not _prompt_first_run_setup(console):
            sys.exit(1)
        provider, rt = _get_chat_runtime()
        if rt is None:
            sys.exit(1)
    model = getattr(rt, "model", "?")

    agent = _A.get_default()
    if agent is None:
        agent = _A.create("main", make_default=True)

    if resume:
        session_id = resume
    else:
        session_id = "local_" + _uuid.uuid4().hex[:10]

    # Full-screen TUI path (default). One-shot stays on the Rich path
    # because rendering a scroll buffer for a single turn is overkill.
    if tui and not oneshot:
        try:
            from openprogram.cli_ink import run_ink_tui
            run_ink_tui(agent=agent, session_id=session_id, rt=rt)
            return
        except Exception as e:  # noqa: BLE001
            # cli.py:_maybe_redirect_for_tui() already dup2'd stdout/stderr
            # to ~/.openprogram/logs/ink-startup.log on the assumption the
            # Ink TUI would take over the terminal. The fallback REPL writes
            # to those same fds, so without restoring them the user sees a
            # frozen blank terminal while everything goes to the log file.
            from openprogram import cli as _cli
            for std_fd, saved_attr in ((1, "_TUI_TTY_OUT"), (2, "_TUI_TTY_ERR")):
                saved = getattr(_cli, saved_attr, None)
                if saved is not None:
                    try:
                        os.dup2(saved, std_fd)
                    except OSError:
                        pass
            console.print(
                f"[yellow]TUI failed to start ({type(e).__name__}: {e}); "
                f"falling back to REPL.[/]"
            )

    # Rich REPL fallback / oneshot path
    if resume:
        console.print(f"[dim]Resuming session {session_id} under "
                      f"agent {agent.id}[/]")
    else:
        console.print(f"[dim]New session {session_id} under "
                      f"agent {agent.id}[/]")

    if oneshot:
        reply = _run_turn_with_history(agent, session_id, oneshot)
        print(reply)
        return

    # Show the channels worker status without asking the user to start
    # anything: the primary thing this REPL does is chat. Channels are
    # an opt-in feature; we surface the status only if a worker is
    # already running so the user knows their bindings are live.
    try:
        from openprogram.worker import current_worker_pid
        pid = current_worker_pid()
        if pid:
            console.print(
                f"[dim]↪ channels worker running (PID {pid})  "
                f"— bindings active (attach/detach in the Web UI)[/]"
            )
    except Exception:
        pass

    _print_banner(console, provider, model)

    while True:
        try:
            user_input = console.input("\n[bold bright_blue]❯[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Goodbye.[/]")
            return
        if not user_input:
            continue
        if user_input.startswith("/"):
            if _handle_slash(user_input, console, rt,
                             agent=agent, session_id=session_id):
                return
            continue
        reply = _run_turn_with_history(agent, session_id, user_input)
        console.print()
        console.print(reply)
        # Fire-and-forget TTS; no-ops unless tts.provider is set.
        try:
            from openprogram.tts import speak
            speak(reply)
        except Exception:
            pass
