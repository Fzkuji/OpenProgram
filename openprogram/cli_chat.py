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
    ("/session", "show the current session id + agent"),
    ("/login <channel> [--id X]",
                 "log in to a channel bot (wechat: QR, others: paste "
                 "token). Also wires inbound messages to this agent."),
    ("/attach <channel> <peer> [--account X] [--kind direct|group]",
                 "route a specific channel peer's messages into this "
                 "session (auto-starts the channels worker)"),
    ("/detach <channel> <peer> [--account X] [--kind ...]",
                 "remove the alias for a channel peer"),
    ("/connections", "list every channel peer currently aliased to "
                     "this session"),
    ("/profile [name]", "show or switch active profile (restart required to switch)"),
    ("/clear", "clear the screen"),
    ("/quit", "exit"),
]


def _handle_slash(cmd: str, console, rt,
                  agent=None, conv_id: str = "") -> bool:
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
        return _handle_model(args, console, agent)

    if verb == "agent":
        return _handle_agent_switch(args, console, agent)

    if verb == "new":
        return _handle_new_session(console)

    if verb == "copy":
        return _handle_copy(console, agent, conv_id)

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

    if verb == "session":
        console.print(f"[bold]session:[/] {conv_id or '(none)'}")
        console.print(f"[bold]agent:[/]   {agent.id if agent else '(none)'}")
        return False

    if verb == "login":
        return _handle_login(args, console, agent)

    if verb == "attach":
        return _handle_attach(args, console, agent, conv_id)

    if verb == "detach":
        return _handle_detach(args, console)

    if verb in ("connections", "conns"):
        return _handle_connections(console, conv_id)

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


# --- Slash: channel attach / detach / connections -------------------------

_VALID_CHANNELS = ("wechat", "telegram", "discord", "slack")


def _parse_kv_args(args: list[str]) -> tuple[list[str], dict[str, str]]:
    """Split [flag, value, positional, ...] into (positionals, flags).

    Supports both ``--account=work`` and ``--account work``.
    """
    positionals: list[str] = []
    flags: dict[str, str] = {}
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("--"):
            key, _, val = a.partition("=")
            key = key[2:]
            if val:
                flags[key] = val
            elif i + 1 < len(args):
                flags[key] = args[i + 1]
                i += 1
            else:
                flags[key] = ""
        else:
            positionals.append(a)
        i += 1
    return positionals, flags


def _handle_login(args: list[str], console, agent) -> bool:
    """Create a channel account if needed, prompt for credentials
    (QR for WeChat; token paste for the rest), and make sure the
    current agent will receive inbound messages from it.
    """
    positional, flags = _parse_kv_args(args)
    if not positional:
        console.print(
            "[yellow]Usage: /login <channel> [--id X][/]  "
            f"channels: {', '.join(_VALID_CHANNELS)}"
        )
        return False
    channel = positional[0]
    if channel not in _VALID_CHANNELS:
        console.print(f"[yellow]Unknown channel {channel!r}.[/]")
        return False
    account_id = flags.get("id", "default")

    try:
        from openprogram.channels import accounts as _accts
        from openprogram.channels import bindings as _bindings
        from openprogram.worker import current_worker_pid, spawn_detached
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]channel modules missing: {e}[/]")
        return False

    # 1. Ensure the account row exists
    if _accts.get(channel, account_id) is None:
        _accts.create(channel, account_id)
        console.print(f"[dim]Created {channel}:{account_id}[/]")

    # 2. Credential acquisition — per-channel
    if channel == "wechat":
        from openprogram.channels.wechat import login_account
        console.print(
            f"[cyan]Opening WeChat QR for account `{account_id}`. "
            "Scan with your phone's WeChat and confirm on the device.[/]"
        )
        creds = login_account(account_id)
        if not creds:
            console.print("[red]WeChat login cancelled / failed.[/]")
            return False
    else:
        # Token paste path — don't echo the token.
        import getpass as _gp
        if channel == "slack":
            bot = _gp.getpass("Slack bot token (xoxb-...): ")
            app = _gp.getpass("Slack app-level token (xapp-...): ")
            patch: dict = {}
            if bot:
                patch["bot_token"] = bot
            if app:
                patch["app_token"] = app
            if not patch:
                console.print("[yellow]No token entered.[/]")
                return False
            _accts.update_credentials(channel, account_id, patch)
        else:
            label = {"telegram": "Telegram", "discord": "Discord"}[channel]
            tok = _gp.getpass(f"{label} bot token: ")
            if not tok:
                console.print("[yellow]No token entered.[/]")
                return False
            _accts.update_credentials(channel, account_id, {"bot_token": tok})
        console.print(f"[green]{channel}:{account_id} credentials saved.[/]")

    # 3. Make sure the agent actually receives inbound for this
    #    (channel, account). If a matching binding already exists we
    #    don't duplicate.
    if agent is not None:
        already = any(
            b["agent_id"] == agent.id
            and b["match"].get("channel") == channel
            and b["match"].get("account_id") in (None, account_id)
            for b in _bindings.list_for_agent(agent.id)
        )
        if not already:
            _bindings.add(agent.id, {
                "channel": channel, "account_id": account_id,
            })
            console.print(
                f"[dim]Bound {channel}:{account_id} → agent "
                f"{agent.id}.[/]"
            )

    # 4. Worker up so polling actually starts
    if current_worker_pid() is None:
        console.print("[dim]Starting channels worker...[/]")
        spawn_detached()
    else:
        console.print("[dim]Channels worker already running.[/]")
    console.print(
        f"[green]Done.[/] Messages from {channel}:{account_id} "
        f"will flow into agent {agent.id if agent else '?'}. "
        f"Use /attach {channel} <peer_id> to pin a specific peer "
        f"to THIS session."
    )
    return False


