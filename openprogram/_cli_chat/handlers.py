"""Slash-command dispatch + per-verb handlers for the CLI chat REPL."""
from __future__ import annotations


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


def _handle_slash(cmd: str, console, rt,
                  agent=None, session_id: str = "") -> bool:
    """Handle a /slash command. Return True if the session should exit."""
    from openprogram._cli_chat.banner import (
        _tool_inventory, _skill_inventory,
        _function_inventory, _application_inventory,
    )
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
        port = 8110
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
        return _handle_copy(console, agent, session_id)

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
        console.print(f"[bold]session:[/] {session_id or '(none)'}")
        console.print(f"[bold]agent:[/]   {agent.id if agent else '(none)'}")
        return False

    if verb == "login":
        return _handle_login(args, console, agent)

    if verb == "attach":
        return _handle_attach(args, console, agent, session_id)

    if verb == "detach":
        return _handle_detach(args, console)

    if verb in ("connections", "conns"):
        return _handle_connections(console, session_id)

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


# --- Channel attach / detach / login --------------------------------------

def _handle_login(args: list[str], console, agent) -> bool:
    """Create a channel account if needed, prompt for credentials
    (QR for WeChat; token paste for the rest), and wire inbound
    messages from it to the current agent.
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

    if _accts.get(channel, account_id) is None:
        _accts.create(channel, account_id)
        console.print(f"[dim]Created {channel}:{account_id}[/]")

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
    """``/model`` lists every enabled model; ``/model <id>`` switches."""
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
    """``/agent`` lists agents; ``/agent <id>`` sets the default."""
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
    """REPL-only stub. The TUI overrides this via Ctrl+N."""
    console.print(
        "[yellow]/new applies in the TUI. "
        "In the Rich REPL, exit and relaunch (or use Ctrl+N inside "
        "the TUI) to start a fresh session.[/]"
    )
    return False


def _handle_copy(console, agent, session_id: str) -> bool:
    """Copy the last assistant message to the system clipboard."""
    from openprogram.webui import persistence as _persist
    if not (agent and session_id):
        console.print("[yellow]No active session.[/]")
        return False
    data = _persist.load_session(agent.id, session_id)
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
        console.print("[dim]No clipboard backend; printing instead:[/]")
        console.print(text)
    return False


def _handle_attach(args: list[str], console, agent, session_id: str) -> bool:
    positional, flags = _parse_kv_args(args)
    if not session_id or agent is None:
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
            agent_id=agent.id, session_id=session_id,
        )
        console.print(
            f"[green]Attached[/] {channel}:{account_id}:"
            f"{peer_kind}:{peer} → session {session_id}"
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


def _handle_connections(console, session_id: str) -> bool:
    if not session_id:
        console.print("[yellow]No active session.[/]")
        return False
    from openprogram.agents import session_aliases as _sa
    rows = _sa.list_for_session(session_id)
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
