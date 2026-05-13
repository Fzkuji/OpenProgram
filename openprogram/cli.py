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
# (RequestsDependencyWarning, "[detect] codex OK", uvicorn boot logs)
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
        "worker", "update", "memory",
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
        help="LLM provider: claude-max-proxy, codex, gemini-cli, anthropic, openai, gemini. "
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
        help="Port for --web / `web` (default: stored UI pref, then 8109)")
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
        help="Port (default: stored UI pref, then 8109)")
    p_web.add_argument("--no-browser", action="store_true", help="Don't open browser")

    # ---- memory (persistent, machine-wide knowledge) ----------------------
    p_memory = sub.add_parser("memory",
        help="Inspect / manage persistent memory (journal + wiki + core).")
    memory_sub = p_memory.add_subparsers(dest="memory_verb", metavar="verb")
    memory_sub.add_parser("status",
        help="Show paths, counts, last sleep timestamp.")
    p_mr = memory_sub.add_parser("recall",
        help="Search wiki + recent journal and print raw snippets.")
    p_mr.add_argument("query", nargs="+")
    p_mr.add_argument("--days", type=int, default=30,
        help="Limit journal search to last N days (default 30).")
    p_ms = memory_sub.add_parser("show",
        help="Print a wiki page (slug or 'kind/slug').")
    p_ms.add_argument("path")
    p_med = memory_sub.add_parser("edit",
        help="Open a wiki page in $EDITOR.")
    p_med.add_argument("path")
    p_msleep = memory_sub.add_parser("sleep",
        help="Run a sleep sweep now (light → deep → REM).")
    p_msleep.add_argument("--phase", choices=["light", "deep", "rem"],
        help="Run only one phase instead of the full sweep.")
    memory_sub.add_parser("reflections",
        help="Print the latest entries from wiki/reflections.md.")
    p_mexp = memory_sub.add_parser("export",
        help="Tar+gzip the entire memory dir to a path.")
    p_mexp.add_argument("--out", default=None,
        help="Output path (default: ./openprogram-memory-<date>.tar.gz)")

    # ---- update (auto-update from upstream) -------------------------------
    p_update = sub.add_parser("update",
        help="Check for + apply updates from upstream. The worker also "
             "runs this in the background at startup; this command is "
             "the manual entry point.")
    p_update.add_argument("--check", action="store_true",
        help="Only check; don't apply any update.")
    p_update.add_argument("--force", action="store_true",
        help="Bypass the 6-hour throttle.")

    # ---- worker (persistent backend process) ------------------------------
    p_worker = sub.add_parser("worker",
        help="Manage the persistent worker process (webui + channels). "
             "All TUI / Web UI front-ends connect to this single process, "
             "so multiple front-ends and external channels share state.")
    worker_sub = p_worker.add_subparsers(dest="worker_verb", metavar="verb")
    worker_sub.add_parser("run",
        help="Run the worker in the foreground (blocking). Useful for "
             "debugging — Ctrl-C stops it.")
    worker_sub.add_parser("start",
        help="Spawn a detached worker in the background and return.")
    worker_sub.add_parser("stop",
        help="Stop the running worker (SIGTERM, escalates to SIGKILL).")
    worker_sub.add_parser("restart",
        help="Stop the running worker and start a fresh one.")
    worker_sub.add_parser("status",
        help="Show whether the worker is running, its PID, port, and uptime.")
    worker_sub.add_parser("install",
        help="Install as a system service (launchd on macOS, systemd --user "
             "on Linux). Auto-starts at login and restarts on crash.")
    worker_sub.add_parser("uninstall",
        help="Remove the system service.")

    # ---- channels ---------------------------------------------------------
    p_channels = sub.add_parser("channels",
        help="Run / inspect chat-channel bots (Telegram, Discord, Slack, WeChat)")
    channels_sub = p_channels.add_subparsers(dest="channels_verb", metavar="verb")
    channels_sub.add_parser("list", help="Show per-platform enable + config status")
    channels_sub.add_parser("setup",
        help="Interactive wizard — pick channel, log in (QR / token), "
             "bind to an agent. One command instead of "
             "`accounts add` + `accounts login` + `bindings add`. "
             "Channels run inside the persistent worker — start it with "
             "`openprogram worker start`.")
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
        help="LLM provider (claude-max-proxy, openai-codex, anthropic, ...)")
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
    p_config_sub.add_parser("search",
        help="Default web search backend (Tavily / Brave / Google PSE / …)")
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
            # Also auto-start the persistent worker since the user has
            # now explicitly asked for external routing.
            from openprogram.worker import current_worker_pid, spawn_detached
            _row, replaced = _a.attach(
                channel=args.channel, account_id=args.account,
                peer_kind=args.peer_kind, peer_id=args.peer,
                agent_id=owner, session_id=args.session_id,
            )
            print(f"Attached {args.channel}:{args.account}:"
                  f"{args.peer_kind}:{args.peer} → agent={owner}, "
                  f"session={args.session_id}")
            if replaced is not None:
                print(f"  (replaced previous binding "
                      f"→ session {replaced.get('session_id')})")
            if current_worker_pid() is None:
                print("Starting openprogram worker in the background...")
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

    if args.command == "memory":
        verb = getattr(args, "memory_verb", None)
        from openprogram.memory import store as _mstore
        from openprogram.memory import index as _midx
        if verb == "status":
            stats = _midx.stats()
            print(f"memory root:     {_mstore.root()}")
            print(f"wiki pages:      {stats['wiki_pages']}")
            print(f"short entries:   {stats['short_entries']}")
            print(f"last reindex:    {stats['last_reindex'] or '(never)'}")
            ls = _mstore.last_sleep_path()
            if ls.exists():
                print(f"last sleep:      {ls.read_text().strip()[:200]}")
            else:
                print("last sleep:      (never)")
            sys.exit(0)
        if verb == "recall":
            from openprogram.memory.builtin.recall import recall_for_prompt
            q = " ".join(args.query)
            text = recall_for_prompt(q, short_days=args.days)
            print(text or f"No memories matched {q!r}.")
            sys.exit(0)
        if verb == "show":
            from openprogram.memory import wiki as _w
            content = _w.read(args.path)
            if content is None:
                print(f"No wiki page matches {args.path!r}.")
                sys.exit(1)
            print(content)
            sys.exit(0)
        if verb == "edit":
            import os, subprocess
            from openprogram.memory import wiki as _w
            target = _w.find(args.path)
            if target is None:
                literal = _w.root() / (args.path if args.path.endswith(".md") else args.path + ".md")
                if not literal.exists():
                    print(f"No wiki page matches {args.path!r}.")
                    sys.exit(1)
                target = literal
            editor = os.environ.get("EDITOR", "vi")
            subprocess.call([editor, str(target)])
            _midx.reindex_all()
            sys.exit(0)
        if verb == "sleep":
            from openprogram.memory.sleep import run_sweep, run_phase
            from openprogram.memory.llm_bridge import build_default_llm
            llm = build_default_llm()
            phase = getattr(args, "phase", None)
            report = run_phase(phase, llm=llm) if phase else run_sweep(llm=llm)
            import json as _json
            print(_json.dumps(report, indent=2, ensure_ascii=False))
            sys.exit(0)
        if verb == "reflections":
            r = _mstore.wiki_reflections()
            if not r.exists():
                print("(no reflections yet)")
                sys.exit(0)
            text = r.read_text(encoding="utf-8")
            blocks = text.strip().split("\n## ")
            tail = blocks[-3:] if len(blocks) > 3 else blocks
            print("\n## ".join(tail))
            sys.exit(0)
        if verb == "export":
            import datetime as _dt
            import shutil as _sh
            import tarfile as _tar
            out = getattr(args, "out", None) or (
                f"./openprogram-memory-{_dt.date.today().isoformat()}.tar.gz"
            )
            with _tar.open(out, "w:gz") as t:
                t.add(str(_mstore.root()), arcname="memory")
            print(f"exported to {out}")
            sys.exit(0)
        p_memory.print_help()
        sys.exit(2)

    if args.command == "update":
        from openprogram.updater import (
            apply_update, check_for_update, detect_install_method, is_disabled,
        )
        if is_disabled() and not args.force:
            print("auto-update disabled by OPENPROGRAM_NO_AUTO_UPDATE.")
            print("Use `openprogram update --force` to override.")
            sys.exit(0)
        method = detect_install_method()
        info = check_for_update(force=args.force)
        if info is None:
            print(f"No update path for install method: {method.value}.")
            sys.exit(0)
        if not info.available:
            print(f"openprogram {info.current} ({method.value}): {info.summary}.")
            sys.exit(0)
        print(f"update available: {info.current} → {info.target} ({info.summary})")
        if args.check:
            sys.exit(0)
        ok, msg = apply_update(info)
        if ok:
            print(f"updated to {info.target}.")
            print("Restart the worker so the new code takes effect:")
            print("  openprogram worker restart")
            sys.exit(0)
        print(f"update failed: {msg}")
        sys.exit(1)

    if args.command == "worker":
        verb = getattr(args, "worker_verb", None)
        from openprogram import worker as _worker
        if verb == "run":
            sys.exit(_worker.run_foreground())
        if verb == "start":
            sys.exit(_worker.spawn_detached())
        if verb == "stop":
            sys.exit(_worker.stop_worker())
        if verb == "restart":
            sys.exit(_worker.restart_worker())
        if verb == "status":
            from openprogram.worker import services as _services
            rc = _worker.print_status()
            if _services.is_supported():
                print()
                _services.status()
            sys.exit(rc)
        if verb == "install":
            from openprogram.worker import services as _services
            sys.exit(_services.install())
        if verb == "uninstall":
            from openprogram.worker import services as _services
            sys.exit(_services.uninstall())
        p_worker.print_help()
        sys.exit(2)

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
        if verb == "setup":
            from openprogram.channels import setup as _ch_setup
            sys.exit(_ch_setup.run())
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
            "search":   _sw.run_search_section,
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


