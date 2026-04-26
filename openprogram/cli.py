"""
OpenProgram CLI.

Run `openprogram` with no arguments to get an interactive mode chooser
(CLI chat or Web UI). Subcommands manage programs, skills, sessions,
providers, and config.

Examples:
    openprogram                           # interactive chooser
    openprogram --web                     # launch web UI
    openprogram --cli                     # launch terminal chat
    openprogram -p "prompt"               # one-shot prompt

    openprogram programs list
    openprogram programs new my_func "what it does"
    openprogram programs run my_func --arg key=value

    openprogram skills list
    openprogram skills install --target claude

    openprogram sessions list
    openprogram sessions resume <id> "answer"

    openprogram providers list
    openprogram providers login anthropic
"""

import argparse
import os
import sys
import json


# --- Pre-import TTY redirect ------------------------------------------------
# When the user is launching the Ink TUI (no subcommand or just `--resume`),
# we want a clean terminal: anything printed during openprogram package import
# (RequestsDependencyWarning, "[detect] claude-code OK", uvicorn boot logs)
# would otherwise show up above the TUI. Do the dup2 BEFORE pulling any
# openprogram modules so the noise lands in a log file. The original tty fds
# are exposed as module attributes so cli_ink can hand them to the Node child.

_TUI_TTY_OUT: int | None = None
_TUI_TTY_ERR: int | None = None


def _looks_like_tui_invocation(argv: list[str]) -> bool:
    """Return True if argv corresponds to launching the chat TUI.

    Bare `openprogram` and `openprogram --resume <id>` go to the TUI. Any
    subcommand (programs, skills, agents, sessions, channels, config,
    providers, web), and any one-shot flag (--print, -p, --no-tui), keep
    stdio plain.
    """
    bypass_words = {
        "agents", "sessions", "channels", "config", "programs", "skills",
        "providers", "web", "resume", "init", "doctor", "browser",
    }
    bypass_flags = {
        "--print", "-p", "--no-tui", "--web", "--help", "-h", "--version",
        "--print-prompt",
    }
    for arg in argv:
        if arg in bypass_flags:
            return False
        if arg.startswith("--print=") or arg.startswith("-p="):
            return False
        if arg in bypass_words:
            return False
    return True


def _maybe_redirect_for_tui() -> None:
    global _TUI_TTY_OUT, _TUI_TTY_ERR
    try:
        if not sys.stdout.isatty():
            return
    except Exception:
        return
    if not _looks_like_tui_invocation(sys.argv[1:]):
        return
    try:
        from pathlib import Path
        log_dir = Path.home() / ".openprogram" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "ink-startup.log"
        _TUI_TTY_OUT = os.dup(1)
        _TUI_TTY_ERR = os.dup(2)
        fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        os.dup2(fd, 1)
        os.dup2(fd, 2)
        os.close(fd)
    except Exception:
        # If anything goes wrong with the redirect we'd rather have a noisy
        # terminal than block the launch.
        _TUI_TTY_OUT = None
        _TUI_TTY_ERR = None


_maybe_redirect_for_tui()


def _add_provider_args(parser):
    """Add --provider and --model arguments to a subcommand parser."""
    parser.add_argument(
        "--provider", "-p",
        default=None,
        help="LLM provider: claude-code, codex, gemini-cli, anthropic, openai, gemini. "
             "Auto-detected if not specified.",
    )
    parser.add_argument(
        "--model", "-m",
        default=None,
        help="Model name override (e.g. sonnet, gpt-4o, claude-sonnet-4-6).",
    )


