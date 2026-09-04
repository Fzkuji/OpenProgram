"""Slash-command dispatch + per-verb handlers for the CLI chat REPL.

Command existence, help text, and completion all come from the unified
six-layer registry in ``openprogram/commands`` — the same table the Web
composer reads. The REPL registers its local actions into the registry's
``builtin`` layer (handler = the action name as a marker string) and
keeps ``_LOCAL_ACTIONS`` mapping each marker back to the local
implementation. Commands from every other layer (plugin / mcp / skill /
user / project) resolve through the same registry: their rendered body
is sent to the agent as this turn's message, matching the Web
composer's "expand into message" semantics.
"""
from __future__ import annotations

import os


# Builtin REPL actions: (name, aliases, argument_hint, description).
# Registered into the commands registry at dispatch time; /help reads
# back from the registry, never from this table.
_BUILTIN_SPECS: list[tuple[str, tuple[str, ...], str, str]] = [
    ("help", ("h", "?"), "", "list all slash commands"),
    ("web", (), "[port]", "launch the Web UI in your browser"),
    ("model", (), "[provider/id]", "show or switch the chat model"),
    ("agent", (), "[id]", "list agents or set the default"),
    ("new", (), "", "start a fresh session (TUI; the Rich REPL prints a hint)"),
    ("copy", (), "", "copy the last assistant reply to the clipboard"),
    ("tools", (), "", "list available tools"),
    ("skills", (), "", "list discovered skills"),
    ("functions", ("fns",), "", "list agentic functions (programs/workflow/)"),
    ("apps", ("applications",), "",
     "list installed programs (gui/research/wiki agents)"),
    ("mcp", (), "[verb]",
     "manage MCP servers: list (default), show <name>, restart <name>, "
     "enable <name>, disable <name>, rm <name>"),
    ("session", (), "", "show the current session id + agent"),
    ("jobs", (), "[job_id]", "show canonical resource state for background jobs"),
    ("login", (), "<channel> [--id X]",
     "log in to a channel bot (wechat: QR, others: paste token). "
     "Also wires inbound messages to this agent."),
    ("attach", (), "<channel> <peer> [--account X] [--kind direct|group]",
     "route a specific channel peer's messages into this session "
     "(auto-starts the channels worker)"),
    ("detach", (), "<channel> <peer> [--account X] [--kind ...]",
     "remove the alias for a channel peer"),
    ("connections", ("conns",), "",
     "list every channel peer currently aliased to this session"),
    ("goal", (), "[prompt | clear]",
     "run the Goal Workflow with current session context "
     "(bare /goal shows status; clear/stop/off/cancel removes it)"),
    ("compact", (), "[hint]",
     "compress conversation history (optional hint guides what to keep)"),
    ("context", (), "", "show token distribution across context window"),
    ("rewind", (), "[n]", "roll back code + conversation to a chosen point"),
    ("sandbox", (), "",
     "toggle system sandbox (restrict bash to cwd writes only)"),
    ("profile", (), "[name]",
     "show or switch active profile (restart required to switch)"),
    ("clear", (), "", "clear the screen"),
    ("quit", ("q", "exit"), "", "exit"),
]