# ---------------------------------------------------------------------------
# Subcommand handlers — bodies live in openprogram/_cli_cmds/*.py. They are
# re-exported here under the names tests / openprogram.cli_chat /
# openprogram.cli_ink import directly off ``openprogram.cli``.
# ---------------------------------------------------------------------------

from openprogram._cli_cmds.programs import (  # noqa: E402,F401
    _get_runtime,
    _get_functions_dir,
    _cmd_configure,
    _cmd_list,
    _cmd_create,
    _cmd_create_app,
    _cmd_edit,
    _cmd_run,
    _cmd_create_skill,
)
from openprogram._cli_cmds.skills import (  # noqa: E402,F401
    _cmd_skills_list,
    _cmd_skills_doctor,
    _cmd_install_skills,
)
from openprogram._cli_cmds.browser import (  # noqa: E402,F401
    _python_pkg_present,
    _cmd_browser_install,
    _cmd_browser_status,
    _cmd_browser_refresh,
    _cmd_browser_reset,
    _cmd_browser_list,
    _cmd_browser_rm,
)
from openprogram._cli_cmds.sessions import (  # noqa: E402,F401
    _cmd_resume,
    _cmd_sessions,
)
from openprogram._cli_cmds.agents import (  # noqa: E402,F401
    _dispatch_agents_verb,
)
from openprogram._cli_cmds.channels import (  # noqa: E402,F401
    _dispatch_accounts_verb,
    _dispatch_bindings_verb,
    _login_account,
)
from openprogram._cli_cmds.web import _cmd_web  # noqa: E402,F401
from openprogram._cli_cmds.chat import (  # noqa: E402,F401
    _cmd_cli_chat,
    _cmd_deep_work,
)
from openprogram._cli_cmds.cron import _cmd_cron_worker  # noqa: E402,F401



if __name__ == "__main__":
    main()