def main():
    parser = argparse.ArgumentParser(
        prog="openprogram",
        description="OpenProgram — build, run, and chat with agentic programs.",
    )
    # Top-level mode flags for a bare `openprogram` launch. If any of
    # these are set, we skip the interactive chooser and go straight in.
    parser.add_argument("--web", action="store_true",
        help="Launch the Web UI (browser)")
    parser.add_argument("--cli", action="store_true",
        help="Launch the terminal chat")
    parser.add_argument("--print", dest="print_prompt", metavar="PROMPT",
        help="One-shot prompt; send, print reply, exit")
    parser.add_argument("--port", type=int, default=None,
        help="Port for --web / `web` (default: stored UI pref, then 8765)")
    parser.add_argument("--no-browser", action="store_true",
        help="Don't auto-open browser with --web")
    parser.add_argument("--profile", default=None,
        help="State-dir profile name. Reroutes config/sessions/logs to "
             "~/.agentic-<name>/ so parallel workspaces don't share state. "
             "Env: OPENPROGRAM_PROFILE.")
    parser.add_argument("--resume", default=None, metavar="SESSION_ID",
        help="Resume a prior CLI chat session. Find ids via "
             "`openprogram sessions list` or the Web UI sidebar.")
    parser.add_argument("--no-tui", action="store_true",
        help="Fall back to the Rich REPL instead of the full-screen "
             "TUI. Useful when recording or in a terminal without "
             "alt-screen support.")

    sub = parser.add_subparsers(dest="command", help="Subcommand")

    # ---- programs ---------------------------------------------------------
    p_programs = sub.add_parser(
        "programs",
        help="Manage agentic programs (new, edit, run, list, app)",
    )
    programs_sub = p_programs.add_subparsers(dest="programs_verb", metavar="verb")
    p_p_new = programs_sub.add_parser("new", help="Create a new program from description")
    p_p_new.add_argument("name", help="Program name")
    p_p_new.add_argument("description", help="What the program should do")
    p_p_new.add_argument("--as-skill", action="store_true",
        help="Also create a SKILL.md for the program")
    _add_provider_args(p_p_new)
    p_p_edit = programs_sub.add_parser("edit", help="Edit an existing program")
    p_p_edit.add_argument("name", help="Program name to edit")
    p_p_edit.add_argument("--instruction", "-i", default=None, help="What to change")
    _add_provider_args(p_p_edit)
    p_p_run = programs_sub.add_parser("run", help="Run a program")
    p_p_run.add_argument("name", help="Program name to run")
    p_p_run.add_argument("--arg", "-a", action="append", default=[],
        help="Program arg as key=value (repeatable)")
    _add_provider_args(p_p_run)
    programs_sub.add_parser("list", help="List all saved programs")
    p_p_app = programs_sub.add_parser("app",
        help="Export a complete runnable app (runtime + functions + main)")
    p_p_app.add_argument("description", help="What the app should do")
    p_p_app.add_argument("--name", "-n", default="app", help="App name (default: app)")
    _add_provider_args(p_p_app)

    # ---- skills -----------------------------------------------------------
    p_skills = sub.add_parser("skills", help="Manage SKILL.md registry")
    skills_sub = p_skills.add_subparsers(dest="skills_verb", metavar="verb")
    p_sk_list = skills_sub.add_parser("list", help="List discovered skills")
    p_sk_list.add_argument("--dir", "-d", action="append", default=None,
        help="Override search dir (repeatable). Default: ~/.openprogram/skills + repo skills/")
    p_sk_list.add_argument("--json", action="store_true", help="Emit JSON")
    p_sk_doc = skills_sub.add_parser("doctor", help="Scan skill dirs for problems")
    p_sk_doc.add_argument("--dir", "-d", action="append", default=None)
    p_sk_inst = skills_sub.add_parser("install",
        help="Install skills into Claude Code / Gemini CLI")
    p_sk_inst.add_argument("--target", "-t", default=None,
        choices=["claude", "gemini"],
        help="Target CLI (default: auto-detect)")
    p_sk_new = skills_sub.add_parser("new",
        help="Create a SKILL.md for an existing program")
    p_sk_new.add_argument("name", help="Program name")
    _add_provider_args(p_sk_new)

    # ---- sessions ---------------------------------------------------------
    p_sessions = sub.add_parser("sessions",
        help="Manage chat sessions (list, attach a channel user to "
             "an existing session, ...)")
    sessions_sub = p_sessions.add_subparsers(dest="sessions_verb", metavar="verb")
    sessions_sub.add_parser("list", help="List every session across every agent")
    p_ss_res = sessions_sub.add_parser("resume", help="Answer a waiting session")
    p_ss_res.add_argument("session_id")
    p_ss_res.add_argument("answer")
    p_ss_att = sessions_sub.add_parser("attach",
        help="Route a channel user's messages into this session.")
    p_ss_att.add_argument("session_id",
        help="Existing session id (e.g. local_abc123def0)")
    p_ss_att.add_argument("--channel", required=True,
        choices=["wechat", "telegram", "discord", "slack"])
    p_ss_att.add_argument("--account", default="default",
        help="Account id (default: 'default')")
    p_ss_att.add_argument("--peer", required=True,
        help="External peer id — WeChat openid / Telegram chat_id / "
             "<channel_id>_<user_id> for Discord/Slack")
    p_ss_att.add_argument("--peer-kind", default="direct",
        choices=["direct", "group", "channel"])
    p_ss_det = sessions_sub.add_parser("detach",
        help="Remove the alias for a channel peer (peer returns to "
             "default scope-based routing)")
    p_ss_det.add_argument("--channel", required=True,
        choices=["wechat", "telegram", "discord", "slack"])
    p_ss_det.add_argument("--account", default="default")
    p_ss_det.add_argument("--peer", required=True)
    p_ss_det.add_argument("--peer-kind", default="direct",
        choices=["direct", "group", "channel"])
    sessions_sub.add_parser("aliases",
        help="List every session↔channel-peer alias")

    # ---- web --------------------------------------------------------------
    p_web = sub.add_parser("web", help="Start the Web UI")
    p_web.add_argument("--port", type=int, default=None,
        help="Port (default: stored UI pref, then 8765)")
    p_web.add_argument("--no-browser", action="store_true", help="Don't open browser")

    # ---- channels ---------------------------------------------------------
    p_channels = sub.add_parser("channels",
        help="Run / inspect chat-channel bots (Telegram, Discord, Slack, WeChat)")
    channels_sub = p_channels.add_subparsers(dest="channels_verb", metavar="verb")
    channels_sub.add_parser("list", help="Show per-platform enable + config status")
    p_chstart = channels_sub.add_parser("start",
        help="Start the channels worker in the background. Returns "
             "immediately; use `channels stop` to kill it. Add "
             "--foreground to keep it attached to your terminal instead "
             "(useful for debugging).")
    p_chstart.add_argument("--foreground", "--fg", action="store_true",
        help="Stay in the foreground (blocking) instead of detaching. "
             "Ctrl-C stops it.")
    # Back-compat: older docs + the inline prompt_spawn_if_configured_but_dead
    # path still pass --detach. Accept and ignore it.
    p_chstart.add_argument("--detach", action="store_true",
        help=argparse.SUPPRESS)
    channels_sub.add_parser("stop",
        help="Stop the background channels worker (SIGTERM via PID file).")
    channels_sub.add_parser("status",
        help="Show whether the channels worker is running, which PID, "
             "and when it started.")
    # ---- channels accounts --------------------------------------------
    p_chacct = channels_sub.add_parser("accounts",
        help="Manage channel bot accounts (WeChat, Telegram, etc.)")
    p_chacct_sub = p_chacct.add_subparsers(dest="accounts_verb",
                                            metavar="verb")
    p_chacct_sub.add_parser("list", help="List every channel account")
    p_chacct_add = p_chacct_sub.add_parser("add",
        help="Create a new channel account and prompt for credentials")
    p_chacct_add.add_argument("channel",
        choices=["wechat", "telegram", "discord", "slack"])
    p_chacct_add.add_argument("--id", default="default",
        help="Account id (default: 'default')")
    p_chacct_rm = p_chacct_sub.add_parser("rm",
        help="Delete a channel account (also drops its bindings)")
    p_chacct_rm.add_argument("channel",
        choices=["wechat", "telegram", "discord", "slack"])
    p_chacct_rm.add_argument("account_id")
    p_chacct_login = p_chacct_sub.add_parser("login",
        help="Re-run the login flow for an account (e.g. WeChat QR)")
    p_chacct_login.add_argument("channel",
        choices=["wechat", "telegram", "discord", "slack"])
    p_chacct_login.add_argument("--id", default="default",
        help="Account id (default: 'default')")

    # ---- channels bindings --------------------------------------------
    p_chb = channels_sub.add_parser("bindings",
        help="Route inbound channel messages to agents")
    p_chb_sub = p_chb.add_subparsers(dest="bindings_verb", metavar="verb")
    p_chb_sub.add_parser("list", help="Show every routing rule")
    p_chb_add = p_chb_sub.add_parser("add",
        help="Add a binding: inbound messages matching (channel, account, "
             "optional peer) go to the given agent")
    p_chb_add.add_argument("agent_id")
    p_chb_add.add_argument("--channel", required=True,
        choices=["wechat", "telegram", "discord", "slack"])
    p_chb_add.add_argument("--account", default=None,
        help="Account id (omit for channel-wide)")
    p_chb_add.add_argument("--peer", default=None,
        help="Specific peer id (user_id / chat_id) — omit for broad rule")
    p_chb_add.add_argument("--peer-kind", default="direct",
        choices=["direct", "group", "channel"])
    p_chb_rm = p_chb_sub.add_parser("rm",
        help="Remove a binding by its id (see `bindings list`)")
    p_chb_rm.add_argument("binding_id")

    # ---- browser ---------------------------------------------------------
    p_browser = sub.add_parser("browser",
        help="Install + maintain the browser tools. Lifecycle (open, "
             "login, attach) is handled automatically by the tools "
             "themselves — see /browser inside the chat.")
    p_browser_sub = p_browser.add_subparsers(dest="browser_verb", metavar="verb")
    p_br_install = p_browser_sub.add_parser("install",
        help="Install browser-tool dependencies (Playwright + Chromium, "
             "patchright/camoufox, agent-browser). Pick one target or 'all'.")
    p_br_install.add_argument("target", nargs="?", default="playwright",
        choices=["playwright", "patchright", "camoufox", "agent", "all"],
        help="What to install (default: playwright).")
    p_browser_sub.add_parser("status",
        help="Show what's installed, whether the sidecar Chrome is running, "
             "and how many saved logins exist.")
    p_browser_sub.add_parser("refresh",
        help="Re-copy your real Chrome profile to the sidecar (use after "
             "logging in to a new site in your main Chrome).")
    p_browser_sub.add_parser("reset",
        help="Full reset — kill sidecar Chrome, drop the sidecar profile + "
             "all saved logins + port file. Next open() re-bootstraps clean.")
    p_browser_sub.add_parser("list",
        help="Show every saved login under ~/.openprogram/browser-states/")
    p_br_rm = p_browser_sub.add_parser("rm",
        help="Delete a saved login by host or file name")
    p_br_rm.add_argument("name", help="Host or file name (e.g. app.gptzero.me)")

    # ---- agents ----------------------------------------------------------
    p_agents = sub.add_parser("agents",
        help="Manage agents (each agent is a named persona with its own "
             "model, skills, tools, and session store)")
    p_agents_sub = p_agents.add_subparsers(dest="agents_verb", metavar="verb")
    p_agents_sub.add_parser("list", help="List every agent")
    p_ag_add = p_agents_sub.add_parser("add",
        help="Create a new agent record")
    p_ag_add.add_argument("id", help="Agent id (e.g. main, family, work)")
    p_ag_add.add_argument("--name", default="",
        help="Human-readable name")
    p_ag_add.add_argument("--provider", default="",
        help="LLM provider (claude-code, openai-codex, anthropic, ...)")
    p_ag_add.add_argument("--model", default="",
        help="Model id within that provider")
    p_ag_add.add_argument("--effort", default="medium",
        choices=["low", "medium", "high", "xhigh"],
        help="Default reasoning effort")
    p_ag_add.add_argument("--default", action="store_true",
        help="Mark this agent as the default")
    p_ag_rm = p_agents_sub.add_parser("rm",
        help="Delete an agent and all its sessions")
    p_ag_rm.add_argument("id")
    p_ag_show = p_agents_sub.add_parser("show",
        help="Print one agent's full record")
    p_ag_show.add_argument("id")
    p_ag_def = p_agents_sub.add_parser("set-default",
        help="Mark an agent as the default")
    p_ag_def.add_argument("id")

    # ---- cron-worker ------------------------------------------------------
    p_cron = sub.add_parser("cron-worker",
        help="Foreground loop that fires scheduled entries from the `cron` tool")
    p_cron.add_argument("--once", action="store_true",
        help="Evaluate one tick and exit")
    p_cron.add_argument("--list", action="store_true",
        help="Show each entry with match status")

    # ---- providers --------------------------------------------------------
    p_providers = sub.add_parser("providers",
        help="Manage LLM providers (login, list, status, ...)")
    providers_sub = p_providers.add_subparsers(dest="providers_cmd", metavar="verb")
    from openprogram.auth.cli import build_parser as _build_provider_verbs
    _build_provider_verbs(providers_sub)

    # ---- setup (top-level, full first-run wizard) -------------------------
    sub.add_parser("setup",
        help="First-run setup (QuickStart or Advanced)")
    sub.add_parser("configure",
        help="Re-edit any config section through a menu loop")

    # ---- config -----------------------------------------------------------
    p_config = sub.add_parser("config",
        help="Configure OpenProgram — individual setup sections")
    p_config_sub = p_config.add_subparsers(dest="config_target", metavar="target")
    p_cfg_provider = p_config_sub.add_parser("provider",
        help="Interactive wizard to set up a provider (legacy name)")
    p_cfg_provider.add_argument("name", nargs="?", default=None,
        help="Provider id. If omitted, pick from a menu.")
    p_config_sub.add_parser("model",
        help="Pick the default chat model across enabled providers")
    p_config_sub.add_parser("tools",
        help="Enable / disable individual tools")
    p_config_sub.add_parser("agent",
        help="Set agent defaults (thinking effort, ...)")
    p_config_sub.add_parser("skills",
        help="Enable / disable individual skills (SKILL.md entries)")
    p_config_sub.add_parser("ui",
        help="Web UI preferences (port, auto-open browser)")
    p_config_sub.add_parser("memory",
        help="Pick the memory backend for the `memory` tool")
    p_config_sub.add_parser("profile",
        help="Active profile name (config-path isolation pending)")
    p_config_sub.add_parser("tts",
        help="Text-to-speech provider (runtime hookup pending)")
    p_config_sub.add_parser("channels",
        help="Chat-channel bots (Telegram/Discord/Slack — runtime pending)")
    p_config_sub.add_parser("backend",
        help="Terminal exec backend (local/docker/ssh — runtime pending)")

    args = parser.parse_args()

    # --profile must land in the env BEFORE any later code reads a path
    # (setup config, session dir, logs dir, ...). get_active_profile
    # checks the env each call so setting it here is enough.
    if args.profile:
        from openprogram.paths import set_active_profile
        set_active_profile(args.profile)

    # -------- No subcommand: bare `openprogram` drops into CLI chat --------
    # Hermes-style: no mode chooser, the banner + REPL is the default
    # experience. --web routes to the browser UI; --print runs one-shot.
    if args.command is None:
        if args.print_prompt:
            _cmd_cli_chat(oneshot=args.print_prompt, resume=args.resume,
                          tui=not args.no_tui)
            return
        if args.web:
            _cmd_web(args.port, False if args.no_browser else None)
            return
        _cmd_cli_chat(oneshot=None, resume=args.resume,
                      tui=not args.no_tui)
        return

    # -------- Subcommand dispatch --------
    if args.command == "programs":
        verb = getattr(args, "programs_verb", None)
        if verb == "list":
            _cmd_list()
        elif verb == "new":
            _cmd_create(args.description, args.name, args.as_skill,
                        args.provider, args.model)
        elif verb == "edit":
            _cmd_edit(args.name, args.instruction, args.provider, args.model)
        elif verb == "run":
            _cmd_run(args.name, args.arg, args.provider, args.model)
        elif verb == "app":
            _cmd_create_app(args.description, args.name, args.provider, args.model)
        else:
            p_programs.print_help()
        return

    if args.command == "skills":
        verb = getattr(args, "skills_verb", None)
        if verb == "list":
            sys.exit(_cmd_skills_list(args.dir, args.json))
        elif verb == "doctor":
            sys.exit(_cmd_skills_doctor(args.dir))
        elif verb == "install":
            _cmd_install_skills(args.target)
        elif verb == "new":
            _cmd_create_skill(args.name, args.provider, args.model)
        else:
            p_skills.print_help()
        return

    if args.command == "sessions":
        verb = getattr(args, "sessions_verb", None)
        if verb == "list":
            _cmd_sessions()
        elif verb == "resume":
            _cmd_resume(args.session_id, args.answer)
        elif verb == "attach":
            from openprogram.agents import session_aliases as _a
            from openprogram.webui import persistence as _persist
            owner = _persist.resolve_agent_for_conv(args.session_id)
            if owner is None:
                print(f"[error] no session {args.session_id!r} found "
                      f"under any agent.")
                sys.exit(1)
            # Also auto-start the channels worker since the user has
            # now explicitly asked for external routing.
            from openprogram.channels.worker import (
                current_worker_pid, spawn_detached,
            )
            _a.attach(
                channel=args.channel, account_id=args.account,
                peer_kind=args.peer_kind, peer_id=args.peer,
                agent_id=owner, session_id=args.session_id,
            )
            print(f"Attached {args.channel}:{args.account}:"
                  f"{args.peer_kind}:{args.peer} → agent={owner}, "
                  f"session={args.session_id}")
            if current_worker_pid() is None:
                print("Starting channels worker in the background...")
                spawn_detached()
        elif verb == "detach":
            from openprogram.agents import session_aliases as _a
            removed = _a.detach(
                channel=args.channel, account_id=args.account,
                peer_kind=args.peer_kind, peer_id=args.peer,
            )
            if removed:
                print(f"Detached {args.channel}:{args.account}:"
                      f"{args.peer_kind}:{args.peer}")
            else:
                print("No matching alias.")
        elif verb == "aliases":
            from openprogram.agents import session_aliases as _a
            rows = _a.list_all()
            if not rows:
                print("No session aliases. "
                      "Inbound channel messages fall back to "
                      "binding → session_scope routing.")
                return
            print(f"{'channel':10} {'account':12} {'peer':28} "
                  f"{'agent':12} session")
            for r in rows:
                peer = r["peer"]
                peer_str = f"{peer['kind']}:{peer['id']}"
                print(f"{r['channel']:10} {r['account_id']:12} "
                      f"{peer_str[:27]:28} {r['agent_id']:12} "
                      f"{r['session_id']}")
        else:
            p_sessions.print_help()
        return

    if args.command == "web":
        _cmd_web(args.port, False if args.no_browser else None)
        return

    if args.command == "channels":
        verb = getattr(args, "channels_verb", None)
        if verb == "list":
            from openprogram.channels import list_status
            rows = list_status()
            if not rows:
                print("No channel accounts configured. "
                      "Run `openprogram channels accounts add <channel>`.")
                return
            print(f"{'channel':10} {'account':14} {'enabled':8} "
                  f"{'configured':12} {'impl':6}")
            for r in rows:
                print(f"{r['platform']:10} {r['account_id']:14} "
                      f"{str(r['enabled']):8} {str(r['configured']):12} "
                      f"{str(r['implemented']):6}")
            return
        if verb == "start":
            if getattr(args, "foreground", False):
                from openprogram.channels.runner import run_all
                sys.exit(run_all())
            from openprogram.channels.worker import spawn_detached
            sys.exit(spawn_detached())
        if verb == "stop":
            from openprogram.channels.worker import stop_worker
            sys.exit(stop_worker())
        if verb == "status":
            from openprogram.channels.worker import print_status
            sys.exit(print_status())
        if verb == "accounts":
            _dispatch_accounts_verb(args, p_chacct)
            return
        if verb == "bindings":
            _dispatch_bindings_verb(args, p_chb)
            return
        p_channels.print_help()
        return

    if args.command == "browser":
        verb = getattr(args, "browser_verb", None)
        if verb == "install":
            sys.exit(_cmd_browser_install(getattr(args, "target", "playwright")))
        if verb == "status":
            sys.exit(_cmd_browser_status())
        if verb == "refresh":
            sys.exit(_cmd_browser_refresh())
        if verb == "reset":
            sys.exit(_cmd_browser_reset())
        if verb == "list":
            sys.exit(_cmd_browser_list())
        if verb == "rm":
            sys.exit(_cmd_browser_rm(args.name))
        p_browser.print_help()
        sys.exit(2)

    if args.command == "agents":
        _dispatch_agents_verb(args, p_agents)
        return

    if args.command == "cron-worker":
        _cmd_cron_worker(args.once, args.list)
        return

    if args.command == "providers":
        from openprogram.auth.cli import dispatch as _providers_dispatch
        if getattr(args, "providers_cmd", None) is None:
            args.providers_cmd = "list"
            args.profile = None
            args.json = False
            rc = _providers_dispatch(args)
            print(
                "\nMore commands:\n"
                "  openprogram providers setup     # interactive first-time wizard\n"
                "  openprogram providers doctor    # diagnose credentials\n"
                "  openprogram providers aliases   # show short-name table\n"
                "  openprogram providers login <prov>   # connect a provider\n"
            )
            sys.exit(rc)
        sys.exit(_providers_dispatch(args))

    if args.command == "setup":
        from openprogram.setup import run_full_setup
        sys.exit(run_full_setup())

    if args.command == "configure":
        from openprogram.setup import run_configure_menu
        sys.exit(run_configure_menu())

    if args.command == "config":
        from openprogram import setup as _sw
        target = args.config_target
        handlers = {
            "model":    _sw.run_model_section,
            "tools":    _sw.run_tools_section,
            "agent":    _sw.run_agent_section,
            "skills":   _sw.run_skills_section,
            "ui":       _sw.run_ui_section,
            "memory":   _sw.run_memory_section,
            "profile":  _sw.run_profile_section,
            "tts":      _sw.run_tts_section,
            "channels": _sw.run_channels_section,
            "backend":  _sw.run_backend_section,
        }
        if target == "provider":
            _cmd_configure(args.name)
        elif target in handlers:
            sys.exit(handlers[target]())
        else:
            p_config.print_help()
        return


