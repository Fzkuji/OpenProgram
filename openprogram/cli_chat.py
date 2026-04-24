"""Terminal chat for ``openprogram`` / ``openprogram --cli``.

Hermes-style welcome banner (tools + skills inventory) followed by a
REPL. The REPL is deliberately thin: each turn goes through the same
chat runtime the web UI uses, so behaviour stays aligned. Slash
commands (``/help``, ``/web``, ``/quit``, ...) are handled locally.

Multi-turn memory depends on the underlying runtime. The Claude Code
runtime keeps a persistent subprocess session so successive
``exec()`` calls share context; HTTP runtimes are stateless here —
left for a follow-up when we plumb conversation history through.
"""
from __future__ import annotations

import os
import sys
from typing import Any


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

    Used after an inline setup run so the newly-imported
    credentials get picked up without restarting the process.
    """
    from openprogram.webui import _runtime_management as rm
    rm._providers_initialized = False
    rm._chat_runtime = None
    rm._chat_provider = None
    rm._chat_model = None
    rm._default_runtime = None
    rm._default_provider = None


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


def _tool_inventory() -> tuple[int, list[str]]:
    from openprogram.tools import ALL_TOOLS, list_available
    names = list_available()  # only tools whose check_fn currently passes
    # Prefer the gated list; if the helper returns empty (no gating), fall
    # back to the full registry so the banner isn't misleadingly blank.
    if not names:
        names = list(ALL_TOOLS.keys())
    return len(names), names


def _skill_inventory() -> tuple[int, list[tuple[str, str]]]:
    """Return (count, [(name, description), ...]) for enabled skills.

    Respects ``skills.disabled`` in ``~/.agentic/config.json`` so the
    banner / /skills listing match what the runtime actually uses.
    """
    try:
        from openprogram.agentic_programming import (
            default_skill_dirs, load_skills,
        )
        from openprogram.setup import read_disabled_skills
        skills = load_skills(default_skill_dirs())
        disabled = read_disabled_skills()
        skills = [s for s in skills if s.name not in disabled]
    except Exception:
        return 0, []
    return len(skills), [(s.name, getattr(s, "description", "") or "") for s in skills]


def _function_inventory() -> tuple[int, list[str]]:
    """Return (count, [name, ...]) of agentic functions in programs/functions/.

    Scans buildin / third_party / meta for .py files. Names are the file
    stems (e.g. ``deep_work``, ``chat``, ``sentiment``). Private helpers
    (leading underscore) and ``__init__`` are skipped.
    """
    import os
    base = os.path.join(os.path.dirname(__file__), "programs", "functions")
    names: list[str] = []
    for sub in ("buildin", "third_party", "meta"):
        d = os.path.join(base, sub)
        if not os.path.isdir(d):
            continue
        for fname in sorted(os.listdir(d)):
            if not fname.endswith(".py"):
                continue
            stem = fname[:-3]
            if stem.startswith("_") or stem == "__init__":
                continue
            names.append(stem)
    return len(names), names


def _application_inventory() -> tuple[int, list[str]]:
    """Return (count, [name, ...]) of applications in programs/applications/.

    Subdirs are apps; bare .py files (besides __init__) count too.
    """
    import os
    d = os.path.join(os.path.dirname(__file__), "programs", "applications")
    if not os.path.isdir(d):
        return 0, []
    names: list[str] = []
    for entry in sorted(os.listdir(d)):
        full = os.path.join(d, entry)
        if entry.startswith("_") or entry.startswith("."):
            continue
        if os.path.isdir(full) and not entry.startswith("__"):
            names.append(entry)
        elif entry.endswith(".py") and entry != "__init__.py":
            names.append(entry[:-3])
    return len(names), names


def _section_text(label: str, items: list[str], count: int, accent: str,
                  empty_msg: str = "none") -> "Text":
    from rich.text import Text
    t = Text()
    t.append(f"{label} ", style="bold")
    t.append(f"({count})\n", style="dim")
    if count == 0:
        t.append(empty_msg, style="dim italic")
        return t
    preview = items[:6]
    t.append(", ".join(preview), style=accent)
    if count > len(preview):
        t.append(f" (+{count - len(preview)} more)", style="dim")
    return t


def _print_banner(console, provider: str, model: str) -> None:
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich import box

    tool_count, tool_names = _tool_inventory()
    skill_count, skill_items = _skill_inventory()
    fn_count, fn_names = _function_inventory()
    app_count, app_names = _application_inventory()

    logo = Text("OpenProgram", style="bold bright_blue")
    subtitle = Text(f"  ·  {provider}/{model}", style="dim")
    header = logo + subtitle

    # Two rows x two columns: tools/skills on top, functions/applications
    # on bottom. Functions + applications together form "programs" — the
    # user-callable code the harness runs. Tools + skills are the
    # LLM-side surface (capabilities + instruction packs).
    grid = Table.grid(padding=(0, 2), expand=True)
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)

    grid.add_row(
        _section_text("Tools", tool_names, tool_count, "cyan"),
        _section_text("Skills", [n for n, _ in skill_items], skill_count,
                      "magenta", empty_msg="no skills loaded"),
    )
    grid.add_row(Text(""), Text(""))  # spacer row
    grid.add_row(
        _section_text("Functions", fn_names, fn_count, "green",
                      empty_msg="no functions registered"),
        _section_text("Applications", app_names, app_count, "yellow",
                      empty_msg="no applications registered"),
    )

    footer = Text()
    footer.append(f"{tool_count} tools", style="cyan")
    footer.append(" · ")
    footer.append(f"{skill_count} skills", style="magenta")
    footer.append(" · ")
    footer.append(f"{fn_count} functions", style="green")
    footer.append(" · ")
    footer.append(f"{app_count} apps", style="yellow")
    footer.append(" · /help for commands", style="dim")

    panel_body = Table.grid(padding=(1, 0))
    panel_body.add_row(grid)
    panel_body.add_row(footer)

    console.print()
    console.print(Panel(
        panel_body,
        title=header,
        border_style="bright_blue",
        box=box.ROUNDED,
        padding=(1, 2),
    ))
    console.print(
        Text("Tip: ", style="yellow bold")
        + Text("type your message, or /help to see commands.", style="dim")
    )


# --- Slash commands --------------------------------------------------------

SLASH_HELP = [
    ("/help", "show this message"),
    ("/web [port]", "launch the Web UI in your browser"),
    ("/model", "show current chat model"),
    ("/tools", "list available tools"),
    ("/skills", "list discovered skills"),
    ("/functions", "list agentic functions (programs/functions/)"),
    ("/apps", "list applications (programs/applications/)"),
    ("/profile [name]", "show or switch active profile (restart required to switch)"),
    ("/clear", "clear the screen"),
    ("/quit", "exit"),
]


def _handle_slash(cmd: str, console, rt) -> bool:
    """Handle a /slash command. Return True if the session should exit."""
    raw = cmd[1:].strip()
    parts = raw.split()
    verb = (parts[0] if parts else "").lower()
    args = parts[1:]

    if verb in ("q", "quit", "exit"):
        console.print("[dim]Goodbye.[/]")
        return True

    if verb in ("", "h", "help", "?"):
        from rich.table import Table
        tbl = Table(show_header=False, box=None, padding=(0, 2))
        tbl.add_column(style="bold cyan")
        tbl.add_column(style="dim")
        for name, desc in SLASH_HELP:
            tbl.add_row(name, desc)
        console.print(tbl)
        return False

    if verb == "web":
        port = 8765
        if args:
            try:
                port = int(args[0])
            except ValueError:
                console.print(f"[yellow]Invalid port: {args[0]!r}[/]")
                return False
        console.print(f"[dim]Starting Web UI at http://localhost:{port} ...[/]")
        from openprogram.cli import _cmd_web  # lazy to avoid cycle
        _cmd_web(port, True)
        return True

    if verb == "model":
        console.print(f"[bold]{getattr(rt, 'model', '?')}[/]")
        return False

    if verb == "tools":
        count, names = _tool_inventory()
        console.print(f"[bold]{count} tools[/]")
        for n in names:
            console.print(f"  [cyan]{n}[/]")
        return False

    if verb == "skills":
        count, items = _skill_inventory()
        console.print(f"[bold]{count} skills[/]")
        for name, desc in items:
            short = (desc[:80] + "...") if len(desc) > 80 else desc
            console.print(f"  [magenta]{name}[/]  [dim]{short}[/]")
        return False

    if verb in ("functions", "fns"):
        count, names = _function_inventory()
        console.print(f"[bold]{count} functions[/]")
        for n in names:
            console.print(f"  [green]{n}[/]")
        return False

    if verb in ("apps", "applications"):
        count, names = _application_inventory()
        console.print(f"[bold]{count} applications[/]")
        for n in names:
            console.print(f"  [yellow]{n}[/]")
        return False

    if verb == "clear":
        console.clear()
        return False

    if verb == "profile":
        from openprogram.paths import get_active_profile, get_state_dir, set_active_profile
        if not args:
            profile = get_active_profile() or "default"
            console.print(f"[bold]profile:[/] {profile}")
            console.print(f"[dim]state dir: {get_state_dir()}[/]")
            return False
        target = args[0]
        set_active_profile(None if target == "default" else target)
        console.print(
            f"[yellow]Profile set to {target!r}.[/]  "
            "Switching mid-session leaves your chat runtime bound to the "
            "old profile's credentials. Re-launch to pick up the new "
            "profile fully:"
        )
        restart_hint = (
            f"  openprogram --profile {target}"
            if target != "default" else "  openprogram"
        )
        console.print(f"[cyan]{restart_hint}[/]")
        console.print("[dim]Exiting so you can restart cleanly.[/]")
        return True

    console.print(f"[yellow]Unknown command: /{verb}[/]  (try /help)")
    return False


# --- Chat turn -------------------------------------------------------------

def _run_turn(rt, message: str) -> str:
    """Send one user message to the runtime; return the assistant reply."""
    try:
        reply = rt.exec(content=[{"type": "text", "text": message}])
    except Exception as e:  # noqa: BLE001
        return f"[error] {type(e).__name__}: {e}"
    if reply is None:
        return ""
    if isinstance(reply, str):
        return reply
    return str(reply)


# --- Entry point -----------------------------------------------------------

def run_cli_chat(oneshot: str | None = None) -> None:
    """Launch the terminal chat. ``oneshot`` runs one turn and exits."""
    from rich.console import Console
    console = Console()

    provider, rt = _get_chat_runtime()
    if rt is None:
        # Hermes-style first-run: offer the setup inline so the
        # user doesn't have to exit and re-invoke. If they accept and
        # the wizard imports at least one credential, we continue into
        # the chat; otherwise we exit cleanly.
        if not _prompt_first_run_setup(console):
            sys.exit(1)
        provider, rt = _get_chat_runtime()
        if rt is None:
            sys.exit(1)
    model = getattr(rt, "model", "?")

    if oneshot:
        reply = _run_turn(rt, oneshot)
        print(reply)
        return

    # Show the channels worker status without asking the user to start
    # anything: the primary thing this REPL does is chat. Channels are
    # an opt-in "let external users talk to my agent" feature, not a
    # gatekeeper on launch. We surface the status line only if a worker
    # happens to already be running, so the user knows their bindings
    # are live.
    try:
        from openprogram.channels.worker import current_worker_pid
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
            if _handle_slash(user_input, console, rt):
                return
            continue
        reply = _run_turn(rt, user_input)
        console.print()
        console.print(reply)
        # Fire-and-forget TTS; no-ops unless tts.provider is set.
        try:
            from openprogram.tts import speak
            speak(reply)
        except Exception:
            pass