def _handle_model(args: list[str], console, agent) -> bool:
    """``/model`` lists every enabled model; ``/model <id>`` switches.
    Both forms support ``provider/id`` and bare ``id`` if the id is
    unique across providers."""
    from openprogram.webui import _model_catalog as mc
    from openprogram.agents import manager as _A
    from openprogram.agents import runtime_registry as _R
    enabled = mc.list_enabled_models()
    if not args:
        if not enabled:
            console.print(
                "[yellow]No enabled models. Run "
                "`openprogram providers setup` and pick at least one.[/]"
            )
            return False
        cur = ""
        if agent and agent.model.provider and agent.model.id:
            cur = f"{agent.model.provider}/{agent.model.id}"
        console.print(
            f"[bold]Current model[/]: [cyan]{cur or '(none)'}[/]"
        )
        console.print(
            "[bold]Available[/] (use [cyan]/model <id>[/] to switch):"
        )
        for m in enabled:
            full = f"{m['provider']}/{m['id']}"
            tag = " ← current" if full == cur else ""
            name = m.get("name") or m["id"]
            console.print(f"  [cyan]{full:42}[/]  [dim]{name}[/]{tag}")
        return False

    target = args[0].strip()
    # Resolve target — accept "provider/id" or bare id (if unique).
    matches = [m for m in enabled
               if f"{m['provider']}/{m['id']}" == target]
    if not matches:
        matches = [m for m in enabled if m["id"] == target]
    if not matches:
        console.print(f"[yellow]No enabled model matches {target!r}.[/]")
        return False
    if len(matches) > 1:
        console.print(
            f"[yellow]{target!r} is ambiguous: "
            f"{', '.join(m['provider'] + '/' + m['id'] for m in matches)}. "
            f"Use the full provider/id form.[/]"
        )
        return False
    m = matches[0]
    if agent is None:
        console.print("[yellow]No active agent.[/]")
        return False
    _A.update(agent.id, {"model": {"provider": m["provider"], "id": m["id"]}})
    _R.invalidate(agent.id)
    console.print(
        f"[green]Agent[/] [cyan]{agent.id}[/]: model = "
        f"[bold]{m['provider']}/{m['id']}[/]"
    )
    return False


def _handle_agent_switch(args: list[str], console, agent) -> bool:
    """``/agent`` lists agents; ``/agent <id>`` switches.

    Switching mid-REPL is partial — it changes the *default* agent
    for new sessions but does not move the current REPL into the new
    agent's runtime (that path lives only in the TUI). Tell the user
    so they aren't surprised."""
    from openprogram.agents import manager as _A
    if not args:
        rows = _A.list_all()
        if not rows:
            console.print(
                "[yellow]No agents. "
                "`openprogram agents add main`.[/]"
            )
            return False
        cur = agent.id if agent else ""
        console.print("[bold]Agents[/]:")
        for a in rows:
            tag = " ← current" if a.id == cur else (
                "  [dim](default)[/]" if a.default else ""
            )
            pm = (f"{a.model.provider}/{a.model.id}"
                  if a.model.provider else "no model")
            console.print(
                f"  [cyan]{a.id:14}[/]  [dim]{pm}[/]{tag}"
            )
        console.print(
            "[dim]To switch: type[/] [cyan]/agent <id>[/]  "
            "[dim](TUI: Ctrl+A also cycles)[/]"
        )
        return False
    target = args[0].strip()
    if _A.get(target) is None:
        console.print(f"[yellow]No agent {target!r}.[/]")
        return False
    _A.set_default(target)
    console.print(
        f"[green]Default agent set to[/] [cyan]{target}[/]. "
        "[dim](Open a new REPL or use /new for the change to take "
        "effect — current REPL keeps its session bound to the old "
        "agent.)[/]"
    )
    return False


