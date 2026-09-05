"""Terminal chat for bare ``openprogram``.

Welcome banner (tools + skills inventory) followed by a chat loop.
Slash commands (``/help``, ``/web``, ``/quit``, ...) are handled
locally; non-slash input goes through the same chat runtime the Web UI
uses, so behaviour stays aligned.

The bulk of this module's logic — banner inventory, slash-command
handlers, per-turn exec — lives in this application's ``repl`` package and is
re-exported here so existing call sites (``scripts/profile_startup.py``,
``openprogram.setup``, ``openprogram.cli.commands.chat``, tests) keep
working unchanged.
"""
from __future__ import annotations

import os
import sys


# Re-exports — every external caller imports through ``openprogram.cli.chat``.
from openprogram.cli.repl.setup import (  # noqa: E402,F401
    _get_chat_runtime,
    _reset_provider_cache,
    _prompt_first_run_setup,
)
from openprogram.cli.repl.banner import (  # noqa: E402,F401
    _tool_inventory,
    _skill_inventory,
    _function_inventory,
    _application_inventory,
    _section_text,
    _print_banner,
)
from openprogram.cli.repl.handlers import (  # noqa: E402,F401
    register_repl_builtins,
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
from openprogram.cli.repl.turn import _run_turn_with_history  # noqa: E402,F401


def run_cli_chat(oneshot: str | None = None,
                 resume: str | None = None,
                 tui: bool = True,
                 response_format=None,
                 no_alt_screen: bool = False,
                 screen_reader: bool = False) -> None:
    """Launch the terminal chat.

    ``oneshot`` runs one turn and exits (still persisted so it shows
    up in the sidebar of a later Web UI session).

    ``resume`` picks up a prior session id under the current default
    agent instead of starting a fresh one.

    ``tui`` defaults True on every platform. Ink performs a real raw-input
    capability check at startup; unsupported terminals fall back to Rich.
    ``oneshot`` always uses the Rich path (one-shot doesn't render
    a TUI).
    """
    import uuid as _uuid
    from rich.console import Console
    console = Console()

    if resume:
        session_id = resume
    else:
        session_id = "local_" + _uuid.uuid4().hex[:10]

    # The Ink client talks to the worker over WebSocket and discovers the
    # default agent/model there.  Initialising a second provider runtime in
    # this short-lived launcher makes a Windows cold start do the expensive
    # work twice and delays worker startup long enough to look hung.
    # Initialise the in-process runtime only for one-shot/Rich execution, or
    # after Ink has genuinely failed and needs the fallback.
    if tui and not oneshot:
        try:
            from openprogram.cli.ink import run_ink_tui
            run_ink_tui(
                session_id=session_id,
                no_alt_screen=no_alt_screen,
                screen_reader=screen_reader,
            )
            return
        except Exception as e:  # noqa: BLE001
            # cli.py:_maybe_redirect_for_tui() may have dup2'd stdout/stderr
            # to the profile's logs/ink-startup.log on POSIX. The fallback
            # REPL must restore them before it prints or prompts.
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

    # Provider detection probes 5+ providers (CLI binaries + API hosts)
    # on cold cache; that takes several seconds. Tell the user something
    # is happening so the TUI launch doesn't look frozen.
    if oneshot and response_format is not None:
        provider, rt = _get_chat_runtime()
    else:
        with console.status("Detecting providers…", spinner="dots"):
            provider, rt = _get_chat_runtime()
    if rt is None:
        if not _prompt_first_run_setup(console):
            sys.exit(1)
        provider, rt = _get_chat_runtime()
        if rt is None:
            sys.exit(1)
    model = getattr(rt, "model", "?")

    from openprogram.agent.management import manager as _A
    agent = _A.get_default()
    if agent is None:
        agent = _A.create("main", make_default=True)

    # Rich REPL fallback / oneshot path. For the chat REPL, the banner
    # below already shows agent + session — no need for a separate
    # "New session ..." line above it. For ``--print`` / oneshot, skip
    # the banner entirely and just print the reply; the user wanted a
    # quick answer, not a UI.
    if oneshot:
        reply = _run_turn_with_history(
            agent,
            session_id,
            oneshot,
            response_format=response_format,
        )
        if response_format is None:
            print(reply)
        else:
            import json as _json
            print(_json.dumps(reply, ensure_ascii=False, separators=(",", ":")))
        return
    if resume:
        console.print(f"[dim]↪ Resuming previous session.[/]")

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

    _print_banner(console, provider, model,
                  agent_id=getattr(agent, "id", "") or "",
                  session_id=session_id)

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
        # Stream the reply token-by-token to the terminal. The turn
        # function writes directly to stdout via ``rt.on_stream``; we
        # don't print again after it returns or the text would
        # duplicate. The returned ``reply`` is the canonical full
        # string — passed to TTS which needs the whole utterance.
        reply = _run_turn_with_history(
            agent, session_id, user_input, console=console,
        )
        # Fire-and-forget TTS; no-ops unless tts.provider is set.
        try:
            from openprogram.tts import speak
            speak(reply)
        except Exception:
            pass