def register_repl_builtins() -> None:
    """(Re-)register every REPL action into the commands registry's
    builtin layer. Idempotent and cheap (in-memory dict writes), so the
    dispatcher calls it per slash — that also survives test helpers
    wiping the registry. ``reload()`` never touches the builtin bucket.
    """
    from openprogram.commands.registry import register_builtin
    for name, aliases, hint, desc in _BUILTIN_SPECS:
        register_builtin(
            name, handler=name, description=desc,
            argument_hint=hint, aliases=list(aliases),
        )


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
    """Resolve a /slash command through the unified commands registry
    and run it. Return True if the session should exit."""
    register_repl_builtins()
    from openprogram.commands.dispatch import invoke

    raw = cmd[1:].strip() if cmd.startswith("/") else cmd.strip()
    if not raw:
        return _handle_help(console)

    res = invoke(cmd, session_id=session_id)
    if res.kind == "error":
        # Registry names are case-sensitive; the old REPL lowercased
        # verbs, so retry lowercased before giving up (/HELP works).
        head, _, rest = raw.partition(" ")
        if head != head.lower():
            res = invoke("/" + head.lower() + ((" " + rest) if rest else ""),
                         session_id=session_id)

    if res.kind == "local":
        action = _LOCAL_ACTIONS.get(res.local_handler)
        if action is not None:
            return action(res.raw_args.split(), console, rt, agent, session_id)
        if callable(res.local_handler):
            # Builtin registered by other host code with a real handler
            # (contract: handler(session_ctx, raw_args) -> result dict).
            out = res.local_handler(
                {"session_id": session_id, "cwd": os.getcwd()}, res.raw_args,
            )
            if isinstance(out, dict) and out.get("text"):
                console.print(str(out["text"]))
            return bool(isinstance(out, dict) and out.get("exit"))
        console.print(
            f"[yellow]/{res.command_name} has no local implementation "
            "in this REPL.[/]"
        )
        return False

    if res.kind == "prompt":
        # plugin / skill / user / project command: the rendered body
        # becomes this turn's message — same expansion semantics as the
        # Web composer (which drops the rendered text into the textarea
        # and the user sends it).
        if agent is None or not session_id:
            console.print("[yellow]No active session to run this command in.[/]")
            return False
        from openprogram.cli.repl.turn import _run_turn_with_history
        console.print(f"[dim]Expanding /{res.command_name} ({res.source})...[/]")
        _run_turn_with_history(agent, session_id, res.rendered, console=console)
        return False

    if res.kind == "mcp_prompt":
        # MCP prompt bodies live on the server; live clients run in the
        # worker process, which this Rich REPL doesn't have.
        console.print(
            f"[yellow]/{res.command_name} is an MCP prompt — it needs a "
            "running worker. Use the TUI (openprogram tui) or the Web UI.[/]"
        )
        return False

    console.print(f"[yellow]Unknown command: /{raw.split()[0]}[/]  (try /help)")
    return False


def _handle_help(console) -> bool:
    """Render /help from the registry — every source, one table."""
    from rich.table import Table
    from openprogram.commands import list_all
    tbl = Table(show_header=False, box=None, padding=(0, 2))
    tbl.add_column(style="bold cyan")
    tbl.add_column(style="dim")
    for spec in list_all():
        if not spec.user_invocable:
            continue
        hint = spec.raw.argument_hint if spec.raw else ""
        name = f"/{spec.name}" + (f" {hint}" if hint else "")
        desc = spec.description
        if spec.source != "builtin":
            # Skill/plugin descriptions run to paragraphs — keep help scannable.
            if len(desc) > 100:
                desc = desc[:100] + "..."
            desc = f"{desc}  {spec.source_label}" if desc else spec.source_label
        tbl.add_row(name, desc)
    console.print(tbl)
    return False


def _handle_quit(console) -> bool:
    console.print("[dim]Goodbye.[/]")
    return True


def _handle_web(args: list[str], console) -> bool:
    from openprogram.worker.lifecycle import resolve_worker_port
    port = resolve_worker_port()
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


def _handle_tools_list(console) -> bool:
    from openprogram.cli.repl.banner import _tool_inventory
    count, names = _tool_inventory()
    console.print(f"[bold]{count} tools[/]")
    for n in names:
        console.print(f"  [cyan]{n}[/]")
    return False


def _handle_skills_list(console) -> bool:
    from openprogram.cli.repl.banner import _skill_inventory
    count, items = _skill_inventory()
    console.print(f"[bold]{count} skills[/]")
    for name, desc in items:
        short = (desc[:80] + "...") if len(desc) > 80 else desc
        console.print(f"  [magenta]{name}[/]  [dim]{short}[/]")
    return False


def _handle_functions_list(console) -> bool:
    from openprogram.cli.repl.banner import _function_inventory
    count, names = _function_inventory()
    console.print(f"[bold]{count} functions[/]")
    for n in names:
        console.print(f"  [green]{n}[/]")
    return False


def _handle_apps_list(console) -> bool:
    from openprogram.cli.repl.banner import _application_inventory
    count, names = _application_inventory()
    console.print(f"[bold]{count} applications[/]")
    for n in names:
        console.print(f"  [yellow]{n}[/]")
    return False


def _handle_session_info(console, agent, session_id: str) -> bool:
    console.print(f"[bold]session:[/] {session_id or '(none)'}")
    console.print(f"[bold]agent:[/]   {agent.id if agent else '(none)'}")
    return False


def _handle_clear(console) -> bool:
    console.clear()
    return False


def _handle_profile(args: list[str], console) -> bool:
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


def _handle_jobs(args: list[str], console, session_id: str) -> bool:
    """Print the same JobResourceView DTO consumed by other surfaces."""
    from openprogram.cli.commands.jobs import job_resource_payload

    payload = job_resource_payload(
        job_id=args[0] if args else None,
        session_id=session_id,
    )
    console.print_json(data=payload)
    return False


