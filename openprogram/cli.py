"""
OpenProgram CLI.

Single-verb model (openclaw / gh / docker style). The top-level grammar
is:

    openprogram                           launch the terminal UI
                                          (Ink on macOS/Linux, Rich on
                                          Windows — both are "TUI";
                                          platform decides which)
    openprogram tui                       same as bare openprogram
    openprogram chat                      alias for `openprogram tui`
    openprogram --print "prompt"          one-shot — send prompt,
                                          print reply, exit
    openprogram --resume <session-id>     resume a prior chat session

    openprogram <verb> ...                everything else (web, programs,
                                          skills, providers, ...)

Examples:

    openprogram
    openprogram tui
    openprogram chat
    openprogram tui --print "summarise this file"
    openprogram tui --resume local_a1b2c3

    openprogram web                       browser UI (frontend + backend)

    openprogram programs list
    openprogram programs run my_func --arg key=value

    openprogram skills list
    openprogram skills install --target claude

    openprogram sessions list
    openprogram sessions resume <id> "answer"

    openprogram providers list
    openprogram providers login anthropic

Note on retired flags: ``--tui`` / ``--no-tui`` / ``--web`` / ``--cli``
are gone. The chat mode is now implicit (``openprogram`` is chat); the
browser is a verb (``openprogram web``); the REPL is a Windows-only
silent fallback when Ink can't initialise. ``--no-tui`` had no good
analogue (the verb-based design wins where the flag would have lost),
so it's removed entirely.
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
    """Return True if argv corresponds to launching the Ink TUI.

    Used by :func:`_maybe_redirect_for_tui` to decide whether to dup2
    stdio into a log file before the Ink Node process takes over the
    terminal — only worth doing when a TUI launch is actually going
    to happen.

    Bare ``openprogram`` and ``openprogram --resume <id>`` go to chat
    (which is TUI on POSIX). Any subcommand (``programs``, ``skills``,
    ``web``, ...) and one-shot flags (``--print`` / ``-p``) keep stdio
    plain — no TUI, no redirect.

    Windows: Ink's ``setRawMode`` reliably fails on common Windows
    terminal configurations (PowerShell loses the console-handle flag
    across Python subprocess inheritance; Git Bash / MinTTY doesn't
    expose a Windows console at all). So Windows skips the TUI attempt
    entirely and goes straight to the Rich REPL — which also means no
    stdio redirect is needed there.
    """
    if sys.platform == "win32":
        return False
    bypass_words = {
        "agents", "sessions", "channels", "config", "programs", "skills", "plugins", "doctor",
        "providers", "web", "resume", "init", "doctor", "browser",
        "worker", "update", "memory", "mcp", "trash", "backup",
        "recordings", "stop", "status", "restart", "upgrade", "help",
    }
    bypass_flags = {
        "--print", "-p", "--help", "-h", "--version", "--print-prompt",
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
        help="LLM provider: claude-code, openai-codex, gemini-cli, anthropic, openai, gemini. "
             "Auto-detected if not specified.",
    )
    parser.add_argument(
        "--model", "-m",
        default=None,
        help="Model name override (e.g. sonnet, gpt-4o, claude-sonnet-4-6).",
    )


def _ensure_utf8_stdio() -> None:
    """Force ``sys.stdout`` / ``sys.stderr`` to UTF-8 with replacement.

    On Windows the console defaults to ``cp1252`` (or ``gbk``, depending
    on locale) — both unable to encode the chat content that flows
    through our ``print`` -based ``_log``. Non-ASCII traffic (Chinese
    queries, em-dashes, …) raises ``UnicodeEncodeError`` mid-handler and
    bubbles out as a 500. ``errors='replace'`` is intentionally lossy —
    logs are diagnostic, not data: better a "?" placeholder than a
    crashed request.

    No-op on POSIX (stdout already utf-8) and on Python builds that
    don't expose ``reconfigure``.
    """
    import sys as _sys
    for stream in (_sys.stdout, _sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, OSError):
            pass


def _need_subcommand(parser) -> None:
    """A container verb was run with no subcommand: print its help (the
    subcommand list) and exit non-zero — the gh/opencode demandCommand
    UX, applied uniformly so a bare ``openprogram <verb>`` never silently
    does nothing or exits 0."""
    parser.print_help()
    sys.exit(2)


def _memory_edit(root, path: str) -> int:
    """Open one memory file in $EDITOR and land the result transactionally.

    The editor is pointed at a scratch copy, never at the committed file.
    A hand edit can drop a block ID or strand a footnote, and the check for
    that runs against the tree as it was *before* the edit — so it has to be
    read before anything is written. Editing the real file in place would
    make the file its own baseline, which is how a deleted block ID used to
    pass validation.

    A rejected edit therefore changes nothing on disk, and the scratch copy
    is left behind so the work that was rejected is not lost with it.
    """
    import os
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    from openprogram.memory.management.transaction import (
        TransactionError, staged_edit, validate_writable_path,
    )
    from openprogram.memory.workspace_layout import resolve_within

    name = path if path.endswith(".md") else path + ".md"
    target = resolve_within(root, name)
    if target is None:
        print(f"{path!r} is outside the memory workspace.")
        return 1
    relative = target.relative_to(Path(root).resolve()).as_posix()
    try:
        validate_writable_path(relative)
    except TransactionError as exc:
        print(f"Cannot edit {relative}: {exc.message}")
        return 1
    if not target.is_file():
        print(f"No memory file at {relative!r}.")
        return 1

    before = target.read_text(encoding="utf-8")
    scratch = Path(tempfile.mkdtemp(prefix="openprogram-memory-edit-"))
    draft = scratch / target.name
    draft.write_text(before, encoding="utf-8")
    subprocess.call([os.environ.get("EDITOR", "vi"), str(draft)])
    edited = draft.read_text(encoding="utf-8")
    if edited == before:
        shutil.rmtree(scratch, ignore_errors=True)
        print(f"{relative} unchanged.")
        return 0

    ok, message = staged_edit(
        root, lambda stage: (stage / relative).write_text(edited, encoding="utf-8")
    )
    if not ok:
        print(f"Rejected: {message}")
        print(f"{relative} is unchanged; your edit is kept at {draft}")
        return 1
    shutil.rmtree(scratch, ignore_errors=True)
    print(f"{relative} validated and derived views rebuilt")
    return 0


def _needs_first_run_setup() -> bool:
    """True when no LLM provider is configured yet.

    Lets a bare ``openprogram`` first run open the setup wizard automatically
    instead of dropping the user into an unconfigured chat — the same
    "configured?" test the doctor uses (an auth pool with ≥1 credential).
    """
    try:
        from openprogram.auth.store import get_store
        return not any(p.credentials for p in get_store().list_pools())
    except Exception:
        return False  # never block startup on a detection hiccup


def _restore_tui_tty() -> bool:
    """Point stdout/stderr back at the real terminal; True if it was redirected.

    The module-load redirect (:func:`_maybe_redirect_for_tui`) sends stdio to
    a log file so Ink starts on a clean terminal. But the bare-``openprogram``
    path runs INTERACTIVE steps before Ink — the first-run setup wizard and
    the TUI-vs-web menu. With stdio still pointed at the log those prompts
    were invisible on POSIX (the terminal just sat there), and ``isatty()``
    being False skipped the surface menu entirely — macOS went straight to
    the TUI while Windows (no redirect) asked. Restore the tty for the
    interactive stretch; call :func:`_re_redirect_tui_log` before launching
    Ink.
    """
    if _TUI_TTY_OUT is None or _TUI_TTY_ERR is None:
        return False
    try:
        os.dup2(_TUI_TTY_OUT, 1)
        os.dup2(_TUI_TTY_ERR, 2)
        return True
    except Exception:
        return False


def _re_redirect_tui_log() -> None:
    """Re-point stdout/stderr at the ink-startup log (post-prompt, pre-Ink)."""
    try:
        from pathlib import Path
        log_path = Path.home() / ".openprogram" / "logs" / "ink-startup.log"
        fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        os.dup2(fd, 1)
        os.dup2(fd, 2)
        os.close(fd)
    except Exception:
        pass


def _choose_surface() -> str:
    """Ask whether to open the terminal UI or the web UI; returns 'tui' or 'web'.

    Only for a bare ``openprogram`` (no surface given). ``openprogram tui`` /
    ``openprogram web`` skip this and launch directly. Non-interactive → 'tui'.
    Caller must have restored the real tty first (:func:`_restore_tui_tty`).
    """
    try:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return "tui"
    except Exception:
        return "tui"
    try:
        import questionary
        ans = questionary.select(
            "Start OpenProgram in:",
            choices=[
                questionary.Choice("Terminal UI — chat right here", value="tui"),
                questionary.Choice("Web UI — browser at http://localhost:18100", value="web"),
            ],
        ).ask()
        return ans if ans in ("tui", "web") else "tui"
    except Exception:
        try:
            print("Start OpenProgram in:\n  1) Terminal UI (default)\n  2) Web UI (browser)")
            return "web" if input("Choice [1]: ").strip() == "2" else "tui"
        except (EOFError, KeyboardInterrupt):
            return "tui"


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
    # Authoring (new / edit / app) lives in the `agentic-programming` skill now —
    # the agent writes .py files directly. Only run / list remain as CLI.
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
        help="credentials: audit every credential file's owner-only permissions")
    p_doctor.add_argument(
        "--repair", action="store_true",
        help="With `credentials`: restore owner-only permissions on files this "
             "user owns. Symlinks and foreign-owned paths are never modified.")

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

    # ---- tasks ------------------------------------------------------------
    p_tasks = sub.add_parser(
        "tasks", help="Inspect canonical resource state for background tasks",
    )
    tasks_sub = p_tasks.add_subparsers(dest="tasks_verb", metavar="verb")
    p_tasks_list = tasks_sub.add_parser("list", help="List task resource DTOs")
    p_tasks_list.add_argument(
        "--session", dest="session_id", default=None,
        help="Restrict tasks to one session id",
    )
    p_tasks_list.add_argument("--json", action="store_true", help="Emit JSON")
    p_tasks_get = tasks_sub.add_parser("get", help="Get one task resource DTO")
    p_tasks_get.add_argument("task_id", help="Task id")
    p_tasks_get.add_argument("--json", action="store_true", help="Emit JSON")

    # ---- subagent ----------------------------------------------------------
    # Subagent spawn / merge ops. See ``openprogram/agent/sub_agent_run.py``
    # and ``openprogram/agent/_merge.py`` for the model. These commands run
    # against the in-process SessionStore singleton — no WS, no webui.
    p_subagent = sub.add_parser("subagent",
        help="Spawn / merge subagent sessions.")
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
        help="Update to the latest code through a gated step chain "
             "(preflight, build, cold-start probe, restart, verify). "
             "Use this instead of `restart` after pulling code.")
    upgrade_sub = p_upgrade.add_subparsers(dest="upgrade_verb", metavar="verb")
    p_upgrade_status = upgrade_sub.add_parser("status",
        help="Show the channel, current sha, target sha, and whether an "
             "update is available. Read-only.")
    # Repeated on the subparser so both `upgrade --json status` and the
    # natural `upgrade status --json` work.
    p_upgrade_status.add_argument("--json", action="store_true",
        help="Emit JSON")
    p_upgrade_status.add_argument("--channel", metavar="NAME",
        help="Report against this channel instead of the configured one.")
    p_upgrade.add_argument("--channel", metavar="NAME",
        help="Release line to follow (default: stable). Persisted as the "
             "`update.channel` setting.")
    p_upgrade.add_argument("--dry-run", action="store_true",
        help="Print the planned steps and change nothing.")
    p_upgrade.add_argument("--no-restart", action="store_true",
        help="Stop after the probe — build and verify the new code without "
             "restarting the running instance.")
    p_upgrade.add_argument("--yes", "-y", action="store_true",
        help="Skip the confirmation a downgrade would otherwise require.")
    p_upgrade.add_argument("--json", action="store_true", help="Emit JSON")

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
    p_ag_rm.add_argument("id", help="Agent id to remove")
    p_ag_show = p_agents_sub.add_parser("show",
        help="Print one agent's full record")
    p_ag_show.add_argument("id", help="Agent id to show (config + channel bindings)")
    p_ag_def = p_agents_sub.add_parser("set-default",
        help="Mark an agent as the default")
    p_ag_def.add_argument("id", help="Agent id to make the default")

    # ---- cron-worker ------------------------------------------------------
    p_cron = sub.add_parser("cron-worker",
        help="Foreground loop that fires scheduled entries from the `cron` tool")
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
               p_tasks,
               p_subagent, p_memory, p_worker, p_channels, p_chacct,
               p_chaccess, p_chb, p_mcp, p_browser, p_agents,
               p_config, p_recordings, p_upgrade, p_providers):
        _p.set_defaults(_cmd_parser=_p)

    return parser


def main():
    _ensure_utf8_stdio()
    parser = build_parser()

    # ``openprogram help`` → ``openprogram --help`` (bare ``help`` isn't a
    # subcommand; users type it anyway). Only when it's the first token, so
    # ``openprogram config help`` etc. still reach their own sub-parser.
    if len(sys.argv) > 1 and sys.argv[1] == "help":
        sys.argv[1] = "--help"

    args = parser.parse_args()
    if getattr(args, "json_schema", None) and not args.print_prompt:
        parser.error("--json-schema requires --print")

    # --profile must land in the env BEFORE any later code reads a path
    # (setup config, session dir, logs dir, ...). get_active_profile
    # checks the env each call so setting it here is enough.
    if args.profile:
        from openprogram.paths import set_active_profile
        set_active_profile(args.profile)

    # -------- TUI launch (bare openprogram OR `openprogram tui/chat`) --
    # Chat is the default experience. Two backing implementations:
    #
    #   * macOS / Linux — full-screen Ink TUI (React-in-terminal, Node)
    #   * Windows       — Rich-driven terminal UI (Python; Ink can't
    #                     initialise raw input mode on Windows consoles)
    #
    # Both are valid "terminal UIs" from a user's perspective; the
    # ``tui_enabled`` flag selects which implementation to launch.
    # There is no user-facing knob — the platform decides.
    tui_enabled = sys.platform != "win32"

    if args.command == "rescue":
        from openprogram._cli_cmds.rescue import _cmd_rescue
        sys.exit(_cmd_rescue())

    if args.command == "logs":
        from openprogram._cli_cmds.logs import (
            _cmd_logs_list, _cmd_logs_path, _cmd_logs_tail,
        )
        verb = getattr(args, "logs_verb", None)
        if verb == "list" or verb is None:
            sys.exit(_cmd_logs_list())
        if verb == "path":
            sys.exit(_cmd_logs_path(args.name))
        if verb == "tail":
            sys.exit(_cmd_logs_tail(args.name, args.lines, args.follow))
        _need_subcommand(args._cmd_parser)

    if args.command == "completion":
        from openprogram._cli_cmds.completion import _cmd_completion
        sys.exit(_cmd_completion(args.shell))

    if args.command in (None, "tui", "chat"):
        if args.print_prompt:
            response_format = None
            schema_path = getattr(args, "json_schema", None)
            if schema_path:
                from openprogram.providers.structured_output import (
                    StructuredOutputError,
                    StructuredOutputSchemaError,
                    StructuredOutputUnsupportedError,
                    normalize_response_format,
                )
                try:
                    if schema_path == "-":
                        if args.print_prompt == "-":
                            parser.error("prompt and JSON schema cannot both read from stdin")
                        raw_schema = sys.stdin.read()
                    else:
                        from pathlib import Path
                        try:
                            raw_schema = Path(schema_path).read_text(encoding="utf-8")
                        except OSError as exc:
                            raise StructuredOutputSchemaError(
                                "JSON schema file could not be read",
                                code="invalid_schema",
                            ) from exc
                    try:
                        parsed_schema = json.loads(raw_schema)
                    except (
                        TypeError,
                        ValueError,
                        RecursionError,
                        json.JSONDecodeError,
                    ) as exc:
                        raise StructuredOutputSchemaError(
                            "JSON schema file is not valid JSON", code="invalid_schema"
                        ) from exc
                    response_format = normalize_response_format(parsed_schema)
                    _cmd_cli_chat(
                        oneshot=args.print_prompt,
                        resume=args.resume,
                        tui=tui_enabled,
                        response_format=response_format,
                    )
                except StructuredOutputError as exc:
                    if isinstance(exc, StructuredOutputSchemaError):
                        print(
                            f"{exc.code}: Structured output request is invalid",
                            file=sys.stderr,
                        )
                        raise SystemExit(2) from None
                    if isinstance(exc, StructuredOutputUnsupportedError):
                        print(
                            f"{exc.code}: Structured output is unsupported",
                            file=sys.stderr,
                        )
                        raise SystemExit(3) from None
                    print(
                        f"{exc.code}: Structured output generation failed",
                        file=sys.stderr,
                    )
                    raise SystemExit(4) from None
            else:
                _cmd_cli_chat(oneshot=args.print_prompt, resume=args.resume,
                              tui=tui_enabled)
            return
        # Interactive pre-Ink stretch (first-run wizard + surface menu) needs
        # the REAL terminal — the module-load redirect already pointed stdio
        # at the ink-startup log, which made these prompts invisible on POSIX.
        restored = _restore_tui_tty()
        # First-run onboarding: a brand-new user just types `openprogram`, not
        # `openprogram setup`. If nothing is configured yet, run the setup
        # wizard automatically, then open the chat — no separate command needed.
        if not args.resume and _needs_first_run_setup():
            try:
                from openprogram import setup as _sw
                _sw.run_full_setup()
            except KeyboardInterrupt:
                print("\nSetup cancelled. Run `openprogram setup` any time.")
                return
        # Bare `openprogram` (no surface given) → let the user pick terminal vs web.
        # The explicit `openprogram tui` / `openprogram chat` (here) and
        # `openprogram web` (its own subcommand) skip the prompt and launch directly.
        if args.command is None and not args.resume and _choose_surface() == "web":
            # _cmd_web is imported at module scope (bottom of this file); a
            # local `import` here would make it a function-local for all of
            # main(), shadowing that global and breaking the `web` subcommand
            # branch with UnboundLocalError.
            _cmd_web(None, None)
            return
        # Ink takes the terminal next — put stray module noise back in the log.
        if restored:
            _re_redirect_tui_log()
        _cmd_cli_chat(oneshot=None, resume=args.resume,
                      tui=tui_enabled)
        return

    # -------- Subcommand dispatch --------
    if args.command == "programs":
        verb = getattr(args, "programs_verb", None)
        if verb == "list":
            _cmd_list()
        elif verb == "run":
            _cmd_run(args.name, args.arg, args.provider, args.model)
        elif verb == "available":
            _cmd_programs_available()
        elif verb == "install":
            _cmd_install(args.name, upgrade=args.upgrade)
        elif verb == "uninstall":
            _cmd_uninstall(args.name)
        else:
            _need_subcommand(args._cmd_parser)
        return

    if args.command == "skills":
        verb = getattr(args, "skills_verb", None)
        if verb == "list":
            sys.exit(_cmd_skills_list(args.dir, args.json))
        elif verb == "doctor":
            sys.exit(_cmd_skills_doctor(args.dir))
        elif verb == "install":
            if args.spec:
                sys.exit(_cmd_skills_install(args.spec, source=args.source))
            else:
                _cmd_install_skills(args.target)
        elif verb == "search":
            sys.exit(_cmd_skills_search(args.query, source=args.source, limit=args.limit))
        elif verb == "update":
            sys.exit(_cmd_skills_update(args.all, args.name))
        elif verb == "remove":
            sys.exit(_cmd_skills_remove(args.name))
        else:
            _need_subcommand(args._cmd_parser)
        return

    if args.command == "doctor":
        as_json = getattr(args, "json", False)
        if getattr(args, "topic", None) == "credentials":
            from openprogram._cli_cmds.doctor import _cmd_doctor_credentials
            sys.exit(_cmd_doctor_credentials(
                repair=getattr(args, "repair", False), as_json=as_json
            ))
        from openprogram._cli_cmds.doctor import _cmd_doctor
        sys.exit(_cmd_doctor(as_json))

    if args.command == "diagnostics":
        from openprogram._cli_cmds.diagnostics import _cmd_diagnostics
        sys.exit(_cmd_diagnostics(getattr(args, "output", None)))

    if args.command == "acp":
        from openprogram._cli_cmds.acp import _cmd_acp
        sys.exit(_cmd_acp(getattr(args, "agent", "main"),
                          getattr(args, "permission", "ask")))

    if args.command == "trash":
        from openprogram._cli_cmds.trash import _cmd_trash_list, _cmd_trash_restore

        verb = getattr(args, "trash_verb", None)
        if verb == "list":
            sys.exit(_cmd_trash_list())
        if verb == "restore":
            sys.exit(_cmd_trash_restore(args.entry_id))
        _need_subcommand(args._cmd_parser)

    if args.command == "backup":
        from openprogram._cli_cmds.backup import (
            _cmd_backup_create, _cmd_backup_list, _cmd_backup_prune,
            _cmd_backup_restore,
        )

        verb = getattr(args, "backup_verb", None)
        if verb == "create":
            sys.exit(_cmd_backup_create(
                include_credentials=getattr(args, "include_credentials", False)))
        if verb == "list":
            sys.exit(_cmd_backup_list())
        if verb == "restore":
            sys.exit(_cmd_backup_restore(
                args.name,
                dry_run=getattr(args, "dry_run", False),
                yes=getattr(args, "yes", False),
            ))
        if verb == "prune":
            sys.exit(_cmd_backup_prune(args.keep))
        _need_subcommand(args._cmd_parser)

    if args.command == "plugins":
        from openprogram._cli_cmds.plugins import (
            _cmd_plugins_list, _cmd_plugins_search, _cmd_plugins_install,
            _cmd_plugins_uninstall, _cmd_plugins_update,
            _cmd_plugins_enable, _cmd_plugins_disable,
        )
        verb = getattr(args, "plugins_verb", None)
        if verb == "list":
            sys.exit(_cmd_plugins_list(args.json))
        elif verb == "search":
            sys.exit(_cmd_plugins_search(args.query))
        elif verb == "install":
            sys.exit(_cmd_plugins_install(args.source, args.spec, ref=args.ref))
        elif verb == "uninstall":
            sys.exit(_cmd_plugins_uninstall(args.name))
        elif verb == "update":
            sys.exit(_cmd_plugins_update(args.all, args.name))
        elif verb == "enable":
            sys.exit(_cmd_plugins_enable(args.name))
        elif verb == "disable":
            sys.exit(_cmd_plugins_disable(args.name))
        else:
            _need_subcommand(args._cmd_parser)
        return

    if args.command == "sessions":
        verb = getattr(args, "sessions_verb", None)
        if verb == "list":
            if getattr(args, "chat", False):
                scope = ("archived" if args.archived
                         else "all" if args.all_scope else "active")
                _cmd_chat_sessions(scope)
            else:
                _cmd_sessions()
        elif verb == "archive":
            _cmd_session_archive(args.session_id, True)
        elif verb == "unarchive":
            _cmd_session_archive(args.session_id, False)
        elif verb == "resume":
            _cmd_resume(args.session_id, args.answer)
        elif verb == "attach":
            from openprogram.agent.management import session_aliases as _a
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
            from openprogram.agent.management import session_aliases as _a
            removed = _a.detach(
                channel=args.channel, account_id=args.account,
                peer_kind=args.peer_kind, peer_id=args.peer,
            )
            if removed:
                print(f"Detached {args.channel}:{args.account}:"
                      f"{args.peer_kind}:{args.peer}")
            else:
                print("No matching alias.")
        elif verb == "export":
            _cmd_sessions_export(args.session_id, args.export_format,
                                 args.output)
        elif verb == "aliases":
            from openprogram.agent.management import session_aliases as _a
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
            _need_subcommand(args._cmd_parser)
        return

    if args.command == "tasks":
        from openprogram._cli_cmds.tasks import _cmd_tasks_get, _cmd_tasks_list

        verb = getattr(args, "tasks_verb", None)
        if verb == "list":
            sys.exit(_cmd_tasks_list(args.session_id, as_json=args.json))
        if verb == "get":
            sys.exit(_cmd_tasks_get(args.task_id, as_json=args.json))
        _need_subcommand(args._cmd_parser)

    if args.command == "web":
        if getattr(args, "web_verb", None) == "auth-url":
            from openprogram._cli_cmds.web import _cmd_web_auth_url

            sys.exit(_cmd_web_auth_url(args.base_url))
        _cmd_web(getattr(args, "web_port", None),
                 False if args.no_browser else None)
        return

    if args.command == "ports":
        from openprogram.setup import read_ui_prefs, set_ui_ports

        def _valid(p):
            return p is None or 1 <= p <= 65535

        if not _valid(args.port):
            print("Port must be in 1–65535.")
            return
        if args.port is None:
            prefs = read_ui_prefs()
            print(f"web UI (API + WebSocket + frontend):  {prefs['web_port']}")
            print()
            print("Change with:  openprogram ports --port <port>")
            print("Override one run via env:  OPENPROGRAM_WEB_PORT")
            return
        prefs = set_ui_ports(web_port=args.port)
        print("Saved. Takes effect on the next `openprogram web` / `openprogram worker` start.")
        print(f"  web UI:  {prefs['web_port']}")
        return

    if args.command == "config":
        from openprogram.config_schema import get_settings, set_setting
        verb = getattr(args, "config_verb", None)
        rows = get_settings()
        by_key = {r["key"]: r for r in rows}

        if verb in (None, "list"):
            group = None
            for r in rows:
                if r["group"] != group:
                    group = r["group"]
                    print(f"\n{group}")
                val = "(set)" if r.get("set") else r.get("value")
                tag = "  · next start" if r["apply"] == "next_start" else ""
                print(f"  {r['key']:24} {str(val):>10}{tag}")
            print("\nChange with:  openprogram config set <key> <value>")
            return

        if verb == "get":
            r = by_key.get(args.key)
            if r is None:
                print(f"unknown setting: {args.key}  (see `openprogram config list`)")
                sys.exit(1)
            print("(set)" if r.get("set") else r.get("value"))
            return

        if verb == "set":
            res = set_setting(args.key, args.value)
            if res.get("error"):
                print(f"error: {res['error']}")
                sys.exit(1)
            when = " (takes effect next start)" if res.get("applied") == "next_start" else ""
            print(f"{args.key} = {res.get('value')}{when}")
            if res.get("note"):
                print(f"  note: {res['note']}")
            return

    if args.command == "recordings":
        from openprogram.providers.recording import dispatch_recordings

        sys.exit(dispatch_recordings(args))

    if args.command == "memory":
        from openprogram.memory import DISABLED_MESSAGE, is_enabled

        if not is_enabled():
            print(DISABLED_MESSAGE)
            sys.exit(1)
        verb = getattr(args, "memory_verb", None)
        from openprogram.memory import store as _mstore
        from openprogram.memory.retrieval import inspect as _inspect
        if verb == "status":
            root = _mstore.ensure()
            import json as _json
            print(f"memory root:     {root}")
            # The local terminal is the owner's own surface, so the path
            # belongs here — unlike the tool result a model can quote.
            print(_json.dumps(
                _inspect.status(root, include_path=True),
                indent=2, ensure_ascii=False,
            ))
            sys.exit(0)
        if verb == "recall":
            root = _mstore.ensure()
            q = " ".join(args.query)
            found = _inspect.search(root, q, top_k=8)
            results = found.get("results", [])
            if not results:
                print(f"No memories matched {q!r}.")
                sys.exit(0)
            for hit in results:
                block = hit.get("event_id")
                head = hit.get("path", "?") + (f"#^{block}" if block else "")
                print(f"--- {head}")
                print(hit.get("content", "").strip())
                print()
            sys.exit(0)
        if verb == "show":
            root = _mstore.ensure()
            try:
                found = _inspect.read_file(root, args.path)
            except Exception as exc:  # noqa: BLE001
                print(f"Cannot read {args.path!r}: {exc}")
                sys.exit(1)
            print(found.get("content", ""))
            sys.exit(0)
        if verb == "edit":
            sys.exit(_memory_edit(_mstore.ensure(), args.path))
        if verb == "sleep":
            from openprogram.memory.writing import reorganize
            import json as _json
            print(_json.dumps(
                reorganize(model=getattr(args, "model", None)),
                indent=2, ensure_ascii=False,
            ))
            sys.exit(0)
        if verb == "backfill":
            from openprogram.memory.writing import backfill
            import json as _json
            report = backfill(
                _mstore.ensure(), model=getattr(args, "model", None),
            )
            print(_json.dumps(report, indent=2, ensure_ascii=False))
            sys.exit(0 if report.get("remaining") == 0 else 1)
        if verb == "export":
            import datetime as _dt
            import tarfile as _tar
            out = getattr(args, "out", None) or (
                f"./openprogram-memory-{_dt.date.today().isoformat()}.tar.gz"
            )
            with _tar.open(out, "w:gz") as t:
                t.add(str(_mstore.root()), arcname="memory")
            print(f"exported to {out}")
            sys.exit(0)
        _need_subcommand(args._cmd_parser)

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

    # Top-level aliases for the background service — no "worker" noun.
    if args.command == "stop":
        from openprogram import worker as _worker
        sys.exit(_worker.stop_worker())
    if args.command == "restart":
        from openprogram import worker as _worker
        sys.exit(_worker.restart_worker())
    if args.command == "upgrade":
        from openprogram._cli_cmds.upgrade import _cmd_upgrade
        sys.exit(_cmd_upgrade(args))
    if args.command == "status":
        from openprogram import worker as _worker
        from openprogram.worker import services as _services
        rc = _worker.print_status()
        if _services.is_supported():
            print()
            _services.status()
        sys.exit(rc)

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
        _need_subcommand(args._cmd_parser)

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
            _dispatch_accounts_verb(args, args._cmd_parser)
            return
        if verb == "access":
            _dispatch_access_verb(args, args._cmd_parser)
            return
        if verb == "bindings":
            _dispatch_bindings_verb(args, args._cmd_parser)
            return
        _need_subcommand(args._cmd_parser)
        return

    if args.command == "mcp":
        verb = getattr(args, "mcp_verb", None)
        if verb == "token":
            token_verb = getattr(args, "mcp_token_verb", None)
            if token_verb == "create":
                sys.exit(_cmd_mcp_token_create())
            _need_subcommand(args._cmd_parser)
        if verb == "list":
            sys.exit(_cmd_mcp_list())
        if verb == "serve":
            sys.exit(_cmd_mcp_serve())
        if verb == "show":
            sys.exit(_cmd_mcp_show(args.name))
        if verb == "add":
            sys.exit(_cmd_mcp_add(args.name, args.server_command,
                                   env=args.env,
                                   timeout=args.timeout,
                                   enabled=not args.disabled))
        if verb == "rm":
            sys.exit(_cmd_mcp_rm(args.name))
        if verb == "restart":
            sys.exit(_cmd_mcp_restart(args.name))
        if verb == "enable":
            sys.exit(_cmd_mcp_enable(args.name))
        if verb == "disable":
            sys.exit(_cmd_mcp_disable(args.name))
        if verb == "edit":
            sys.exit(_cmd_mcp_edit_removed())
        if verb == "test":
            sys.exit(_cmd_mcp_test(args.name, args.server_command,
                                    env=args.env, timeout=args.timeout))
        _need_subcommand(args._cmd_parser)

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
        _need_subcommand(args._cmd_parser)

    if args.command == "agents":
        _dispatch_agents_verb(args, args._cmd_parser)
        return

    if args.command == "subagent":
        verb = getattr(args, "subagent_verb", None)
        as_json = not getattr(args, "no_json", False)
        if verb == "spawn":
            context = getattr(args, "context", "inherit") or "inherit"
            if getattr(args, "clean", False):
                context = "clean"
            sys.exit(_cmd_subagent_spawn(
                session=args.session,
                prompt=args.prompt,
                parent_msg=getattr(args, "parent_msg", None),
                label=getattr(args, "label", None),
                agent_id=getattr(args, "agent", "main"),
                context=context,
                as_json=as_json,
            ))
        if verb == "merge":
            sys.exit(_cmd_subagent_merge(
                target=args.target,
                branches=list(getattr(args, "branch", []) or []),
                message=getattr(args, "message", ""),
                agent_id=getattr(args, "agent", "main"),
                base_branch=getattr(args, "base", None),
                as_json=as_json,
            ))
        _need_subcommand(args._cmd_parser)

    if args.command == "cron-worker":
        _cmd_cron_worker(args.once, args.list)
        return

    if args.command in ("providers", "secrets"):
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
        from openprogram import setup as _sw
        target = getattr(args, "target", None)
        if target == "menu":
            # Interactive picker that loops back to itself between
            # sections (old ``configure`` verb behaviour).
            sys.exit(_sw.run_configure_menu())
        if target:
            # Jump straight to one section.
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
            sys.exit(handlers[target]())
        # Default: full first-run wizard.
        sys.exit(_sw.run_full_setup())


# ---------------------------------------------------------------------------
# Subcommand handlers — bodies live in openprogram/_cli_cmds/*.py. They are
# re-exported here under the names tests / openprogram.cli_chat /
# openprogram.cli_ink import directly off ``openprogram.cli``.
# ---------------------------------------------------------------------------

from openprogram._cli_cmds.programs import (  # noqa: E402,F401
    _get_runtime,
    _cmd_configure,
    _cmd_list,
    _cmd_run,
    _cmd_install,
    _cmd_uninstall,
    _cmd_programs_available,
)
from openprogram._cli_cmds.skills import (  # noqa: E402,F401
    _cmd_skills_list,
    _cmd_skills_doctor,
    _cmd_install_skills,
    _cmd_skills_search,
    _cmd_skills_install,
    _cmd_skills_update,
    _cmd_skills_remove,
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
from openprogram._cli_cmds.subagent import (  # noqa: E402,F401
    _cmd_subagent_spawn,
    _cmd_subagent_merge,
)

from openprogram._cli_cmds.sessions import (  # noqa: E402,F401
    _cmd_chat_sessions,
    _cmd_resume,
    _cmd_session_archive,
    _cmd_sessions,
    _cmd_sessions_export,
)
from openprogram._cli_cmds.agents import (  # noqa: E402,F401
    _dispatch_agents_verb,
)
from openprogram._cli_cmds.channels import (  # noqa: E402,F401
    _dispatch_access_verb,
    _dispatch_accounts_verb,
    _dispatch_bindings_verb,
    _login_account,
)
from openprogram._cli_cmds.web import _cmd_web  # noqa: E402,F401
from openprogram._cli_cmds.chat import (  # noqa: E402,F401
    _cmd_cli_chat,
)
from openprogram._cli_cmds.cron import _cmd_cron_worker  # noqa: E402,F401
from openprogram._cli_cmds.mcp import (  # noqa: E402,F401
    _cmd_mcp_serve,
    _cmd_mcp_token_create,
    _cmd_mcp_list,
    _cmd_mcp_show,
    _cmd_mcp_add,
    _cmd_mcp_rm,
    _cmd_mcp_restart,
    _cmd_mcp_enable,
    _cmd_mcp_disable,
    _cmd_mcp_edit_removed,
    _cmd_mcp_test,
)



if __name__ == "__main__":
    main()