def _cmd_cli_chat(oneshot: str | None = None,
                  resume: str | None = None,
                  tui: bool = True) -> None:
    """Terminal chat entry point."""
    from openprogram.cli_chat import run_cli_chat
    run_cli_chat(oneshot=oneshot, resume=resume, tui=tui)


def _cmd_resume(session_id, answer):
    """Resume a waiting follow-up session."""
    from openprogram.agentic_programming.session import Session
    session = Session(session_id)
    if not session.exists():
        print(json.dumps({"type": "error", "message": f"Session not found: {session_id}"}))
        sys.exit(1)
    meta = session.read_meta()
    if not meta:
        print(json.dumps({"type": "error", "message": f"Session metadata unreadable: {session_id}"}))
        sys.exit(1)
    session.send_answer(answer)
    print(json.dumps({"type": "ok", "message": f"Answer sent to session {session_id}"}))


def _cmd_sessions():
    """List active follow-up sessions."""
    from openprogram.agentic_programming.session import list_sessions
    sessions = list_sessions()
    if not sessions:
        print("No active sessions.")
        return
    print(f"Active sessions ({len(sessions)}):\n")
    for s in sessions:
        sid = s.get("session_id", "?")
        q = s.get("question", "?")
        status = s.get("status", "?")
        print(f"  {sid}  [{status}]  {q[:80]}")
    print(f"\nResume with: agentic resume <session_id> \"your answer\"")