# Marker string (as registered via ``register_repl_builtins``) → local
# implementation. Uniform adapter signature:
# ``(args, console, rt, agent, session_id) -> should_exit``.
_LOCAL_ACTIONS = {
    "help": lambda args, console, rt, agent, sid: _handle_help(console),
    "quit": lambda args, console, rt, agent, sid: _handle_quit(console),
    "web": lambda args, console, rt, agent, sid: _handle_web(args, console),
    "model": lambda args, console, rt, agent, sid: _handle_model(args, console, agent),
    "agent": lambda args, console, rt, agent, sid: _handle_agent_switch(args, console, agent),
    "new": lambda args, console, rt, agent, sid: _handle_new_session(console),
    "copy": lambda args, console, rt, agent, sid: _handle_copy(console, agent, sid),
    "tools": lambda args, console, rt, agent, sid: _handle_tools_list(console),
    "skills": lambda args, console, rt, agent, sid: _handle_skills_list(console),
    "functions": lambda args, console, rt, agent, sid: _handle_functions_list(console),
    "apps": lambda args, console, rt, agent, sid: _handle_apps_list(console),
    "mcp": lambda args, console, rt, agent, sid: _handle_mcp(args, console),
    "clear": lambda args, console, rt, agent, sid: _handle_clear(console),
    "session": lambda args, console, rt, agent, sid: _handle_session_info(console, agent, sid),
    "jobs": lambda args, console, rt, agent, sid: _handle_jobs(args, console, sid),
    "login": lambda args, console, rt, agent, sid: _handle_login(args, console, agent),
    "attach": lambda args, console, rt, agent, sid: _handle_attach(args, console, agent, sid),
    "detach": lambda args, console, rt, agent, sid: _handle_detach(args, console),
    "connections": lambda args, console, rt, agent, sid: _handle_connections(console, sid),
    "profile": lambda args, console, rt, agent, sid: _handle_profile(args, console),
    "goal": lambda args, console, rt, agent, sid: _handle_goal(
        args, console, rt, sid,
    ),
    "compact": lambda args, console, rt, agent, sid: _handle_compact(args, console, sid),
    "context": lambda args, console, rt, agent, sid: _handle_context(console, agent, sid),
    "rewind": lambda args, console, rt, agent, sid: _handle_rewind(args, console, sid),
    "sandbox": lambda args, console, rt, agent, sid: _handle_sandbox(console),
}




def _handle_goal(args: list[str], console, rt, session_id: str) -> bool:
    """Run the same public Goal Workflow used by Programs and Web chat."""
    if not session_id:
        console.print("[yellow]No active session.[/]")
        return False
    from openprogram.programs.workflow.goal import handle_goal_command
    out = handle_goal_command(session_id, " ".join(args))
    if out.get("text"):
        console.print(out["text"], markup=False)
    invocation = out.get("invoke")
    if not isinstance(invocation, dict):
        return False
    if rt is None:
        console.print("[yellow]No active Runtime — Goal was not started.[/]")
        return False
    try:
        from openprogram.agent.run_control import (
            reset_current_session_id,
            set_current_session_id,
        )
        from openprogram.programs.workflow.goal import goal

        token = set_current_session_id(session_id)
        try:
            result = goal(**dict(invocation.get("kwargs") or {}), runtime=rt)
        finally:
            reset_current_session_id(token)
        if result:
            console.print(result, markup=False)
    except Exception as e:  # noqa: BLE001 — surface, don't kill the REPL
        console.print(f"\n[red]Goal failed: {type(e).__name__}: {e}[/]")
    return False


def _handle_compact(args: list[str], console, session_id: str) -> bool:
    """Manual compaction — compress conversation history via LLM summary."""
    if not session_id:
        console.print("[yellow]No active session[/]")
        return False
    try:
        from openprogram.agent.dispatcher.titles import trigger_compaction

        hint = " ".join(args) if args else None
        console.print("[dim]Compacting conversation history...[/]")

        def _emit(envelope: dict) -> None:
            data = envelope.get("data") or {}
            if data.get("type") == "compaction_started":
                console.print("[dim]  LLM summarizing...[/]")

        result = trigger_compaction(
            session_id,
            on_event=_emit,
        )

        summary = result.get("summary", "")
        kept = result.get("kept_count", 0)
        preview = summary[:200] + "..." if len(summary) > 200 else summary
        console.print(f"[green]Compacted.[/]  Kept {kept} recent messages.")
        if preview:
            console.print(f"[dim]Summary: {preview}[/]")
    except Exception as e:
        console.print(f"[red]Compact failed: {type(e).__name__}: {e}[/]")
    return False