def _handle_new_session(console) -> bool:
    """REPL-only stub. The TUI overrides this via Ctrl+N. In the
    Rich REPL we can't reset the current process's session_id from
    here, so we tell the user to relaunch."""
    console.print(
        "[yellow]/new applies in the TUI. "
        "In the Rich REPL, exit and relaunch (or use Ctrl+N inside "
        "the TUI) to start a fresh session.[/]"
    )
    return False


def _handle_copy(console, agent, conv_id: str) -> bool:
    """Copy the last assistant message to the system clipboard.

    The TUI has its own override that uses ``app.copy_to_clipboard``;
    this Rich-REPL fallback uses pyperclip when present, otherwise
    prints the message to stdout so the user can use the terminal's
    own selection."""
    from openprogram.webui import persistence as _persist
    if not (agent and conv_id):
        console.print("[yellow]No active session.[/]")
        return False
    data = _persist.load_conversation(agent.id, conv_id)
    if not data:
        console.print("[yellow]Session has no messages yet.[/]")
        return False
    last_assistant = next(
        (m for m in reversed(data.get("messages") or [])
         if m.get("role") == "assistant"),
        None,
    )
    if last_assistant is None:
        console.print("[yellow]No assistant reply to copy yet.[/]")
        return False
    text = last_assistant.get("content") or ""
    try:
        import pyperclip
        pyperclip.copy(text)
        console.print(f"[green]Copied {len(text)} chars to clipboard.[/]")
    except Exception:
        # No pyperclip / no system clipboard daemon — fall back to
        # printing so the user can still get the text out.
        console.print("[dim]No clipboard backend; printing instead:[/]")
        console.print(text)
    return False


def _handle_attach(args: list[str], console, agent, conv_id: str) -> bool:
    positional, flags = _parse_kv_args(args)
    if not conv_id or agent is None:
        console.print("[yellow]No active session — can't attach.[/]")
        return False
    if len(positional) < 2:
        console.print(
            "[yellow]Usage: /attach <channel> <peer_id> "
            "[--account X] [--kind direct|group|channel][/]\n"
            f"  channels: {', '.join(_VALID_CHANNELS)}"
        )
        return False
    channel, peer = positional[0], positional[1]
    if channel not in _VALID_CHANNELS:
        console.print(f"[yellow]Unknown channel {channel!r}. "
                      f"One of: {', '.join(_VALID_CHANNELS)}.[/]")
        return False
    account_id = flags.get("account", "default")
    peer_kind = flags.get("kind", "direct")

    try:
        from openprogram.agents import session_aliases as _sa
        from openprogram.worker import current_worker_pid, spawn_detached
        _row, replaced = _sa.attach(
            channel=channel, account_id=account_id,
            peer_kind=peer_kind, peer_id=peer,
            agent_id=agent.id, session_id=conv_id,
        )
        console.print(
            f"[green]Attached[/] {channel}:{account_id}:"
            f"{peer_kind}:{peer} → session {conv_id}"
        )
        if replaced is not None:
            console.print(
                f"[yellow]Replaced[/] previous binding "
                f"{channel}:{account_id}:{peer_kind}:{peer} "
                f"→ session {replaced.get('session_id')}"
            )
        if current_worker_pid() is None:
            console.print(
                "[dim]Starting channels worker in the background so "
                "inbound messages can arrive...[/]"
            )
            spawn_detached()
        return False
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]Attach failed:[/] {type(e).__name__}: {e}")
        return False


def _handle_detach(args: list[str], console) -> bool:
    positional, flags = _parse_kv_args(args)
    if len(positional) < 2:
        console.print(
            "[yellow]Usage: /detach <channel> <peer_id> "
            "[--account X] [--kind direct|group|channel][/]"
        )
        return False
    channel, peer = positional[0], positional[1]
    if channel not in _VALID_CHANNELS:
        console.print(f"[yellow]Unknown channel {channel!r}.[/]")
        return False
    account_id = flags.get("account", "default")
    peer_kind = flags.get("kind", "direct")
    from openprogram.agents import session_aliases as _sa
    removed = _sa.detach(
        channel=channel, account_id=account_id,
        peer_kind=peer_kind, peer_id=peer,
    )
    if removed:
        console.print(f"[green]Detached[/] "
                      f"{channel}:{account_id}:{peer_kind}:{peer}")
    else:
        console.print("[yellow]No matching alias.[/]")
    return False