def _cmd_skills_list(override_dirs, as_json: bool) -> int:
    """Print skills the runtime would discover, in override-precedence order."""
    from openprogram.agentic_programming.skills import default_skill_dirs, load_skills

    dirs = override_dirs or default_skill_dirs()
    skills = load_skills(dirs)

    if as_json:
        import json as _json
        print(_json.dumps([{
            "name": s.name,
            "description": s.description,
            "slug": s.slug,
            "file_path": s.file_path,
            "base_dir": s.base_dir,
        } for s in skills], indent=2))
        return 0

    print(f"Search dirs (override order):")
    for d in dirs:
        import os as _os
        exists = "✓" if _os.path.isdir(d) else "✗"
        print(f"  {exists}  {d}")
    if not skills:
        print("\n(no skills discovered)")
        return 0
    print(f"\nDiscovered {len(skills)} skill(s):\n")
    for s in skills:
        print(f"  {s.name}  ({s.slug})")
        print(f"    {s.description[:100]}")
        print(f"    {s.file_path}")
    return 0


def _cmd_skills_doctor(override_dirs) -> int:
    """Scan skill dirs for broken SKILL.md files and duplicate names.

    Exit code is non-zero when at least one issue is found so CI can
    consume it.
    """
    import os as _os
    from pathlib import Path as _Path

    from openprogram.agentic_programming.skills import (
        _load_one, _parse_front_matter, default_skill_dirs,
    )

    dirs = override_dirs or default_skill_dirs()
    issues: list[str] = []
    seen_names: dict[str, str] = {}

    for d in dirs:
        root = _Path(d)
        if not root.is_dir():
            # Missing dirs are warnings, not errors — the runtime tolerates them.
            print(f"[warn] skill dir does not exist: {d}")
            continue
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            skill_md = entry / "SKILL.md"
            if not skill_md.is_file():
                issues.append(f"{entry}: missing SKILL.md")
                continue
            try:
                text = skill_md.read_text(encoding="utf-8")
            except OSError as e:
                issues.append(f"{skill_md}: cannot read ({e})")
                continue
            fm = _parse_front_matter(text)
            if not fm:
                issues.append(f"{skill_md}: no YAML front matter (--- ... --- block)")
                continue
            name = (fm.get("name") or "").strip()
            description = (fm.get("description") or "").strip()
            if not name:
                issues.append(f"{skill_md}: front matter missing `name`")
            if not description:
                issues.append(f"{skill_md}: front matter missing `description`")
            if name and name in seen_names and seen_names[name] != str(skill_md):
                issues.append(
                    f"{skill_md}: duplicate name {name!r} "
                    f"(first seen at {seen_names[name]})"
                )
            if name:
                seen_names.setdefault(name, str(skill_md))

    if not issues:
        print(f"All skill dirs OK ({len(seen_names)} skill(s) discovered).")
        return 0
    print(f"Found {len(issues)} issue(s):")
    for issue in issues:
        print(f"  - {issue}")
    return 1