def _handle_context(console, agent, session_id: str) -> bool:
    """Show token distribution across the context window."""
    try:
        from openprogram.context.tokens import real_context_window, estimate_message_tokens
        from openprogram.context.budget import default_allocator
        from openprogram.context.components import build_system_prompt
        from openprogram.store import _store as _store_var

        # agent.model 是 AgentModelRef(provider, id)，没有 context_window
        # 字段，直接传 real_context_window 恒回落 128k。先查模型注册表。
        from openprogram.providers.models import get_model
        ref = getattr(agent, "model", None)
        model = (get_model(getattr(ref, "provider", "") or "",
                           getattr(ref, "id", "") or "")
                 if ref is not None else None)
        ctx_window = real_context_window(model) if model else 200_000

        sys_prompt = ""
        try:
            sys_prompt = build_system_prompt(agent)
        except Exception:
            pass
        sys_tokens = estimate_message_tokens({"role": "system", "content": sys_prompt}) if sys_prompt else 0

        history: list[dict] = []
        store = _store_var.get(None)
        if store and hasattr(store, "get_messages"):
            try:
                history = store.get_messages(session_id) or []
            except Exception:
                pass
        hist_tokens = 0
        for msg in history:
            try:
                hist_tokens += estimate_message_tokens(msg)
            except Exception:
                hist_tokens += 50

        tools = []
        try:
            from openprogram.programs import agent_tools
            tools = agent_tools()
        except Exception:
            pass
        tools_tokens = default_allocator._estimate_tools(tools)

        output_reserve = 16_384
        total_used = sys_tokens + hist_tokens + tools_tokens
        free = max(0, ctx_window - total_used - output_reserve)
        pct = (total_used + output_reserve) / ctx_window * 100 if ctx_window > 0 else 0

        def _fmt(n: int) -> str:
            if n >= 1000:
                return f"{n / 1000:.1f}k"
            return str(n)

        console.print(f"\n[bold]Context Usage: {_fmt(total_used + output_reserve)}/{_fmt(ctx_window)} tokens ({pct:.1f}%)[/]\n")
        console.print(f"  [cyan]System prompt:[/]   {_fmt(sys_tokens):>8} tokens  ({sys_tokens / ctx_window * 100:.1f}%)")
        console.print(f"  [cyan]Tools schema:[/]    {_fmt(tools_tokens):>8} tokens  ({tools_tokens / ctx_window * 100:.1f}%)")
        console.print(f"  [cyan]History:[/]         {_fmt(hist_tokens):>8} tokens  ({hist_tokens / ctx_window * 100:.1f}%)")
        console.print(f"  [cyan]Output reserve:[/]  {_fmt(output_reserve):>8} tokens  ({output_reserve / ctx_window * 100:.1f}%)")
        console.print(f"  [dim]Free space:[/]      {_fmt(free):>8} tokens  ({free / ctx_window * 100:.1f}%)")

        if pct > 60:
            console.print(f"\n  [yellow]Tip: consider /compact to free space[/]")
        console.print()

    except Exception as e:
        console.print(f"[red]Failed to compute context: {e}[/]")
    return False


