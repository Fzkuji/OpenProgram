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

import os
import sys
import json
from pathlib import Path

from openprogram.cli.parser import build_parser


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
        "execution", "jobs",
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


def _is_cli_process() -> bool:
    """Return whether this package was imported by an OpenProgram entry point."""
    executable = Path(sys.argv[0]).name.lower()
    if executable in {
        "openprogram",
        "openprogram.exe",
        "openprogram-script.py",
    }:
        return True
    if executable != "__main__.py":
        return False
    parent = Path(sys.argv[0]).parent.name
    return parent in {"openprogram", "cli", "openprogram_cli"}


if _is_cli_process():
    _maybe_redirect_for_tui()


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
        root,
        lambda stage: (stage / relative).write_text(edited, encoding="utf-8"),
        commit_message=f"memory: edit {relative}",
    )
    if not ok:
        print(f"Rejected: {message}")
        print(f"{relative} is unchanged; your edit is kept at {draft}")
        return 1
    shutil.rmtree(scratch, ignore_errors=True)
    print(f"{relative} validated and derived views rebuilt")
    if message:
        print(f"Warning: {message}")
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
        from openprogram.cli.commands.rescue import _cmd_rescue
        sys.exit(_cmd_rescue())

    if args.command == "logs":
        from openprogram.cli.commands.logs import (
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
        from openprogram.cli.commands.completion import _cmd_completion
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

    if args.command == "workflows":
        verb = getattr(args, "workflows_verb", None)
        if verb == "validate":
            sys.exit(_cmd_workflows_validate(args.directory, as_json=args.json))
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
            from openprogram.cli.commands.doctor import _cmd_doctor_credentials
            sys.exit(_cmd_doctor_credentials(
                repair=getattr(args, "repair", False), as_json=as_json
            ))
        from openprogram.cli.commands.doctor import _cmd_doctor
        sys.exit(_cmd_doctor(as_json))

    if args.command == "diagnostics":
        from openprogram.cli.commands.diagnostics import _cmd_diagnostics
        sys.exit(_cmd_diagnostics(getattr(args, "output", None)))

    if args.command == "acp":
        from openprogram.cli.commands.acp import _cmd_acp
        sys.exit(_cmd_acp(getattr(args, "agent", "main"),
                          getattr(args, "permission", "ask")))

    if args.command == "trash":
        from openprogram.cli.commands.trash import _cmd_trash_list, _cmd_trash_restore

        verb = getattr(args, "trash_verb", None)
        if verb == "list":
            sys.exit(_cmd_trash_list())
        if verb == "restore":
            sys.exit(_cmd_trash_restore(args.entry_id))
        _need_subcommand(args._cmd_parser)

    if args.command == "backup":
        from openprogram.cli.commands.backup import (
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
        from openprogram.cli.commands.plugins import (
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

    if args.command == "execution":
        from openprogram.cli.commands.execution import _cmd_execution_cancel

        verb = getattr(args, "execution_verb", None)
        if verb == "cancel":
            sys.exit(_cmd_execution_cancel(args.execution_id))
        _need_subcommand(args._cmd_parser)

    if args.command == "jobs":
        from openprogram.cli.commands.jobs import _cmd_jobs_get, _cmd_jobs_list

        verb = getattr(args, "jobs_verb", None)
        if verb == "list":
            sys.exit(_cmd_jobs_list(args.session_id, as_json=args.json))
        if verb == "get":
            sys.exit(_cmd_jobs_get(args.job_id, as_json=args.json))
        _need_subcommand(args._cmd_parser)

    if args.command == "web":
        if getattr(args, "web_verb", None) == "auth-url":
            from openprogram.cli.commands.web import _cmd_web_auth_url

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
        from openprogram.cli.commands.upgrade import _cmd_upgrade
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
        if verb == "spawn":
            as_json = not getattr(args, "no_json", False)
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
            as_json = not getattr(args, "no_json", False)
            sys.exit(_cmd_subagent_merge(
                target=args.target,
                branches=list(getattr(args, "branch", []) or []),
                message=getattr(args, "message", ""),
                agent_id=getattr(args, "agent", "main"),
                base_branch=getattr(args, "base", None),
                as_json=as_json,
            ))
        if verb == "list":
            sys.exit(_cmd_subagent_list(args.session, as_json=args.json))
        if verb == "show":
            sys.exit(_cmd_subagent_show(args.job_id, as_json=args.json))
        if verb == "cancel":
            sys.exit(_cmd_subagent_cancel(args.job_id, as_json=args.json))
        _need_subcommand(args._cmd_parser)

    if args.command in ("scheduler-worker", "cron-worker"):
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
# Subcommand handlers live in this application's ``commands`` package. They are
# re-exported here under the names tests / openprogram.cli.chat /
# openprogram.cli.ink import directly off ``openprogram.cli``.
# ---------------------------------------------------------------------------

from openprogram.cli.commands.programs import (  # noqa: E402,F401
    _get_runtime,
    _cmd_configure,
    _cmd_list,
    _cmd_run,
    _cmd_install,
    _cmd_uninstall,
    _cmd_programs_available,
)
from openprogram.cli.commands.workflows import _cmd_workflows_validate  # noqa: E402,F401
from openprogram.cli.commands.skills import (  # noqa: E402,F401
    _cmd_skills_list,
    _cmd_skills_doctor,
    _cmd_install_skills,
    _cmd_skills_search,
    _cmd_skills_install,
    _cmd_skills_update,
    _cmd_skills_remove,
)
from openprogram.cli.commands.browser import (  # noqa: E402,F401
    _python_pkg_present,
    _cmd_browser_install,
    _cmd_browser_status,
    _cmd_browser_refresh,
    _cmd_browser_reset,
    _cmd_browser_list,
    _cmd_browser_rm,
)
from openprogram.cli.commands.subagent import (  # noqa: E402,F401
    _cmd_subagent_cancel,
    _cmd_subagent_list,
    _cmd_subagent_spawn,
    _cmd_subagent_show,
    _cmd_subagent_merge,
)

from openprogram.cli.commands.sessions import (  # noqa: E402,F401
    _cmd_chat_sessions,
    _cmd_resume,
    _cmd_session_archive,
    _cmd_sessions,
    _cmd_sessions_export,
)
from openprogram.cli.commands.agents import (  # noqa: E402,F401
    _dispatch_agents_verb,
)
from openprogram.cli.commands.channels import (  # noqa: E402,F401
    _dispatch_access_verb,
    _dispatch_accounts_verb,
    _dispatch_bindings_verb,
    _login_account,
)
from openprogram.cli.commands.web import _cmd_web  # noqa: E402,F401
from openprogram.cli.commands.chat import (  # noqa: E402,F401
    _cmd_cli_chat,
)
from openprogram.cli.commands.cron import _cmd_cron_worker  # noqa: E402,F401
from openprogram.cli.commands.mcp import (  # noqa: E402,F401
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