def _cmd_browser_install(target: str) -> int:
    """Install browser-tool dependencies. Pure shell-out — no agent involved."""
    import subprocess
    import shutil

    targets = ["playwright", "patchright", "camoufox", "agent"] if target == "all" else [target]
    rc = 0
    for t in targets:
        print(f"\n=== installing {t} ===")
        if t == "playwright":
            r1 = subprocess.run(["pip", "install", "playwright>=1.45.0"])
            r2 = subprocess.run(["playwright", "install", "chromium"]) if shutil.which("playwright") else r1
            if r1.returncode or (hasattr(r2, "returncode") and r2.returncode):
                rc = 1
        elif t == "patchright":
            r1 = subprocess.run(["pip", "install", "patchright>=1.40"])
            r2 = subprocess.run(["patchright", "install", "chromium"]) if shutil.which("patchright") else r1
            if r1.returncode or (hasattr(r2, "returncode") and r2.returncode):
                rc = 1
        elif t == "camoufox":
            r1 = subprocess.run(["pip", "install", "camoufox>=0.4.0"])
            r2 = subprocess.run(["camoufox", "fetch"]) if shutil.which("camoufox") else r1
            if r1.returncode or (hasattr(r2, "returncode") and r2.returncode):
                rc = 1
        elif t == "agent":
            if not shutil.which("npm"):
                print("npm not found — install Node.js first.")
                rc = 1
                continue
            r1 = subprocess.run(["npm", "install", "-g", "agent-browser"])
            r2 = subprocess.run(["agent-browser", "install"]) if shutil.which("agent-browser") else r1
            if r1.returncode or (hasattr(r2, "returncode") and r2.returncode):
                rc = 1
        else:
            print(f"Unknown target: {t}")
            rc = 1
    return rc


def _cmd_browser_status() -> int:
    """Show what's installed, sidecar state, saved login count."""
    import shutil
    import socket
    from pathlib import Path
    from openprogram.tools.browser._chrome_bootstrap import (
        port_file, sidecar_dir, is_port_listening,
    )

    print("Installations:")
    print(f"  playwright       {'✓' if _python_pkg_present('playwright') else '✗'}")
    print(f"  patchright       {'✓' if _python_pkg_present('patchright') else '✗'}")
    print(f"  camoufox         {'✓' if _python_pkg_present('camoufox') else '✗'}")
    print(f"  agent-browser    {'✓' if shutil.which('agent-browser') or shutil.which('npx') else '✗'}")

    print("\nSidecar Chrome:")
    sd = sidecar_dir()
    if sd.exists():
        size_mb = sum(f.stat().st_size for f in sd.rglob("*") if f.is_file()) / 1024 / 1024
        print(f"  profile dir: {sd} ({size_mb:.0f} MB)")
    else:
        print(f"  profile dir: (not yet created — runs lazily on first use)")
    pf = port_file()
    if pf.exists():
        try:
            port = int(pf.read_text().strip())
            alive = is_port_listening(port)
            print(f"  port file: {pf} → :{port} {'(LISTENING)' if alive else '(stale, not listening)'}")
        except (OSError, ValueError):
            print(f"  port file: {pf} (unreadable)")
    else:
        print("  port file: (none — sidecar not started)")

    print("\nSaved logins:")
    state_dir = Path.home() / ".openprogram" / "browser-states"
    if state_dir.exists():
        states = sorted(state_dir.glob("*.json"))
        if states:
            for p in states:
                kb = p.stat().st_size / 1024
                print(f"  {p.stem:<40} {kb:>6.1f} KB")
        else:
            print(f"  (none under {state_dir})")
    else:
        print(f"  (directory {state_dir} doesn't exist yet)")
    return 0


