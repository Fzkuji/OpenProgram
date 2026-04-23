"""
agentic CLI — command-line interface for Agentic Programming.

Usage:
    openprogram create "description" --name my_func
    openprogram edit my_func --instruction "change X to Y"
    openprogram run my_func --arg key=value
    openprogram list
    openprogram create-skill my_func
    agentic providers                     # show available providers
    openprogram create "desc" --provider anthropic --model claude-sonnet-4-6
"""

import argparse
import sys
import json


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
        description="Agentic Programming CLI — create, edit, and run LLM-powered functions.",
    )
    sub = parser.add_subparsers(dest="command", help="Command to run")

    # create
    p_create = sub.add_parser("create", help="Create a new function from description")
    p_create.add_argument("description", help="What the function should do")
    p_create.add_argument("--name", "-n", required=True, help="Function name")
    p_create.add_argument("--as-skill", action="store_true", help="Also create a SKILL.md")
    _add_provider_args(p_create)

    # edit
    p_edit = sub.add_parser("edit", help="Edit an existing function")
    p_edit.add_argument("name", help="Function name to edit")
    p_edit.add_argument("--instruction", "-i", default=None, help="What to change")
    _add_provider_args(p_edit)

    # run
    p_run = sub.add_parser("run", help="Run an existing function")
    p_run.add_argument("name", help="Function name to run")
    p_run.add_argument("--arg", "-a", action="append", default=[], help="Arguments as key=value")
    _add_provider_args(p_run)

    # list
    sub.add_parser("list", help="List all saved functions")

    # create-app
    p_app = sub.add_parser("create-app", help="Create a complete runnable app (runtime + functions + main)")
    p_app.add_argument("description", help="What the app should do")
    p_app.add_argument("--name", "-n", default="app", help="App name (default: app)")
    _add_provider_args(p_app)

    # create-skill
    p_skill = sub.add_parser("create-skill", help="Create a SKILL.md for a function")
    p_skill.add_argument("name", help="Function name")
    _add_provider_args(p_skill)

    # deep-work
    p_deep = sub.add_parser("deep-work", help="Run autonomous agent on a complex task with quality evaluation")
    p_deep.add_argument("task", help="The task to accomplish")
    p_deep.add_argument("--level", "-l", default="bachelor",
                        choices=["high_school", "bachelor", "master", "phd", "professor"],
                        help="Quality level (default: bachelor)")
    p_deep.add_argument("--max-steps", type=int, default=100, help="Max total steps (default: 100)")
    p_deep.add_argument("--max-revisions", type=int, default=5, help="Max evaluation cycles (default: 5)")
    p_deep.add_argument("--no-interactive", action="store_true",
                        help="Skip clarification questions, start immediately")
    _add_provider_args(p_deep)

    # resume
    p_resume = sub.add_parser("resume", help="Resume a waiting follow-up session with an answer")
    p_resume.add_argument("session_id", help="Session ID from a previous follow-up")
    p_resume.add_argument("answer", help="Answer to the follow-up question")

    # sessions
    sub.add_parser("sessions", help="List active follow-up sessions")

    # install-skills
    p_skills = sub.add_parser("install-skills", help="Install skills for Claude Code / Gemini CLI")
    p_skills.add_argument("--target", "-t", default=None,
                          choices=["claude", "gemini"],
                          help="Target CLI tool (default: auto-detect)")

    # skills — introspect the skill registry the runtime will load
    p_skills_ns = sub.add_parser(
        "skills",
        help="Inspect discovered SKILL.md entries (list, doctor)",
    )
    skills_sub = p_skills_ns.add_subparsers(dest="skills_verb", metavar="verb")
    p_skills_list = skills_sub.add_parser("list", help="List discovered skills with their descriptions")
    p_skills_list.add_argument("--dir", "-d", action="append", default=None,
                               help="Override search dir (repeatable). Default: ~/.openprogram/skills + repo skills/")
    p_skills_list.add_argument("--json", action="store_true", help="Emit JSON for scripting")
    p_skills_doctor = skills_sub.add_parser("doctor", help="Scan skill dirs and report problems")
    p_skills_doctor.add_argument("--dir", "-d", action="append", default=None,
                                 help="Override search dir (repeatable)")

    # cron-worker
    p_cron = sub.add_parser(
        "cron-worker",
        help="Foreground loop that fires scheduled entries from the `cron` tool",
    )
    p_cron.add_argument("--once", action="store_true",
        help="Evaluate one tick and exit (for testing / external schedulers)")
    p_cron.add_argument("--list", action="store_true",
        help="Show each entry and whether it matches the current minute")

    # web UI
    p_web = sub.add_parser("web", help="Start the web UI")
    p_web.add_argument("--port", type=int, default=8765, help="Port (default: 8765)")
    p_web.add_argument("--no-browser", action="store_true", help="Don't open browser")
    # Back-compat alias: accept old `agentic visualize` but point to same handler
    p_viz = sub.add_parser("visualize", help="(alias of 'web')")
    p_viz.add_argument("--port", type=int, default=8765, help="Port (default: 8765)")
    p_viz.add_argument("--no-browser", action="store_true", help="Don't open browser")

    # providers — single plural noun namespace for every LLM-provider
    # management verb. Per docs/design/cli-naming.md the shape is
    # `openprogram providers <verb>` or `openprogram providers profiles
    # <verb>` (exactly one verb per command; nested `auth` layer is
    # intentionally absent).
    p_providers = sub.add_parser(
        "providers",
        help="Manage LLM providers (login, list, status, ...)",
    )
    providers_sub = p_providers.add_subparsers(dest="providers_cmd", metavar="verb")
    from openprogram.auth.cli import build_parser as _build_provider_verbs
    _build_provider_verbs(providers_sub)

    # config — namespaced configuration commands (provider, ...)
    p_config = sub.add_parser("config", help="Configure OpenProgram (providers, models, ...)")
    p_config_sub = p_config.add_subparsers(dest="config_target", metavar="target")
    p_cfg_provider = p_config_sub.add_parser("provider",
        help="Interactive wizard to set up a provider (CLI login, API key, model)")
    p_cfg_provider.add_argument("name", nargs="?", default=None,
        help="Provider id (e.g. openai-codex). If omitted, you pick from a menu.")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    # Lazy imports — only load when needed
    if args.command == "resume":
        _cmd_resume(args.session_id, args.answer)
        return
    elif args.command == "sessions":
        _cmd_sessions()
        return
    elif args.command == "install-skills":
        _cmd_install_skills(args.target)
        return
    elif args.command == "skills":
        verb = getattr(args, "skills_verb", None) or "list"
        dirs = getattr(args, "dir", None)
        if verb == "list":
            sys.exit(_cmd_skills_list(dirs, getattr(args, "json", False)))
        elif verb == "doctor":
            sys.exit(_cmd_skills_doctor(dirs))
        else:
            p_skills_ns.print_help()
            return
    elif args.command in ("web", "visualize"):
        _cmd_web(args.port, not args.no_browser)
        return
    elif args.command == "cron-worker":
        _cmd_cron_worker(args.once, args.list)
        return
    elif args.command == "list":
        _cmd_list()
    elif args.command == "providers":
        # Bare `providers` is equivalent to `providers list` with an
        # extra footer pointing at the other verbs — `doctor`, `setup`,
        # `aliases`, `profiles`. Any explicit verb just dispatches.
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
    elif args.command == "config":
        if args.config_target == "provider":
            _cmd_configure(args.name)
        else:
            p_config.print_help()
    elif args.command == "create":
        _cmd_create(args.description, args.name, args.as_skill, args.provider, args.model)
    elif args.command == "create-app":
        _cmd_create_app(args.description, args.name, args.provider, args.model)
    elif args.command == "edit":
        _cmd_edit(args.name, args.instruction, args.provider, args.model)
    elif args.command == "run":
        _cmd_run(args.name, args.arg, args.provider, args.model)
    elif args.command == "create-skill":
        _cmd_create_skill(args.name, args.provider, args.model)
    elif args.command == "deep-work":
        _cmd_deep_work(
            args.task, args.level,
            args.provider, args.model,
            args.max_steps, args.max_revisions,
            not args.no_interactive,
        )


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
    """Interactive provider-setup wizard. Drives openprogram.legacy_providers.configuration."""
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
    """Start the web UI."""
    try:
        from openprogram.webui import start_web
    except ImportError:
        print("Web UI dependencies not installed.")
        print("Install with: pip install openprogram[web]")
        sys.exit(1)

    thread = start_web(port=port, open_browser=open_browser)
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


if __name__ == "__main__":
    main()
