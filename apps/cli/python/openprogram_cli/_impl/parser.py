"""Top-level OpenProgram CLI argument grammar.

This module contains declarations only. Command execution remains in
``openprogram.cli`` so documentation and tests can inspect the parser without
mixing its structure with dispatch logic.
"""

import argparse


def _add_provider_args(parser):
    """Add --provider and --model arguments to a subcommand parser."""
    parser.add_argument(
        "--provider", "-p",
        default=None,
        help="LLM provider: claude-code, openai-codex, gemini-cli, anthropic, openai, gemini. "
             "Auto-detected if not specified.",
    )
    parser.add_argument(
        "--model", "-m",
        default=None,
        help="Model name override (e.g. sonnet, gpt-4o, claude-sonnet-4-6).",
    )




def build_parser() -> argparse.ArgumentParser:
    """The full ``openprogram`` argument parser.

    Separate from ``main()`` so tools (docs reference generator, tests)
    can walk the command tree without running the CLI.
    """
    parser = argparse.ArgumentParser(
        prog="openprogram",
        description="OpenProgram — build, run, and chat with agentic programs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "commands:\n"
            "  chat / run\n"
            "    (none)          open the chat (terminal UI)\n"
            "    web             open the browser UI at http://localhost:18100\n"
            "    --print \"...\"   one-shot prompt; print reply and exit\n"
            "    --resume <id>   resume a prior chat session\n"
            "\n"
            "  background service\n"
            "    status          is the background service running? (PID, port, uptime)\n"
            "    stop            stop it (web UI stays up until you do)\n"
            "    restart         restart it (after changing code / config)\n"
            "    upgrade         update the code, then restart only if it works\n"
            "\n"
            "  setup & config\n"
            "    setup           first-run setup wizard\n"
            "    providers       manage LLM providers / keys (login, available, status)\n"
            "    config          view / change settings\n"
            "    recordings      record, replay, inspect, and prune provider calls\n"
            "    ports           show / set the web UI ports\n"
            "    mcp             manage MCP servers\n"
            "    browser         install / maintain the browser tools\n"
            "\n"
            "  content\n"
            "    agents          manage agents (model, skills, tools per persona)\n"
            "    sessions        manage chat sessions\n"
            "    programs        run / list agentic programs\n"
            "    workflows       validate authored Workflow packages\n"
            "    skills          manage the SKILL.md registry\n"
            "    plugins         manage installed plugins\n"
            "    channels        chat-channel bots (Telegram, Discord, Slack, WeChat)\n"
            "    memory          inspect / manage persistent memory\n"
            "    trash           list / restore recoverable local deletions\n"
            "    backup          snapshot / restore your profile state\n"
            "\n"
            "  maintenance\n"
            "    doctor          health checks\n"
            "    rescue          diagnose problems, print fix commands\n"
            "    logs            view log files\n"
            "    update          check for + apply updates\n"
            "    worker install  run as a login service (auto-start, crash-restart)\n"
            "    completion      emit a shell autocompletion script\n"
            "\n"
            "Run `openprogram <command> -h` for a command's own help "
            "(e.g. `openprogram providers -h`)."
        ),
    )
    # Top-level options for bare ``openprogram`` (chat mode). All other
    # modes are subcommands — see ``openprogram web``, ``openprogram
    # programs``, etc. The ``--web`` / ``--cli`` / ``--tui`` / ``--no-tui``
    # flags from earlier versions are gone; use the equivalent verb
    # (``openprogram web``) or just bare ``openprogram`` (chat).
    parser.add_argument("--print", dest="print_prompt", metavar="PROMPT",
        help="One-shot prompt; send, print reply, exit")
    from importlib.metadata import PackageNotFoundError, version
    try:
        package_version = version("openprogram")
    except PackageNotFoundError:
        package_version = "unknown"
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {package_version}",
    )
    parser.add_argument("--json-schema", dest="json_schema", metavar="PATH",
        help="Require JSON Schema output for a one-shot --print call; '-' reads stdin")
    parser.add_argument("--profile", default=None,
        help="State-dir profile name. Reroutes config/sessions/logs to "
             "~/.openprogram-<name>/ so parallel workspaces don't share state. "
             "Env: OPENPROGRAM_PROFILE.")
    parser.add_argument("--resume", default=None, metavar="SESSION_ID",
        help="Resume a prior CLI chat session. Find ids via "
             "`openprogram sessions list` or the Web UI sidebar.")

    # help=SUPPRESS hides argparse's default flat, add-order subcommand dump
    # (rescue/logs/completion first, daily commands scattered mid-list). The
    # grouped, curated list in ``epilog`` replaces it. Each sub-parser keeps
    # its own ``help=`` so ``openprogram <verb> -h`` still works.
    sub = parser.add_subparsers(dest="command", metavar="<command>",
                                help=argparse.SUPPRESS)

    # ---- rescue (crestodian-style first-aid diagnostic) -------------------
    sub.add_parser(
        "rescue",
        help="Diagnose common openprogram problems and print fix commands "
             "(deterministic — works when LLM/agent path is broken)",
    )

    # ---- logs (structured log viewer) -------------------------------------
    p_logs = sub.add_parser(
        "logs",
        help="Inspect worker / runtime / ink-startup log files",
    )
    logs_sub = p_logs.add_subparsers(dest="logs_verb", metavar="verb")
    p_l_list = logs_sub.add_parser("list", help="Show all log files (size, age)")
    p_l_path = logs_sub.add_parser("path", help="Print absolute path to a log")
    p_l_path.add_argument("name", nargs="?", default=None,
        help="Log name (worker / runtime / ink). Default: worker.")
    p_l_tail = logs_sub.add_parser("tail", help="Print last N lines (optionally follow)")
    p_l_tail.add_argument("name", nargs="?", default=None,
        help="Log name (worker / runtime / ink). Default: worker.")
    p_l_tail.add_argument("-n", "--lines", type=int, default=50,
        help="Number of trailing lines to print (default 50)")
    p_l_tail.add_argument("-f", "--follow", action="store_true",
        help="Keep streaming new appends until Ctrl-C")

    # ---- completion (shell autocomplete) ----------------------------------
    p_completion = sub.add_parser(
        "completion",
        help="Emit shell autocompletion script (bash / zsh / powershell)",
    )
    p_completion.add_argument(
        "shell",
        choices=["bash", "zsh", "powershell", "pwsh"],
        help="Target shell — pipe stdout into your shell rc or eval it.",
    )

    # ---- tui (alias: chat) — explicit verb for the default chat mode -----
    # Bare ``openprogram`` already launches the terminal UI; this verb
    # lets users write ``openprogram tui`` for clarity and parity with
    # other verbs (``openprogram web``, ``openprogram programs``, etc).
    # ``chat`` is accepted as an alias because it reads more naturally
    # for newcomers. Both ``--print`` and ``--resume`` are re-declared
    # on the subparser so they work after the verb
    # (``openprogram tui --print "hi"``) the same way they work at top
    # level (``openprogram --print "hi"``).
    p_tui = sub.add_parser(
        "tui",
        aliases=["chat"],
        help="Launch the terminal UI (Ink on macOS/Linux, Rich on "
             "Windows). Same as running `openprogram` with no verb.",
    )
    p_tui.add_argument("--print", dest="print_prompt", metavar="PROMPT",
        help="One-shot prompt; send, print reply, exit")
    p_tui.add_argument("--json-schema", dest="json_schema", metavar="PATH",
        help="Require JSON Schema output for a one-shot --print call; '-' reads stdin")
    p_tui.add_argument("--resume", default=None, metavar="SESSION_ID",
        help="Resume a prior CLI chat session.")

    # ---- programs ---------------------------------------------------------
    # The agent authors Programs directly with file tools and the documented API.
    # Only run / list remain as CLI operations.
    p_programs = sub.add_parser(
        "programs",
        help="Manage agentic programs (run, list)",
    )
    programs_sub = p_programs.add_subparsers(dest="programs_verb", metavar="verb")
    p_p_run = programs_sub.add_parser("run", help="Run a program")
    p_p_run.add_argument("name", help="Program name to run")
    p_p_run.add_argument("--arg", "-a", action="append", default=[],
        help="Program arg as key=value (repeatable)")
    _add_provider_args(p_p_run)
    programs_sub.add_parser("list", help="List all saved programs")
    # Optional first-party programs (gui / research / wiki agents) live in
    # their own repos; third-party harnesses install the same way by git URL.
    programs_sub.add_parser(
        "available",
        help="List installable programs + installed third-party harnesses")
    p_p_inst = programs_sub.add_parser(
        "install",
        help="Install a program (gui/research/wiki/all) or any third-party "
             "harness by git URL / owner/repo")
    p_p_inst.add_argument(
        "name",
        help="gui | research | wiki | all — or a git URL / owner/repo "
             "for a third-party harness")
    p_p_inst.add_argument("--upgrade", "-U", action="store_true",
        help="Reinstall/upgrade even if already present")
    p_p_un = programs_sub.add_parser(
        "uninstall",
        help="Uninstall a program (gui/research/wiki/all) or a third-party "
             "harness by its clone-dir name")
    p_p_un.add_argument("name", help="Program or harness dir name to uninstall")

    # ---- workflows --------------------------------------------------------
    p_workflows = sub.add_parser(
        "workflows",
        help="Author and validate reusable Workflow packages",
    )
    workflows_sub = p_workflows.add_subparsers(
        dest="workflows_verb", metavar="verb",
    )
    p_w_validate = workflows_sub.add_parser(
        "validate",
        help="Statically validate a Workflow package without executing it",
    )
    p_w_validate.add_argument(
        "directory",
        help="Workflow project directory containing pyproject.toml",
    )
    p_w_validate.add_argument(
        "--json",
        action="store_true",
        help="Emit a stable JSON report",
    )

    # ---- skills -----------------------------------------------------------
    p_skills = sub.add_parser("skills", help="Manage SKILL.md registry")
    skills_sub = p_skills.add_subparsers(dest="skills_verb", metavar="verb")
    p_sk_list = skills_sub.add_parser("list", help="List discovered skills")
    p_sk_list.add_argument("--dir", "-d", action="append", default=None,
        help="Override search dir (repeatable). Default: ~/.openprogram/skills + repo skills/")
    p_sk_list.add_argument("--json", action="store_true", help="Emit JSON")
    p_sk_doc = skills_sub.add_parser("doctor", help="Scan skill dirs for problems")
    p_sk_doc.add_argument("--dir", "-d", action="append", default=None, help="Skill directory to scan (repeatable; default: standard dirs)")
    p_sk_inst = skills_sub.add_parser("install",
        help="Install a skill from ClawHub or a discovery source")
    p_sk_inst.add_argument("spec", nargs="?", default=None,
        help="Skill slug (default source: ClawHub). Or 'clawhub:<slug>' / 'github:owner/repo' prefix form.")
    p_sk_inst.add_argument("--source", "-s", default=None,
        help="Discovery source URL (clawhub://, https://github.com/..., or JSON index)")
    p_sk_inst.add_argument("--target", "-t", default=None,
        choices=["claude", "gemini"],
        help="(Legacy) install local skills/ dir into Claude Code / Gemini CLI")

    p_sk_search = skills_sub.add_parser("search",
        help="Search for skills in a discovery source (default: ClawHub)")
    p_sk_search.add_argument("query", help="Query string")
    p_sk_search.add_argument("--source", "-s", default=None, help="Limit the search to one skill source/registry")
    p_sk_search.add_argument("--limit", "-n", type=int, default=20, help="Maximum results to show (default: 20)")

    p_sk_update = skills_sub.add_parser("update",
        help="Re-pull outdated skills (compare local SKILL.md hash against upstream)")
    p_sk_update.add_argument("name", nargs="?",
        help="Skill name to update (omit when --all is set)")
    p_sk_update.add_argument("--all", action="store_true",
        help="Update every outdated skill across all registered sources")

    p_sk_remove = skills_sub.add_parser("remove",
        help="Delete an installed skill (project/user/remote-cache only)")
    p_sk_remove.add_argument("name", help="Skill name")

    # ---- plugins ----------------------------------------------------------
    p_plugins = sub.add_parser("plugins", help="Manage installed plugins")
    plugins_sub = p_plugins.add_subparsers(dest="plugins_verb", metavar="verb")
    p_pl_list = plugins_sub.add_parser("list", help="List installed plugins")
    p_pl_list.add_argument("--json", action="store_true", help="Emit JSON")
    p_pl_srch = plugins_sub.add_parser("search",
        help="Search configured marketplaces for plugins matching <query>")
    p_pl_srch.add_argument("query", help="Search text to match plugin names/descriptions")
    p_pl_inst = plugins_sub.add_parser("install",
        help="Install a plugin from pip / npm / git / path")
    p_pl_inst.add_argument("source", choices=["pip", "npm", "git", "path"], help="Where to install the plugin from")
    p_pl_inst.add_argument("spec", help="Package name / URL / absolute path")
    p_pl_inst.add_argument("--ref", help="Git ref (branch/tag/sha) for source=git")
    p_pl_un = plugins_sub.add_parser("uninstall", help="Remove an installed plugin")
    p_pl_un.add_argument("name", help="Plugin name to uninstall")
    p_pl_up = plugins_sub.add_parser("update",
        help="Re-install (upgrade) plugins from pip/npm")
    p_pl_up.add_argument("name", nargs="?", help="Plugin name (omit when --all)")
    p_pl_up.add_argument("--all", action="store_true", help="Update every installed plugin")
    p_pl_en = plugins_sub.add_parser("enable", help="Enable an installed plugin")
    p_pl_en.add_argument("name", help="Plugin name to enable")
    p_pl_dis = plugins_sub.add_parser("disable", help="Disable a loaded plugin")
    p_pl_dis.add_argument("name", help="Plugin name to disable")

    # ---- doctor -----------------------------------------------------------
    p_doctor = sub.add_parser("doctor",
        help="Run sanity checks: python, node, skills, plugins, providers, mcp, cache, worker")
    p_doctor.add_argument("--json", action="store_true", help="Emit JSON")
    p_doctor.add_argument(
        "topic", nargs="?", choices=["credentials"],
        help="credentials: report disabled credential filesystem checks")
    p_doctor.add_argument(
        "--repair", action="store_true",
        help="Accepted for compatibility; credential filesystem checks stay disabled")

    # ---- diagnostics ------------------------------------------------------
    p_diagnostics = sub.add_parser(
        "diagnostics",
        help="Build a redacted support bundle (version, config, logs, probes) as a zip",
    )
    p_diagnostics.add_argument(
        "--output", metavar="PATH",
        help="Write the zip here (default: ./openprogram-diagnostics-<date>.zip)",
    )

    p_acp = sub.add_parser("acp",
        help="Serve the Agent Client Protocol on stdio, for editors like Zed")
    p_acp.add_argument("--agent", default="main",
                       help="Agent id to run sessions as (default: main)")
    p_acp.add_argument("--permission", default="ask",
                       choices=["ask", "acceptEdits", "plan", "auto", "bypass"],
                       help="Permission mode for tool calls (default: ask)")

    # ---- recoverable local deletions -------------------------------------
    p_trash = sub.add_parser(
        "trash",
        help="List or restore local deletions captured during agent turns",
    )
    trash_sub = p_trash.add_subparsers(dest="trash_verb", metavar="verb")
    trash_sub.add_parser("list", help="List recorded deletions and their status")
    p_trash_restore = trash_sub.add_parser(
        "restore", help="Restore one recorded deletion without overwriting"
    )
    p_trash_restore.add_argument("entry_id", help="Deletion id from `trash list`")

    # ---- backup -----------------------------------------------------------
    p_backup = sub.add_parser(
        "backup",
        help="Snapshot / restore the profile state dir (memory, sessions, "
             "config, bindings)",
    )
    backup_sub = p_backup.add_subparsers(dest="backup_verb", metavar="verb")
    p_bk_create = backup_sub.add_parser(
        "create", help="Write a tar.gz snapshot into <state>/backups/")
    p_bk_create.add_argument(
        "--include-credentials", action="store_true",
        help="Also archive auth/ and mcp_tokens/ — plaintext secrets, "
             "off by default")
    backup_sub.add_parser("list", help="List existing backups with size + contents")
    p_bk_restore = backup_sub.add_parser(
        "restore", help="Restore a backup over the current state dir")
    p_bk_restore.add_argument(
        "name", help="Backup filename from `backup list`, or a path")
    p_bk_restore.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be overwritten, change nothing")
    p_bk_restore.add_argument(
        "-y", "--yes", action="store_true", help="Skip the confirmation prompt")
    p_bk_prune = backup_sub.add_parser(
        "prune", help="Delete all but the newest N backups")
    p_bk_prune.add_argument(
        "--keep", type=int, default=5,
        help="Number of newest backups to keep (default: 5)")

    # ---- sessions ---------------------------------------------------------
    p_sessions = sub.add_parser("sessions",
        help="Manage chat sessions (list, attach a channel user to "
             "an existing session, ...)")
    sessions_sub = p_sessions.add_subparsers(dest="sessions_verb", metavar="verb")
    p_ss_list = sessions_sub.add_parser("list",
        help="List every session across every agent")
    p_ss_list.add_argument("--chat", action="store_true",
        help="List chat sessions from the session store instead of "
             "waiting follow-up sessions")
    p_ss_list.add_argument("--archived", action="store_true",
        help="With --chat: list archived chat sessions instead of active ones")
    p_ss_list.add_argument("--all", action="store_true", dest="all_scope",
        help="With --chat: list archived and active chat sessions together")
    p_ss_arc = sessions_sub.add_parser("archive",
        help="Hide a chat session from the default list (reversible, "
             "deletes nothing)")
    p_ss_arc.add_argument("session_id", help="Chat session id to archive")
    p_ss_unarc = sessions_sub.add_parser("unarchive",
        help="Return an archived chat session to the default list")
    p_ss_unarc.add_argument("session_id", help="Chat session id to unarchive")
    p_ss_res = sessions_sub.add_parser("resume", help="Answer a waiting session")
    p_ss_res.add_argument("session_id", help="Session id of the waiting session to answer")
    p_ss_res.add_argument("answer", help="Text to send back as the user's reply")
    p_ss_att = sessions_sub.add_parser("attach",
        help="Route a channel user's messages into this session.")
    p_ss_att.add_argument("session_id",
        help="Existing session id (e.g. local_abc123def0)")
    p_ss_att.add_argument("--channel", required=True, help="Channel id (e.g. discord, slack, wechat)",
        choices=["wechat", "telegram", "discord", "slack"])
    p_ss_att.add_argument("--account", default="default",
        help="Account id (default: 'default')")
    p_ss_att.add_argument("--peer", required=True,
        help="External peer id — WeChat openid / Telegram chat_id / "
             "<channel_id>_<user_id> for Discord/Slack")
    p_ss_att.add_argument("--peer-kind", default="direct", help="Peer kind: direct | group (default: direct)",
        choices=["direct", "group", "channel"])
    p_ss_det = sessions_sub.add_parser("detach",
        help="Remove the alias for a channel peer (peer returns to "
             "default scope-based routing)")
    p_ss_det.add_argument("--channel", required=True, help="Channel id the binding is on",
        choices=["wechat", "telegram", "discord", "slack"])
    p_ss_det.add_argument("--account", default="default", help="Channel account id (default: default)")
    p_ss_det.add_argument("--peer", required=True, help="Peer id (user/chat) to detach")
    p_ss_det.add_argument("--peer-kind", default="direct", help="Peer kind: direct | group (default: direct)",
        choices=["direct", "group", "channel"])
    sessions_sub.add_parser("aliases",
        help="List every session↔channel-peer alias")
    p_ss_exp = sessions_sub.add_parser("export",
        help="Export a session as a shareable Markdown or HTML file")
    p_ss_exp.add_argument("session_id", help="Session id to export")
    p_ss_exp.add_argument("--format", dest="export_format", default="md",
        choices=["md", "html"],
        help="Output format: md (default) or html (single self-contained file)")
    p_ss_exp.add_argument("--output", default=None,
        help="Write here instead of ./<session-id>.<format>")

    # ---- execution --------------------------------------------------------
    p_execution = sub.add_parser(
        "execution",
        help="Inspect or cancel a single execution",
    )
    execution_sub = p_execution.add_subparsers(
        dest="execution_verb", metavar="verb",
    )
    p_execution_cancel = execution_sub.add_parser(
        "cancel", help="Cancel one execution by id",
    )
    p_execution_cancel.add_argument(
        "execution_id", help="Execution id to cancel",
    )

    # ---- jobs -------------------------------------------------------------
    p_jobs = sub.add_parser(
        "jobs", help="Inspect canonical resource state for background jobs",
    )
    jobs_sub = p_jobs.add_subparsers(dest="jobs_verb", metavar="verb")
    p_jobs_list = jobs_sub.add_parser("list", help="List job resource DTOs")
    p_jobs_list.add_argument(
        "--session", dest="session_id", default=None,
        help="Restrict jobs to one session id",
    )
    p_jobs_list.add_argument("--json", action="store_true", help="Emit JSON")
    p_jobs_get = jobs_sub.add_parser("get", help="Get one job resource DTO")
    p_jobs_get.add_argument("job_id", help="Job id")
    p_jobs_get.add_argument("--json", action="store_true", help="Emit JSON")

    # ---- subagent ----------------------------------------------------------
    # Subagent spawn / merge ops. See ``openprogram/agent/sub_agent_run.py``
    # and ``openprogram/agent/_merge.py`` for the model. These commands run
    # against the in-process SessionStore singleton — no WS, no webui.
    p_subagent = sub.add_parser("subagent",
        help="Spawn, inspect, cancel, or merge subagent sessions.")
    subagent_sub = p_subagent.add_subparsers(
        dest="subagent_verb", metavar="verb",
    )

    p_sa_spawn = subagent_sub.add_parser("spawn",
        help="Spawn an agent in the given session as a new branch.")
    p_sa_spawn.add_argument("--session", required=True,
        help="Session id to spawn into (the new branch / root lives here)")
    p_sa_spawn.add_argument("--prompt", required=True,
        help="Prompt the spawned agent receives as its only user turn")
    p_sa_spawn.add_argument("--parent-msg", default=None,
        help="Specific node id to fork off in inherit mode "
             "(defaults to the session's current HEAD)")
    p_sa_spawn.add_argument("--label", default=None,
        help="1-3 word label used as the branch name")
    p_sa_spawn.add_argument("--agent", default="main",
        help="Agent profile id to run the spawn under (default: main)")
    p_sa_spawn.add_argument("--context", default="inherit",
        choices=["inherit", "clean"],
        help="inherit (default): forks off the parent turn, inheriting "
             "the conversation chain. clean: new root in the same "
             "session, the agent sees only the prompt.")
    p_sa_spawn.add_argument("--clean", action="store_true",
        help="Shortcut for --context clean")
    p_sa_spawn.add_argument("--no-json", action="store_true",
        help="Print human-readable summary instead of JSON")

    p_sa_merge = subagent_sub.add_parser("merge",
        help="Merge N subagent sessions into the target with a new turn.")
    p_sa_merge.add_argument("--target", required=True,
        help="Target session id (gets the merge reply + multi-parent commit)")
    p_sa_merge.add_argument("--branch", action="append", default=[],
        metavar="SID", required=True,
        help="Subagent session id to include in the merge "
             "(repeat for multiple)")
    p_sa_merge.add_argument("--message", default="",
        help="Merge instruction (the merge agent reads this alongside "
             "each branch's final text)")
    p_sa_merge.add_argument("--agent", default="main",
        help="Agent profile to run the merge under (default: main)")
    p_sa_merge.add_argument("--base", type=int, default=None,
        metavar="N",
        help="0-based index into --branch list. Marks that branch as the "
             "merge BASE — the reply is written as a continuation of "
             "it, with the others as supplemental context "
             "(attach-style merge).")
    p_sa_merge.add_argument("--no-json", action="store_true",
        help="Print human-readable summary instead of JSON")

    p_sa_list = subagent_sub.add_parser("list",
        help="List resource views for jobs in a session.")
    p_sa_list.add_argument("--session", required=True,
        help="Session id whose jobs should be listed")
    p_sa_list.add_argument("--json", action="store_true",
        help="Print the canonical job resource views as JSON")

    p_sa_show = subagent_sub.add_parser("show",
        help="Show one job's resource view.")
    p_sa_show.add_argument("job_id", help="Job id to inspect")
    p_sa_show.add_argument("--json", action="store_true",
        help="Print the canonical job resource view as JSON")

    p_sa_cancel = subagent_sub.add_parser("cancel",
        help="Cancel one job and show its updated resource view.")
    p_sa_cancel.add_argument("job_id", help="Job id to cancel")
    p_sa_cancel.add_argument("--json", action="store_true",
        help="Print the canonical job resource view as JSON")

    # ---- web --------------------------------------------------------------
    p_web = sub.add_parser("web", help="Start the Web UI")
    p_web.add_argument("--web-port", type=int, default=None,
        help="Web UI port for this run (default: stored pref, then 18100)")
    p_web.add_argument("--no-browser", action="store_true", help="Don't open browser")
    web_sub = p_web.add_subparsers(dest="web_verb", metavar="verb")
    p_web_auth_url = web_sub.add_parser(
        "auth-url",
        help="Print an authenticated browser bootstrap URL for the active Web server",
    )
    p_web_auth_url.add_argument(
        "--base-url",
        required=True,
        help="Canonical browser Origin, for example https://agent.example.com",
    )

    p_ports = sub.add_parser("ports",
        help="Show or set the web UI port; takes effect next start")
    p_ports.add_argument("--port", type=int, default=None, metavar="PORT",
        help="Persist the single web UI port. Default 18100.")

    # ---- config (scriptable settings: the same schema the TUI edits) ------
    p_config = sub.add_parser("config",
        help="View or change settings (`config list` / `config get KEY` / `config set KEY VALUE`)")
    config_sub = p_config.add_subparsers(dest="config_verb", metavar="verb")
    config_sub.add_parser("list", help="List every setting with its value, group, and apply mode")
    p_cget = config_sub.add_parser("get", help="Print one setting's current value")
    p_cget.add_argument("key", help="Setting id, e.g. ui.web_port (see `config list`)")
    p_cset = config_sub.add_parser("set", help="Change one setting; some take effect on next start")
    p_cset.add_argument("key", help="Setting id, e.g. ui.web_port")
    p_cset.add_argument("value", help="New value")

    # ---- provider request recordings ------------------------------------
    p_recordings = sub.add_parser(
        "recordings", help="Configure and manage provider request recordings"
    )
    recordings_sub = p_recordings.add_subparsers(dest="recordings_verb", metavar="verb")
    p_rec_status = recordings_sub.add_parser("status", help="Show configured mode and file")
    p_rec_status.add_argument("--json", action="store_true")
    p_rec_record = recordings_sub.add_parser("record", help="Record provider calls next start")
    p_rec_record.add_argument("--name", default=None, help="Managed recording ID")
    p_rec_replay = recordings_sub.add_parser("replay", help="Replay a recording next start")
    p_rec_replay.add_argument("selector", help="Managed ID or explicit file path")
    recordings_sub.add_parser("off", help="Disable record/replay next start")
    p_rec_list = recordings_sub.add_parser("list", help="List managed recordings")
    p_rec_list.add_argument("--json", action="store_true")
    p_rec_show = recordings_sub.add_parser("show", help="Show recording metadata")
    p_rec_show.add_argument("selector", help="Managed ID or explicit file path")
    p_rec_show.add_argument("--json", action="store_true")
    p_rec_show.add_argument("--content", action="store_true")
    p_rec_delete = recordings_sub.add_parser("delete", help="Delete one managed recording")
    p_rec_delete.add_argument("recording_id", help="Managed recording ID")
    p_rec_delete.add_argument("--yes", action="store_true")
    p_rec_prune = recordings_sub.add_parser("prune", help="Delete old managed recordings")
    p_rec_prune.add_argument("--older-than-days", type=int, required=True, metavar="N")
    p_rec_prune.add_argument("--dry-run", action="store_true")
    p_rec_prune.add_argument("--yes", action="store_true")

    # ---- memory (persistent, machine-wide knowledge) ----------------------
    p_memory = sub.add_parser("memory",
        help="Inspect / manage persistent memory (topics + sources + core).")
    memory_sub = p_memory.add_subparsers(dest="memory_verb", metavar="verb")
    memory_sub.add_parser("status",
        help="Show workspace contents, revision, writer health, and pending turns.")
    p_mr = memory_sub.add_parser("recall",
        help="Search memory and print the matching paragraphs.")
    p_mr.add_argument("query", nargs="+", help="Words to recall memories for")
    p_ms = memory_sub.add_parser("show",
        help="Print one memory file, e.g. topics/people/dave.md.")
    p_ms.add_argument("path", help="Path of the memory file to print")
    p_med = memory_sub.add_parser("edit",
        help="Open a memory file in $EDITOR; the edit lands only if it validates.")
    p_med.add_argument("path", help="Path of the memory file to open")
    p_msleep = memory_sub.add_parser("sleep",
        help="Reorganise topic files now, instead of waiting for tonight.")
    p_msleep.add_argument("--model", default=None,
        help="Model to reorganise with (default: whatever your own CLI uses)")
    p_mbackfill = memory_sub.add_parser("backfill",
        help="Write trusted source records that no Topic cites.")
    p_mbackfill.add_argument("--model", default=None,
        help="Model to write with (default: the configured memory writer)")
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

    # Top-level aliases that hide the internal "worker" noun. Running
    # `openprogram` already auto-starts the background service, so the only
    # verbs a user needs are stop / status / restart.
    sub.add_parser("stop",
        help="Stop the background OpenProgram service. The web UI stays "
             "reachable after you close the terminal until you run this.")
    sub.add_parser("status",
        help="Show whether the background service is running (PID, port, uptime).")
    sub.add_parser("restart",
        help="Restart the background service (picks up new code / config).")

    # ---- upgrade ----------------------------------------------------------
    p_upgrade = sub.add_parser("upgrade",
        help="Install the latest complete stable Release. In a source checkout, "
             "run the gated Git/build/probe/restart pipeline instead.")
    upgrade_sub = p_upgrade.add_subparsers(dest="upgrade_verb", metavar="verb")
    p_upgrade_status = upgrade_sub.add_parser("status",
        help="Show current/target version or SHA and whether an update is "
             "available. Read-only; source checkouts persist an explicit "
             "--channel.")
    # Repeated on the subparser so both `upgrade --json status` and the
    # natural `upgrade status --json` work.
    p_upgrade_status.add_argument("--json", action="store_true",
        help="Emit JSON")
    p_upgrade_status.add_argument("--channel", metavar="NAME",
        help="For a source checkout, report against and persist this channel "
             "instead of the configured one.")
    p_upgrade.add_argument("--channel", metavar="NAME",
        help="Release line to follow (default: stable). Source checkouts "
             "persist it as the `update.channel` setting.")
    p_upgrade.add_argument("--dry-run", action="store_true",
        help="Print planned steps without changing checkout, worker, or "
             "upgrade state. A source checkout still persists an explicit "
             "--channel.")
    p_upgrade.add_argument("--no-restart", action="store_true",
        help="Source checkout only: stop after the probe without restarting.")
    p_upgrade.add_argument("--yes", "-y", action="store_true",
        help="Source checkout only: allow a confirmed Git downgrade.")
    p_upgrade.add_argument("--json", action="store_true", help="Emit JSON")
    p_upgrade.add_argument("--check", action="store_true",
        help="Only report whether a stable update is available.")

    # ---- channels ---------------------------------------------------------
    p_channels = sub.add_parser("channels",
        help="Run / inspect chat-channel bots (Telegram, Discord, Slack, WeChat)")
    channels_sub = p_channels.add_subparsers(dest="channels_verb", metavar="verb")
    channels_sub.add_parser("list", help="Show per-platform enable + config status")
    channels_sub.add_parser("setup",
        help="Interactive wizard — pick channel, log in (QR / token), "
             "bind to an agent. One command instead of "
             "`accounts add` + `accounts login` + `bindings add`. "
             "Channels run inside the background service — start it by "
             "running `openprogram`.")
    # ---- channels accounts --------------------------------------------
    p_chacct = channels_sub.add_parser("accounts",
        help="Manage channel bot accounts (WeChat, Telegram, etc.)")
    p_chacct_sub = p_chacct.add_subparsers(dest="accounts_verb",
                                            metavar="verb")
    p_chacct_sub.add_parser("list", help="List every channel account")
    p_chacct_add = p_chacct_sub.add_parser("add",
        help="Create a new channel account and prompt for credentials")
    p_chacct_add.add_argument("channel", help="Channel id (telegram, discord, slack, wechat)",
        choices=["wechat", "telegram", "discord", "slack"])
    p_chacct_add.add_argument("--id", default="default",
        help="Account id (default: 'default')")
    p_chacct_rm = p_chacct_sub.add_parser("rm",
        help="Delete a channel account (also drops its bindings)")
    p_chacct_rm.add_argument("channel", help="Channel id the account belongs to",
        choices=["wechat", "telegram", "discord", "slack"])
    p_chacct_rm.add_argument("account_id", help="Account id to remove")
    p_chacct_login = p_chacct_sub.add_parser("login",
        help="Re-run the login flow for an account (e.g. WeChat QR)")
    p_chacct_login.add_argument("channel", help="Channel id to log into",
        choices=["wechat", "telegram", "discord", "slack"])
    p_chacct_login.add_argument("--id", default="default",
        help="Account id (default: 'default')")
    p_chacct_set = p_chacct_sub.add_parser("set",
        help="Set an account behavior setting (e.g. telegram group "
             "semantics: group_sessions=shared|per-user, "
             "require_mention=on|off). Restart the worker to apply.")
    p_chacct_set.add_argument("channel", help="Channel id",
        choices=["wechat", "telegram", "discord", "slack"])
    p_chacct_set.add_argument("key", help="Setting key (see channel docs)")
    p_chacct_set.add_argument("value", help="Setting value")
    p_chacct_set.add_argument("--id", default="default",
        help="Account id (default: 'default')")

    # ---- channels access ------------------------------------------------
    p_chaccess = channels_sub.add_parser("access",
        help="Inbound sender access control: allowlist + pairing codes. "
             "Unknown senders get a pairing code instead of driving the "
             "agent; approve them here (never from the chat itself). An "
             "account takes any number of approved senders — they share one "
             "agent and one memory, which records who said what.")
    p_chaccess_sub = p_chaccess.add_subparsers(dest="access_verb",
                                               metavar="verb")
    p_cha_list = p_chaccess_sub.add_parser("list",
        help="Show policy, allowlist and pending pairing codes")
    p_cha_list.add_argument("channel", nargs="?", default=None,
        help="Limit to one channel (optional)")
    p_cha_approve = p_chaccess_sub.add_parser("approve",
        help="Approve a pending sender by pairing code")
    p_cha_approve.add_argument("channel",
        choices=["wechat", "telegram", "discord", "slack"])
    p_cha_approve.add_argument("code", help="Pairing code the sender received")
    p_cha_approve.add_argument("--id", default="default",
        help="Account id (default: 'default')")
    p_cha_allow = p_chaccess_sub.add_parser("allow",
        help="Allowlist a platform user id directly (no pairing code needed)")
    p_cha_allow.add_argument("channel",
        choices=["wechat", "telegram", "discord", "slack"])
    p_cha_allow.add_argument("user_id", help="Platform-native sender id")
    p_cha_allow.add_argument("--id", default="default",
        help="Account id (default: 'default')")
    p_cha_revoke = p_chaccess_sub.add_parser("revoke",
        help="Remove a sender from the allowlist (and pending list)")
    p_cha_revoke.add_argument("channel",
        choices=["wechat", "telegram", "discord", "slack"])
    p_cha_revoke.add_argument("user_id", help="Platform-native sender id")
    p_cha_revoke.add_argument("--id", default="default",
        help="Account id (default: 'default')")
    # ---- channels bindings --------------------------------------------
    p_chb = channels_sub.add_parser("bindings",
        help="Route inbound channel messages to agents")
    p_chb_sub = p_chb.add_subparsers(dest="bindings_verb", metavar="verb")
    p_chb_sub.add_parser("list", help="Show every routing rule")
    p_chb_add = p_chb_sub.add_parser("add",
        help="Add a binding: inbound messages matching (channel, account, "
             "optional peer) go to the given agent")
    p_chb_add.add_argument("agent_id", help="Agent that matching inbound messages route to")
    p_chb_add.add_argument("--channel", required=True, help="Channel id this binding matches",
        choices=["wechat", "telegram", "discord", "slack"])
    p_chb_add.add_argument("--account", default=None,
        help="Account id (omit for channel-wide)")
    p_chb_add.add_argument("--peer", default=None,
        help="Specific peer id (user_id / chat_id) — omit for broad rule")
    p_chb_add.add_argument("--peer-kind", default="direct", help="Peer kind: direct | group (default: direct)",
        choices=["direct", "group", "channel"])
    p_chb_rm = p_chb_sub.add_parser("rm",
        help="Remove a binding by its id (see `bindings list`)")
    p_chb_rm.add_argument("binding_id", help="Binding id to remove (see `channels bindings list`)")

    # ---- mcp -------------------------------------------------------------
    p_mcp = sub.add_parser("mcp",
        help="Manage MCP (Model Context Protocol) servers. Talks to "
             "the background service — start it first by running "
             "`openprogram`. Same backend as the webui /mcp page and "
             "the TUI /mcp command.")
    p_mcp_sub = p_mcp.add_subparsers(dest="mcp_verb", metavar="verb")
    p_mcp_token = p_mcp_sub.add_parser(
        "token", help="Manage the independent stdio MCP server token"
    )
    p_mcp_token_sub = p_mcp_token.add_subparsers(
        dest="mcp_token_verb", metavar="verb"
    )
    p_mcp_token_sub.add_parser(
        "create", help="Create and print a new stdio MCP server token"
    )
    p_mcp_token.set_defaults(_cmd_parser=p_mcp_token)
    p_mcp_sub.add_parser("serve", help="Serve authenticated MCP over local stdio")
    p_mcp_sub.add_parser("list", help="List every configured MCP server with state")
    p_mcp_show = p_mcp_sub.add_parser("show", help="Show one server's tools + full schemas")
    p_mcp_show.add_argument("name", help="MCP server name to show")
    p_mcp_add = p_mcp_sub.add_parser("add",
        help="Add a new MCP server (stdio command). Persists to "
             "mcp_servers.json and spawns immediately.")
    p_mcp_add.add_argument("name", help="Short identifier (used as tool prefix)")
    p_mcp_add.add_argument("server_command", metavar="command", nargs="+",
        help="Command and args to spawn the server, e.g. `npx -y @drawio/mcp`")
    p_mcp_add.add_argument("--env", action="append", default=None,
        metavar="KEY=VALUE",
        help="Env var to inject into the subprocess (repeatable)")
    p_mcp_add.add_argument("--timeout", type=float, default=30.0,
        help="Startup + per-call timeout (s)")
    p_mcp_add.add_argument("--disabled", action="store_true",
        help="Create the entry but don't start it")
    p_mcp_rm = p_mcp_sub.add_parser("rm", help="Remove a server (stop + delete config)")
    p_mcp_rm.add_argument("name", help="MCP server name to remove")
    p_mcp_rs = p_mcp_sub.add_parser("restart", help="Stop + respawn one server")
    p_mcp_rs.add_argument("name", help="MCP server name to restart")
    p_mcp_en = p_mcp_sub.add_parser("enable", help="Enable + spawn")
    p_mcp_en.add_argument("name", help="MCP server name to enable")
    p_mcp_dis = p_mcp_sub.add_parser("disable", help="Stop + mark disabled (config kept)")
    p_mcp_dis.add_argument("name", help="MCP server name to disable")
    p_mcp_sub.add_parser("edit",
        help="Removed: raw editing exposed stored secrets. Use add/rm or the "
             "MCP settings page.")
    p_mcp_test = p_mcp_sub.add_parser("test",
        help="Spawn an ad-hoc config and verify the server starts + "
             "returns a tool list. Doesn't write disk.")
    p_mcp_test.add_argument("name", help="Name to label this MCP server under")
    p_mcp_test.add_argument("server_command", metavar="command", nargs="+",
        help="Command and args that launch the MCP server")
    p_mcp_test.add_argument("--env", action="append", default=None, help="Extra env var as KEY=VALUE (repeatable)",
        metavar="KEY=VALUE")
    p_mcp_test.add_argument("--timeout", type=float, default=30.0, help="Startup timeout in seconds (default: 30)")

    # ---- browser ---------------------------------------------------------
    p_browser = sub.add_parser("browser",
        help="Install + maintain the browser tools. Lifecycle (open, "
             "login, attach) is handled automatically by the tools "
             "themselves — see /browser inside the chat.")
    p_browser_sub = p_browser.add_subparsers(dest="browser_verb", metavar="verb")
    p_br_install = p_browser_sub.add_parser("install",
        help="Install browser-tool dependencies (Playwright + Chromium, "
             "patchright/camoufox, agent-browser) in a source checkout. "
             "Packaged releases reject this command.",
        description="Source checkout only: install optional browser backends. "
                    "Packaged releases reject this command.")
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
    p_ag_rm.add_argument("id", help="Agent id to remove")
    p_ag_show = p_agents_sub.add_parser("show",
        help="Print one agent's full record")
    p_ag_show.add_argument("id", help="Agent id to show (config + channel bindings)")
    p_ag_def = p_agents_sub.add_parser("set-default",
        help="Mark an agent as the default")
    p_ag_def.add_argument("id", help="Agent id to make the default")

    # ---- scheduler-worker -------------------------------------------------
    p_cron = sub.add_parser("scheduler-worker", aliases=["cron-worker"],
        help="Foreground loop that fires Scheduler tasks")
    p_cron.add_argument("--once", action="store_true",
        help="Evaluate one tick and exit")
    p_cron.add_argument("--list", action="store_true",
        help="Show each entry with match status")

    # ---- providers --------------------------------------------------------
    p_providers = sub.add_parser("providers",
        aliases=["secrets"],
        help="Manage LLM providers / stored credentials "
             "(login, list, status, doctor, ...). `secrets` is an alias.")
    providers_sub = p_providers.add_subparsers(dest="providers_cmd", metavar="verb")
    from openprogram.auth.cli import build_parser as _build_provider_verbs
    _build_provider_verbs(providers_sub)

    # ---- setup (unified) — first-run wizard, menu loop, or jump-to-section
    # Three usage shapes under one verb. All three are positional — no
    # mode flags — so the help reads as one consistent grammar:
    #
    #   openprogram setup                  # full wizard (default — first-run)
    #   openprogram setup menu             # interactive section picker
    #   openprogram setup <section>        # jump to one section
    #
    # The positional accepts ``menu`` (special) plus every section name:
    # model, tools, agent, skills, ui, memory, profile, search, tts,
    # channels, backend. Provider configuration lives under
    # ``openprogram providers setup`` — it has its own login / profile
    # flows that don't fit the section model, so we don't duplicate it.
    SETUP_SECTIONS = (
        "model", "tools", "agent", "skills", "ui", "memory",
        "profile", "search", "tts", "channels", "backend",
    )
    SETUP_TARGETS = ("menu",) + SETUP_SECTIONS
    p_setup = sub.add_parser(
        "setup",
        help="Run the setup wizard (first-run by default; "
             "`menu` for picker, `<section>` to jump).",
    )
    p_setup.add_argument(
        "target", nargs="?", default=None, choices=SETUP_TARGETS,
        metavar="[menu | <section>]",
        help="``menu`` opens the interactive picker; a section name "
             "(model / tools / agent / skills / ui / memory / profile / "
             "search / tts / channels / backend) jumps to that section; "
             "omit for the full first-run wizard.",
    )

    # main() 的 dispatch 需要这些子 parser(缺 verb 时打印对应 help),
    # 但它们是本函数局部变量 — 经 set_defaults 盖进 args,嵌套子命令
    # 由更深一层覆盖,args._cmd_parser 恒为选中路径上最深的一个。
    for _p in (p_logs, p_programs, p_skills, p_plugins, p_trash, p_backup, p_sessions,
               p_execution, p_jobs,
               p_subagent, p_memory, p_worker, p_channels, p_chacct,
               p_chaccess, p_chb, p_mcp, p_browser, p_agents,
               p_config, p_recordings, p_upgrade, p_providers):
        _p.set_defaults(_cmd_parser=_p)

    return parser