def _cmd_browser_refresh() -> int:
    """Re-copy the real Chrome profile to the sidecar."""
    import subprocess
    from pathlib import Path
    from openprogram.tools.browser._chrome_bootstrap import (
        sidecar_dir, port_file,
    )

    sd = sidecar_dir()
    if sd.exists():
        # Sidecar Chrome is probably running — kill it so the profile is unlocked.
        subprocess.run(["pkill", "-9", "-f", "openprogram/chrome-profile"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Drop the old sidecar profile so the next bootstrap re-copies fresh.
        import shutil
        shutil.rmtree(sd, ignore_errors=True)
        print(f"Removed old sidecar profile at {sd}")
    pf = port_file()
    if pf.exists():
        pf.unlink()
        print(f"Cleared port file {pf}")
    print("Next browser tool open() will re-copy your real Chrome profile.")
    return 0


def _cmd_browser_reset() -> int:
    """Full reset — sidecar + states + port file."""
    import subprocess
    import shutil
    from pathlib import Path

    subprocess.run(["pkill", "-9", "-f", "openprogram/chrome-profile"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    sd = Path.home() / ".openprogram" / "chrome-profile"
    if sd.exists():
        shutil.rmtree(sd, ignore_errors=True)
        print(f"Removed sidecar profile {sd}")
    states = Path.home() / ".openprogram" / "browser-states"
    if states.exists():
        shutil.rmtree(states, ignore_errors=True)
        print(f"Removed saved logins {states}")
    pf = Path.home() / ".openprogram" / "browser-cdp-port"
    if pf.exists():
        pf.unlink()
        print(f"Removed port file {pf}")
    print("Browser tool fully reset. Next open() bootstraps clean.")
    return 0


def _python_pkg_present(name: str) -> bool:
    """Cheap import probe for status checks."""
    import importlib.util
    return importlib.util.find_spec(name) is not None


def _cmd_browser_list() -> int:
    """List saved browser logins."""
    import os
    from pathlib import Path
    state_dir = Path.home() / ".openprogram" / "browser-states"
    if not state_dir.exists():
        print(f"(no saved logins — directory doesn't exist: {state_dir})")
        return 0
    entries = sorted(state_dir.glob("*.json"))
    if not entries:
        print(f"(no saved logins under {state_dir})")
        return 0
    print(f"Saved logins ({len(entries)}):")
    for p in entries:
        size_kb = p.stat().st_size / 1024
        print(f"  {p.stem:<40} {size_kb:>6.1f} KB")
    return 0


def _cmd_browser_rm(name: str) -> int:
    """Delete a saved login."""
    from pathlib import Path
    state_dir = Path.home() / ".openprogram" / "browser-states"
    candidates = [state_dir / f"{name}.json", state_dir / name]
    for p in candidates:
        if p.exists():
            p.unlink()
            print(f"Removed {p}")
            return 0
    print(f"No saved login found for {name!r} (looked in {state_dir})")
    return 1



def _cmd_install_skills(target=None):
    """Install skills to Claude Code or Gemini CLI."""
    import os
    import shutil
    import tempfile
    import subprocess

    # Determine target directories
    home = os.path.expanduser("~")
    targets = {}
    if shutil.which("claude"):
        targets["claude"] = os.path.join(home, ".claude", "skills")
    if shutil.which("gemini"):
        targets["gemini"] = os.path.join(home, ".gemini", "skills")

    if target:
        if target not in targets:
            print(f"Error: {target} CLI not found. Install it first.")
            sys.exit(1)
        targets = {target: targets[target]}

    if not targets:
        print("No CLI tools found. Install Claude Code or Gemini CLI first:")
        print("  npm i -g @anthropic-ai/claude-code && claude login")
        print("  npm i -g @google/gemini-cli")
        sys.exit(1)

    # Check if skills are available locally (dev install)
    pkg_dir = os.path.dirname(os.path.dirname(__file__))
    local_skills = os.path.join(pkg_dir, "skills")

    if os.path.isdir(local_skills):
        skills_dir = local_skills
    else:
        # Download from GitHub
        print("Downloading skills from GitHub...")
        tmp = tempfile.mkdtemp()
        try:
            subprocess.run(
                ["git", "clone", "--depth=1", "--filter=blob:none", "--sparse",
                 "https://github.com/Fzkuji/Agentic-Programming.git", tmp],
                check=True, capture_output=True,
            )
            subprocess.run(
                ["git", "sparse-checkout", "set", "skills"],
                cwd=tmp, check=True, capture_output=True,
            )
            skills_dir = os.path.join(tmp, "skills")
        except (subprocess.CalledProcessError, FileNotFoundError):
            print("Error: Failed to download skills. Install git or clone the repo manually:")
            print("  git clone https://github.com/Fzkuji/Agentic-Programming.git")
            print("  cp -r Agentic-Programming/skills/* ~/.claude/skills/")
            sys.exit(1)

    if not os.path.isdir(skills_dir):
        print("Error: skills/ directory not found.")
        sys.exit(1)

    # Copy skills
    for name, dest in targets.items():
        os.makedirs(dest, exist_ok=True)
        count = 0
        for item in os.listdir(skills_dir):
            src = os.path.join(skills_dir, item)
            dst = os.path.join(dest, item)
            if os.path.isdir(src):
                if os.path.exists(dst):
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
                count += 1
            elif os.path.isfile(src):
                shutil.copy2(src, dst)
                count += 1
        print(f"  Installed {count} skills to {dest} ({name})")

    print("\nDone! Your agent can now use agentic functions via natural language.")


def _get_runtime(provider=None, model=None):
    """Get a Runtime via auto-detection or explicit provider.

    Args:
        provider:  Provider name (e.g. "anthropic", "claude-code").
                   If None, auto-detects the best available.
        model:     Model name override.

    Returns:
        A ready-to-use Runtime instance.
    """
    from openprogram.legacy_providers import create_runtime
    return create_runtime(provider=provider, model=model)


def _get_functions_dir():
    import os
    return os.path.join(os.path.dirname(__file__), "programs", "functions", "third_party")


def _cmd_configure(provider: str | None):
    """Interactive provider-setup. Drives openprogram.legacy_providers.configuration."""
    from openprogram.legacy_providers import configuration

    catalog = configuration.list_providers()
    if not catalog:
        print("No provider configuration is currently registered.")
        return

    if provider is None:
        print("Available providers to configure:\n")
        for i, p in enumerate(catalog, 1):
            print(f"  {i}. {p['id']:15s}  {p['label']}")
            if p.get("description"):
                print(f"     {p['description']}")
        print()
        choice = input(f"Pick one [1-{len(catalog)}] (default 1): ").strip() or "1"
        try:
            provider = catalog[int(choice) - 1]["id"]
        except (ValueError, IndexError):
            print(f"Invalid choice: {choice}")
            return

    entry = configuration.get_provider(provider)
    if entry is None:
        print(f"Unknown provider: {provider}")
        print(f"Available: {', '.join(p['id'] for p in catalog)}")
        return

    print(f"\nConfiguring: {entry['label']}")
    if entry.get("description"):
        print(f"  {entry['description']}")
    print()

    ctx: dict = {}
    for step in entry["steps"]:
        while True:  # loop on the same step until it's ok or user aborts
            result = configuration.run_step(provider, step["id"], ctx)
            status = result["status"]
            if status == "ok":
                print(f"  [ok] {step['label']}: {result['message']}")
                break
            elif status == "needs_input":
                print(f"  [?]  {result['message']}")
                options = result.get("options") or []
                default = result.get("default")
                if options:
                    for i, opt in enumerate(options, 1):
                        marker = " (default)" if opt["value"] == default else ""
                        print(f"       {i}. {opt['value']:18s} {opt.get('desc', '')}{marker}")
                    pick = input(f"       Pick [1-{len(options)}]: ").strip()
                    if not pick and default is not None:
                        value = default
                    else:
                        try:
                            value = options[int(pick) - 1]["value"]
                        except (ValueError, IndexError):
                            print(f"       Invalid choice: {pick}")
                            continue
                else:
                    value = input(f"       > ").strip()
                    if not value and default is not None:
                        value = default
                ctx[result["input_key"]] = value
                continue  # re-run the step with input in ctx
            else:  # error
                print(f"  [x]  {step['label']}: {result['message']}")
                fix = result.get("fix")
                if fix:
                    print(f"       Fix with: {fix}")
                    retry = input("       Retry this step after running the fix? [Y/n]: ").strip().lower()
                    if retry in ("", "y", "yes"):
                        continue
                print("Aborted.")
                return

    print("\nAll steps complete. You can now run agentic commands without specifying --provider.")


def _cmd_list():
    """List all saved functions."""
    import os
    functions_dir = _get_functions_dir()
    if not os.path.exists(functions_dir):
        print("No functions created yet.")
        return

    files = [f[:-3] for f in os.listdir(functions_dir)
             if f.endswith(".py") and f != "__init__.py"]
    if not files:
        print("No functions created yet.")
        return

    print(f"Functions ({len(files)}):\n")
    for name in sorted(files):
        filepath = os.path.join(functions_dir, f"{name}.py")
        # Read first line of docstring
        with open(filepath) as f:
            content = f.read()
        desc = ""
        if '"""' in content:
            start = content.index('"""') + 3
            end = content.index('"""', start)
            desc = content[start:end].strip().split("\n")[0]
        print(f"  {name:20s}  {desc}")


def _cmd_create(description, name, as_skill, provider=None, model=None):
    """Create a new function."""
    from openprogram.programs.functions.meta import create
    runtime = _get_runtime(provider, model)

    print(f"Creating '{name}' (provider: {runtime.__class__.__name__})...")
    fn = create(description=description, runtime=runtime, name=name, as_skill=as_skill)
    print(f"  Saved to openprogram/programs/functions/third_party/{name}.py")
    if as_skill:
        print(f"  Skill created at skills/{name}/SKILL.md")


def _cmd_create_app(description, name, provider=None, model=None):
    """Create a complete runnable app."""
    from openprogram.programs.functions.meta import create_app
    runtime = _get_runtime(provider, model)

    print(f"Creating app '{name}' (provider: {runtime.__class__.__name__})...")
    filepath = create_app(description=description, runtime=runtime, name=name)
    print(f"  Saved to {filepath}")
    print(f"  Run with: python {filepath}")


def _cmd_edit(name, instruction, provider=None, model=None):
    """Edit an existing function."""
    import importlib
    from openprogram.programs.functions.meta import edit
    runtime = _get_runtime(provider, model)

    try:
        from openprogram.programs.functions import resolve_function_module
        mod = resolve_function_module(name)
        target_func = getattr(mod, name)
    except (ImportError, AttributeError):
        print(f"Error: function '{name}' not found in openprogram/programs/functions/third_party/")
        sys.exit(1)

    print(f"Editing '{name}' (provider: {runtime.__class__.__name__})...")
    result = edit(fn=target_func, runtime=runtime, instruction=instruction)
    if isinstance(result, dict) and result.get("type") == "follow_up":
        print(f"  LLM needs more info: {result['question']}")
        print(f"  Re-run with: openprogram edit {name} --instruction '<your answer>'")
    else:
        print(f"  Edited and saved to openprogram/programs/functions/third_party/{name}.py")


def _cmd_run(name, arg_list, provider=None, model=None):
    """Run an existing function."""
    import importlib

    try:
        from openprogram.programs.functions import resolve_function_module
        mod = resolve_function_module(name)
        loaded_func = getattr(mod, name)
    except (ImportError, AttributeError):
        print(f"Error: function '{name}' not found in openprogram/programs/functions/third_party/")
        sys.exit(1)

    # Check if it needs runtime
    import inspect
    unwrapped_func = loaded_func._fn if hasattr(loaded_func, '_fn') else loaded_func
    source = ""
    try:
        source = inspect.getsource(unwrapped_func)
    except (OSError, TypeError):
        pass

    if "runtime.exec" in source or "runtime" in str(getattr(loaded_func, '__globals__', {})):
        runtime = _get_runtime(provider, model)
        if hasattr(loaded_func, '_fn') and loaded_func._fn:
            loaded_func._fn.__globals__['runtime'] = runtime
        elif hasattr(loaded_func, '__globals__'):
            loaded_func.__globals__['runtime'] = runtime

    # Parse arguments
    kwargs = {}
    for a in arg_list:
        if "=" in a:
            k, v = a.split("=", 1)
            kwargs[k] = v
        else:
            print(f"Error: argument must be key=value, got '{a}'")
            sys.exit(1)

    result = loaded_func(**kwargs)
    print(result)


def _cmd_create_skill(name, provider=None, model=None):
    """Create a SKILL.md for a function."""
    import importlib
    import inspect
    from openprogram.programs.functions.meta import create_skill
    runtime = _get_runtime(provider, model)

    try:
        from openprogram.programs.functions import resolve_function_module
        mod = resolve_function_module(name)
        loaded_func = getattr(mod, name)
    except (ImportError, AttributeError):
        print(f"Error: function '{name}' not found in openprogram/programs/functions/third_party/")
        sys.exit(1)

    # Get source and description
    unwrapped_func = loaded_func._fn if hasattr(loaded_func, '_fn') else loaded_func
    try:
        code = inspect.getsource(unwrapped_func)
    except (OSError, TypeError):
        code = f"# Source not available for {name}"

    description = getattr(loaded_func, '__doc__', '') or name

    print(f"Creating skill for '{name}'...")
    path = create_skill(fn_name=name, description=description, code=code, runtime=runtime)
    print(f"  Skill created at {path}")


def _cmd_cron_worker(once: bool, show_list: bool) -> None:
    """Dispatch cron-worker subcommand."""
    from openprogram.tools.cron import list_next, run_forever, run_once

    if show_list:
        list_next()
        return
    if once:
        fired = run_once()
        print(f"Fired {fired} entr{'y' if fired == 1 else 'ies'}.")
        return
    run_forever()


def _cmd_web(port, open_browser):
    """Start the web UI.

    ``port=None`` / ``open_browser=None`` means "use the user's stored
    UI pref" (written by ``openprogram config ui``), falling back to
    the legacy defaults if none set.
    """
    try:
        from openprogram.webui import start_web
    except ImportError:
        print("Web UI dependencies not installed.")
        print("Install with: pip install openprogram[web]")
        sys.exit(1)

    if port is None or open_browser is None:
        try:
            from openprogram.setup import read_ui_prefs
            prefs = read_ui_prefs()
            if port is None:
                port = prefs["port"]
            if open_browser is None:
                open_browser = prefs["open_browser"]
        except Exception:
            pass
    if port is None:
        port = 8765
    if open_browser is None:
        open_browser = True

    thread = start_web(port=port, open_browser=open_browser)

    # Channels are opt-in (attach a conversation in the UI to start
    # exchanging with an external user). If a worker is already
    # running we mention it; otherwise stay quiet — the Web UI will
    # offer to start one the moment a user hits "Attach" on a
    # conversation.
    try:
        from openprogram.channels.worker import current_worker_pid
        pid = current_worker_pid()
        if pid:
            print(f"Channels worker running (PID {pid}).")
    except Exception:
        pass

    print("Press Ctrl+C to stop.")
    try:
        thread.join()
    except KeyboardInterrupt:
        print("\nStopping web UI.")


def _cmd_deep_work(task, level, provider, model,
                    max_steps, max_revisions, interactive):
    """Run deep work session."""
    from openprogram.programs.functions.buildin.deep_work import deep_work

    runtime = _get_runtime(provider, model)

    print(f"Deep work session")
    print(f"  Task: {task}")
    print(f"  Level: {level}")
    print(f"  Runtime: {runtime.__class__.__name__}")
    print()

    def on_update(result):
        rtype = result.get("type", "?")
        if rtype == "clarify":
            plan = result.get("plan_summary", "")
            if plan:
                print(f"  Plan: {plan[:200]}")
        elif rtype == "step":
            action = result.get("action", "?")
            print(f"  [step] {action}")
            if result.get("ready_for_review"):
                print(f"  → Submitting for evaluation...")
        elif rtype == "evaluation":
            score = result.get("score", "?")
            verdict = result.get("verdict", "?")
            passed = result.get("passed", False)
            icon = "PASS" if passed else "FAIL"
            print(f"  [eval] [{icon}] Score: {score}/10 — {verdict}")
            if not passed:
                feedback = result.get("feedback", "")
                if feedback:
                    print(f"  Feedback: {feedback[:200]}")
                print(f"  → Revising...")

    result = deep_work(
        task=task,
        level=level,
        runtime=runtime,
        max_steps=max_steps,
        max_revisions=max_revisions,
        callback=on_update,
        interactive=interactive,
    )

    print()
    if result.get("done"):
        evals = result.get("evaluations", [])
        final_score = evals[-1].get("score", "?") if evals else "?"
        print(f"Completed in {result['steps']} steps, {result.get('revisions', 0)} revision(s).")
        if evals:
            print(f"Final score: {final_score}/10")
    else:
        print(f"Stopped after {result['steps']} steps.")
        if result.get("error"):
            print(f"Reason: {result['error']}")


# ---------------------------------------------------------------------------
# agents / channels.accounts / channels.bindings CLI dispatchers
# ---------------------------------------------------------------------------

def _dispatch_agents_verb(args, parser) -> None:
    """Handle ``openprogram agents <verb>``."""
    from openprogram.agents import manager as _A
    verb = getattr(args, "agents_verb", None)
    if verb == "list":
        rows = _A.list_all()
        if not rows:
            print("No agents. Create one with `openprogram agents add main`.")
            return
        print(f"{'id':16} {'default':8} {'provider/model':40} effort")
        for a in rows:
            pm = f"{a.model.provider}/{a.model.id}" if a.model.provider else "-"
            print(f"{a.id:16} {str(a.default):8} {pm:40} "
                  f"{a.thinking_effort}")
        return
    if verb == "add":
        try:
            a = _A.create(
                args.id,
                name=args.name,
                provider=args.provider,
                model_id=args.model,
                thinking_effort=args.effort,
                make_default=getattr(args, "default", False),
            )
        except ValueError as e:
            print(f"[error] {e}")
            sys.exit(1)
        print(f"Created agent {a.id!r} "
              f"(provider={a.model.provider or '-'}, "
              f"model={a.model.id or '-'}, default={a.default})")
        return
    if verb == "rm":
        _A.delete(args.id)
        print(f"Agent {args.id!r} removed")
        return
    if verb == "show":
        a = _A.get(args.id)
        if a is None:
            print(f"No agent {args.id!r}")
            sys.exit(1)
        print(json.dumps(a.to_dict(), indent=2, sort_keys=True, default=str))
        # Also show which channels route into this agent — that's
        # how most users actually think about the record ("my main
        # agent is hooked up to WeChat and Telegram").
        try:
            from openprogram.channels import bindings as _b
            rows = _b.list_for_agent(a.id)
        except Exception:
            rows = []
        print()
        print("Channel bindings:")
        if not rows:
            print("  (none — inbound messages fall back to the default "
                  "agent if that's this one, otherwise ignored)")
            return
        for r in rows:
            m = r["match"]
            peer = m.get("peer") or {}
            peer_str = (f"  peer={peer.get('kind','?')}:{peer.get('id','?')}"
                        if peer else "")
            print(f"  · {r['id']}  channel={m.get('channel','*')}  "
                  f"account={m.get('account_id','*')}{peer_str}")
        return
    if verb == "set-default":
        _A.set_default(args.id)
        print(f"Default agent is now {args.id!r}")
        return
    parser.print_help()


def _dispatch_accounts_verb(args, parser) -> None:
    """Handle ``openprogram channels accounts <verb>``."""
    from openprogram.channels import accounts as _acc
    verb = getattr(args, "accounts_verb", None)
    if verb == "list":
        rows = _acc.list_all_accounts()
        if not rows:
            print("No channel accounts. "
                  "Run `openprogram channels accounts add <channel>`.")
            return
        print(f"{'channel':10} {'account':14} {'name':20} "
              f"{'enabled':8} configured")
        for a in rows:
            print(f"{a.channel:10} {a.account_id:14} {a.name[:19]:20} "
                  f"{str(_acc.is_enabled(a.channel, a.account_id)):8} "
                  f"{_acc.is_configured(a.channel, a.account_id)}")
        return
    if verb == "add":
        try:
            _acc.create(args.channel, args.id)
        except ValueError as e:
            print(f"[error] {e}")
            sys.exit(1)
        print(f"Created {args.channel}:{args.id}. "
              f"Now set credentials with "
              f"`openprogram channels accounts login {args.channel} "
              f"--id {args.id}`.")
        return
    if verb == "rm":
        from openprogram.channels import bindings as _b
        _b.remove_for_account(args.channel, args.account_id)
        _acc.delete(args.channel, args.account_id)
        print(f"Removed {args.channel}:{args.account_id} (and its bindings)")
        return
    if verb == "login":
        _login_account(args.channel, args.id)
        return
    parser.print_help()


def _login_account(channel: str, account_id: str) -> None:
    """Interactive credential entry for one account.

    Telegram/Discord/Slack take tokens (env paste); WeChat does the
    QR flow. Lives in cli.py rather than setup.py so `openprogram
    channels accounts login` works without going through the setup
    loop.
    """
    from openprogram.channels import accounts as _acc
    if _acc.get(channel, account_id) is None:
        _acc.create(channel, account_id)
    if channel == "wechat":
        from openprogram.channels.wechat import login_account
        login_account(account_id)
        return
    import getpass
    if channel == "telegram":
        tok = getpass.getpass("Telegram bot token: ")
        _acc.update_credentials("telegram", account_id, {"bot_token": tok})
    elif channel == "discord":
        tok = getpass.getpass("Discord bot token: ")
        _acc.update_credentials("discord", account_id, {"bot_token": tok})
    elif channel == "slack":
        bot = getpass.getpass("Slack bot token (xoxb-...): ")
        app = getpass.getpass("Slack app-level token (xapp-...): ")
        patch: dict = {}
        if bot:
            patch["bot_token"] = bot
        if app:
            patch["app_token"] = app
        if patch:
            _acc.update_credentials("slack", account_id, patch)
    else:
        print(f"Unknown channel {channel!r}")
        sys.exit(1)
    print(f"{channel}:{account_id} credentials saved")


def _dispatch_bindings_verb(args, parser) -> None:
    """Handle ``openprogram channels bindings <verb>``."""
    from openprogram.channels import bindings as _b
    verb = getattr(args, "bindings_verb", None)
    if verb == "list":
        rows = _b.list_all()
        if not rows:
            print("No bindings. Inbound messages route to the default "
                  "agent until you add one with `openprogram channels "
                  "bindings add <agent_id> --channel <channel>`.")
            return
        print(f"{'id':18} {'agent':14} {'channel':10} {'account':12} "
              f"peer")
        for r in rows:
            m = r["match"]
            peer = m.get("peer") or {}
            peer_str = (f"{peer.get('kind','?')}:{peer.get('id','?')}"
                        if peer else "-")
            print(f"{r['id']:18} {r['agent_id']:14} "
                  f"{m.get('channel','*'):10} "
                  f"{m.get('account_id','*'):12} {peer_str}")
        return
    if verb == "add":
        match: dict = {"channel": args.channel}
        if args.account:
            match["account_id"] = args.account
        if args.peer:
            match["peer"] = {"kind": args.peer_kind, "id": args.peer}
        entry = _b.add(args.agent_id, match)
        print(f"Binding {entry['id']}: {match} → {args.agent_id}")
        return
    if verb == "rm":
        removed = _b.remove(args.binding_id)
        if removed:
            print(f"Removed binding {args.binding_id}")
        else:
            print(f"No binding {args.binding_id!r}")
        return
    parser.print_help()


if __name__ == "__main__":
    main()