def _handle_connections(console, conv_id: str) -> bool:
    if not conv_id:
        console.print("[yellow]No active session.[/]")
        return False
    from openprogram.agents import session_aliases as _sa
    rows = _sa.list_for_session(conv_id)
    if not rows:
        console.print(
            "[dim]No channel peers attached to this session yet. "
            "Try: /attach wechat <openid>[/]"
        )
        return False
    from rich.table import Table
    tbl = Table(show_header=True, box=None, padding=(0, 2))
    tbl.add_column("channel", style="cyan")
    tbl.add_column("account", style="dim")
    tbl.add_column("peer", style="bold")
    for r in rows:
        tbl.add_row(r["channel"], r["account_id"],
                    f"{r['peer']['kind']}:{r['peer']['id']}")
    console.print(tbl)
    return False


# --- Chat turn -------------------------------------------------------------

def _run_turn_with_history(agent, conv_id: str, message: str) -> str:
    """Run one CLI chat turn, persisted to
    ``<state>/agents/<agent_id>/sessions/<conv_id>/``.

    Loads the session's prior messages, renders them as a
    [User]/[Assistant] prefix, calls rt.exec through the per-agent
    runtime registry, and appends + saves both sides.
    """
    import time as _time
    import uuid as _uuid
    from openprogram.agents import runtime_registry as _runtimes
    from openprogram.agents.context_engine import default_engine as _engine
    from openprogram.webui import persistence as _persist

    data = _persist.load_conversation(agent.id, conv_id) or {}
    meta = {k: v for k, v in data.items()
            if k not in ("messages", "function_trees")}
    messages: list = list(data.get("messages") or [])
    if not meta:
        meta = {
            "id": conv_id,
            "agent_id": agent.id,
            "title": message[:50] + ("..." if len(message) > 50 else ""),
            "created_at": _time.time(),
            "source": "cli",
            "_titled": True,
        }

    user_id = _uuid.uuid4().hex[:12]
    user_msg = {
        "role": "user", "id": user_id,
        "parent_id": messages[-1]["id"] if messages else None,
        "content": message, "timestamp": _time.time(),
        "source": "cli", "peer_display": "you",
    }
    _engine.ingest(messages, user_msg)

    assembled = _engine.assemble(agent, meta, messages[:-1])
    exec_content: list[dict] = []
    if assembled.system_prompt_addition:
        exec_content.append({
            "type": "text", "text": assembled.system_prompt_addition,
        })
    exec_content.extend(assembled.messages)
    exec_content.append({"type": "text", "text": message})

    try:
        rt = _runtimes.get_runtime_for(agent)
        reply = rt.exec(content=exec_content)
        reply_text = str(reply or "").strip() or ""
    except Exception as e:  # noqa: BLE001
        reply_text = f"[error] {type(e).__name__}: {e}"

    reply_msg = {
        "role": "assistant", "id": user_id + "_reply",
        "parent_id": user_id,
        "content": reply_text, "timestamp": _time.time(), "source": "cli",
    }
    _engine.ingest(messages, reply_msg)
    _engine.after_turn(agent, meta, messages)
    meta["head_id"] = reply_msg["id"]
    meta["_last_touched"] = _time.time()

    _persist.save_meta(agent.id, conv_id, meta)
    _persist.save_messages(agent.id, conv_id, messages)
    return reply_text


# --- Entry point -----------------------------------------------------------

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
        conv_id = resume
    else:
        conv_id = "local_" + _uuid.uuid4().hex[:10]

    # Full-screen TUI path (default). One-shot stays on the Rich path
    # because rendering a scroll buffer for a single turn is overkill.
    if tui and not oneshot:
        try:
            from openprogram.cli_ink import run_ink_tui
            run_ink_tui(agent=agent, conv_id=conv_id, rt=rt)
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
        console.print(f"[dim]Resuming session {conv_id} under "
                      f"agent {agent.id}[/]")
    else:
        console.print(f"[dim]New session {conv_id} under "
                      f"agent {agent.id}[/]")

    if oneshot:
        reply = _run_turn_with_history(agent, conv_id, oneshot)
        print(reply)
        return

    # Show the channels worker status without asking the user to start
    # anything: the primary thing this REPL does is chat. Channels are
    # an opt-in "let external users talk to my agent" feature, not a
    # gatekeeper on launch. We surface the status line only if a worker
    # happens to already be running, so the user knows their bindings
    # are live.
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
                             agent=agent, conv_id=conv_id):
                return
            continue
        reply = _run_turn_with_history(agent, conv_id, user_input)
        console.print()
        console.print(reply)
        # Fire-and-forget TTS; no-ops unless tts.provider is set.
        try:
            from openprogram.tts import speak
            speak(reply)
        except Exception:
            pass