def _handle_rewind(args: list[str], console, session_id: str) -> bool:
    """Roll back code + conversation to a chosen point (Claude Code /rewind style)."""
    if not session_id:
        console.print("[yellow]No active session.[/]")
        return False
    try:
        from openprogram.agent._rewind import (
            list_rewind_points,
            plan_rewind,
            rewind_to,
        )
        points = list_rewind_points(session_id)
        if not points:
            console.print("[dim]No rewind points available.[/]")
            return False

        if args:
            try:
                pick = int(args[0])
            except ValueError:
                console.print(f"[yellow]Invalid number: {args[0]}[/]")
                return False
        else:
            console.print("[bold]Rewind[/]  Select a restore point:\n")
            for i, p in enumerate(points, 1):
                files_str = ""
                if p["files_affected"]:
                    files_str = f"  [dim]({len(p['files_affected'])} file(s))[/]"
                console.print(
                    f"  [cyan]{i:>2}.[/] {p['summary']}{files_str}"
                )
            console.print(
                f"\n[dim]Enter a number (1-{len(points)}) or press Enter to cancel:[/]"
            )
            return False

        if pick < 1 or pick > len(points):
            console.print(f"[yellow]Out of range (1-{len(points)})[/]")
            return False

        target = points[pick - 1]
        plan = plan_rewind(session_id, target["msg_id"])
        if plan.get("status") != "ready":
            console.print(f"[red]Rewind blocked: {plan.get('error')}[/]")
            return False
        console.print(f"[bold]Rewind plan:[/] {target['summary']}")
        console.print(
            f"[dim]{plan['turns_reverted']} turn(s), "
            f"{len(plan['files'])} file(s)[/]"
        )
        confirmed = len(args) > 1 and args[1].lower() == "confirm"
        if not confirmed:
            console.print(
                f"[dim]Run /rewind {pick} confirm {plan['plan_hash']} "
                f"{plan['idempotency_key']} to apply this exact plan.[/]"
            )
            return False
        if len(args) < 4:
            console.print(
                "[red]Confirm requires the plan_hash and idempotency_key "
                "printed by the preview.[/]"
            )
            return False
        confirmed_hash, confirmed_key = args[2], args[3]
        if confirmed_hash != plan["plan_hash"]:
            console.print("[red]Rewind blocked: stale_plan[/]")
            return False

        result = rewind_to(
            session_id,
            target["msg_id"],
            idempotency_key=confirmed_key,
            expected_plan_hash=confirmed_hash,
        )
        if result.get("status") != "committed":
            for err in result["errors"]:
                console.print(f"[red]Rewind blocked: {err}[/]")
            return False
        restored = result.get("total_restored_paths") or []
        n = result.get("turns_reverted", 0)
        console.print(
            f"[bold green]Rewound {n} turn(s).[/] "
            f"Restored {len(restored)} file(s)."
        )
        for p in restored:
            console.print(f"  [dim]{p}[/]")
    except Exception as e:
        console.print(f"[red]Rewind error: {type(e).__name__}: {e}[/]")
    return False


# --- Sandbox toggle --------------------------------------------------------

def _handle_sandbox(console) -> bool:
    from openprogram.sandbox import is_enabled, set_mode, unavailable_reason
    current = is_enabled()
    reason = unavailable_reason()
    if not current and reason:
        console.print(f"[yellow]System sandbox not available.[/]  ({reason})")
        return False
    # Persisted, not held on this thread's context: the flag has to reach
    # background threads and spawned subprocesses too.
    set_mode(not current)
    console.print(f"[bold]Sandbox:[/] {'ON' if not current else 'OFF'}")
    if not current:
        console.print("[dim]Bash writes confined to the working directory; "
                      "credential paths unreadable; no network.[/]")
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
        from openprogram.channels.implementations.wechat import login_account
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
    from openprogram.webui import _model_listing as mc
    from openprogram.agent.management import manager as _A
    from openprogram.agent.management import runtime_registry as _R
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
    from openprogram.agent.management import manager as _A
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
        from openprogram.agent.management import session_aliases as _sa
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
    from openprogram.agent.management import session_aliases as _sa
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


def _handle_mcp(args: list[str], console) -> bool:
    """Dispatch /mcp [verb] inside the TUI/REPL.

    Verbs reuse the CLI implementations (which HTTP-hit the running
    worker), so behaviour matches `openprogram mcp ...` exactly.
    """
    from openprogram.cli.commands.mcp import (
        _cmd_mcp_list, _cmd_mcp_show, _cmd_mcp_restart,
        _cmd_mcp_enable, _cmd_mcp_disable, _cmd_mcp_rm,
    )
    verb = args[0].lower() if args else "list"
    rest = args[1:]
    if verb == "list":
        _cmd_mcp_list()
        return False
    if verb == "show" and rest:
        _cmd_mcp_show(rest[0])
        return False
    if verb == "restart" and rest:
        _cmd_mcp_restart(rest[0])
        return False
    if verb == "enable" and rest:
        _cmd_mcp_enable(rest[0])
        return False
    if verb == "disable" and rest:
        _cmd_mcp_disable(rest[0])
        return False
    if verb == "rm" and rest:
        _cmd_mcp_rm(rest[0])
        return False
    console.print(
        "[yellow]Usage: /mcp [list | show <name> | restart <name> | "
        "enable <name> | disable <name> | rm <name>][/]\n"
        "[dim]For add/edit/test use `openprogram mcp ...` in a "
        "separate shell.[/]"
    )
    return False


def _handle_connections(console, session_id: str) -> bool:
    if not session_id:
        console.print("[yellow]No active session.[/]")
        return False
    from openprogram.agent.management import session_aliases as _sa
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
