"""
agentic CLI — command-line interface for Agentic Programming.

Usage:
    agentic create "description" --name my_func
    agentic fix my_func --instruction "change X to Y"
    agentic run my_func --arg key=value
    agentic list
    agentic create-skill my_func
    agentic providers                     # show available providers
    agentic create "desc" --provider anthropic --model claude-sonnet-4-20250514
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
        help="Model name override (e.g. sonnet, gpt-4o, claude-sonnet-4-20250514).",
    )


def main():
    parser = argparse.ArgumentParser(
        prog="agentic",
        description="Agentic Programming CLI — create, fix, and run LLM-powered functions.",
    )
    sub = parser.add_subparsers(dest="command", help="Command to run")

    # create
    p_create = sub.add_parser("create", help="Create a new function from description")
    p_create.add_argument("description", help="What the function should do")
    p_create.add_argument("--name", "-n", required=True, help="Function name")
    p_create.add_argument("--as-skill", action="store_true", help="Also create a SKILL.md")
    _add_provider_args(p_create)

    # fix
    p_fix = sub.add_parser("fix", help="Fix an existing function")
    p_fix.add_argument("name", help="Function name to fix")
    p_fix.add_argument("--instruction", "-i", default=None, help="What to change")
    _add_provider_args(p_fix)

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

    # providers
    sub.add_parser("providers", help="Show available LLM providers and detection status")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    # Lazy imports — only load when needed
    if args.command == "list":
        _cmd_list()
    elif args.command == "providers":
        _cmd_providers()
    elif args.command == "create":
        _cmd_create(args.description, args.name, args.as_skill, args.provider, args.model)
    elif args.command == "create-app":
        _cmd_create_app(args.description, args.name, args.provider, args.model)
    elif args.command == "fix":
        _cmd_fix(args.name, args.instruction, args.provider, args.model)
    elif args.command == "run":
        _cmd_run(args.name, args.arg, args.provider, args.model)
    elif args.command == "create-skill":
        _cmd_create_skill(args.name, args.provider, args.model)


def _get_runtime(provider=None, model=None):
    """Get a Runtime via auto-detection or explicit provider.

    Args:
        provider:  Provider name (e.g. "anthropic", "claude-code").
                   If None, auto-detects the best available.
        model:     Model name override.

    Returns:
        A ready-to-use Runtime instance.
    """
    from agentic.providers import create_runtime
    return create_runtime(provider=provider, model=model)


def _get_functions_dir():
    import os
    return os.path.join(os.path.dirname(__file__), "functions")


def _cmd_providers():
    """Show available providers and which one would be auto-detected."""
    import os
    import shutil
    from agentic.providers import PROVIDERS

    print("Available LLM providers:\n")

    # Check what's available
    detected = None
    statuses = {}

    cli_checks = {
        "claude-code": ("claude", "Claude Code CLI"),
        "codex": ("codex", "Codex CLI"),
        "gemini-cli": ("gemini", "Gemini CLI"),
    }
    api_checks = {
        "anthropic": ("ANTHROPIC_API_KEY", "Anthropic API"),
        "openai": ("OPENAI_API_KEY", "OpenAI API"),
        "gemini": ("GOOGLE_API_KEY", "Gemini API"),
    }

    # Detection order matches detect_provider()
    detection_order = ["claude-code", "codex", "gemini-cli", "anthropic", "openai", "gemini"]

    for name in detection_order:
        _, _, default_model = PROVIDERS[name]

        if name in cli_checks:
            cmd, label = cli_checks[name]
            found = shutil.which(cmd) is not None
            status = "ready" if found else "not found"
            how = f"`{cmd}` in PATH" if found else f"install: npm install -g ..."
        else:
            env_var, label = api_checks[name]
            found = bool(os.environ.get(env_var))
            status = "ready" if found else "not set"
            how = f"${env_var}" if found else f"export {env_var}=..."

        if found and detected is None:
            detected = name
            marker = " <-- auto-detected"
        else:
            marker = ""

        icon = "+" if found else "-"
        print(f"  [{icon}] {name:14s}  ({label:16s})  model: {default_model:30s}  [{status}]{marker}")

    print()
    if detected:
        print(f"Auto-detected provider: {detected}")
        print(f"Override with: agentic <command> --provider <name> --model <model>")
    else:
        print("No provider detected. Set up one of the above to get started.")
        print("See: https://github.com/Fzkuji/Agentic-Programming#quick-start")


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
    from agentic.meta_functions import create
    runtime = _get_runtime(provider, model)

    print(f"Creating '{name}' (provider: {runtime.__class__.__name__})...")
    fn = create(description=description, runtime=runtime, name=name, as_skill=as_skill)
    print(f"  Saved to agentic/functions/{name}.py")
    if as_skill:
        print(f"  Skill created at skills/{name}/SKILL.md")


def _cmd_create_app(description, name, provider=None, model=None):
    """Create a complete runnable app."""
    from agentic.meta_functions import create_app
    runtime = _get_runtime(provider, model)

    print(f"Creating app '{name}' (provider: {runtime.__class__.__name__})...")
    filepath = create_app(description=description, runtime=runtime, name=name)
    print(f"  Saved to {filepath}")
    print(f"  Run with: python {filepath}")


def _cmd_fix(name, instruction, provider=None, model=None):
    """Fix an existing function."""
    import importlib
    from agentic.meta_functions import fix
    runtime = _get_runtime(provider, model)

    try:
        mod = importlib.import_module(f"agentic.functions.{name}")
        fn = getattr(mod, name)
    except (ImportError, AttributeError):
        print(f"Error: function '{name}' not found in agentic/functions/")
        sys.exit(1)

    print(f"Fixing '{name}' (provider: {runtime.__class__.__name__})...")
    fixed = fix(fn=fn, runtime=runtime, instruction=instruction)
    print(f"  Fixed and saved to agentic/functions/{name}.py")


def _cmd_run(name, arg_list, provider=None, model=None):
    """Run an existing function."""
    import importlib

    try:
        mod = importlib.import_module(f"agentic.functions.{name}")
        fn = getattr(mod, name)
    except (ImportError, AttributeError):
        print(f"Error: function '{name}' not found in agentic/functions/")
        sys.exit(1)

    # Check if it needs runtime
    import inspect
    source = inspect.getsource(fn) if hasattr(fn, '_fn') else ""
    if hasattr(fn, '_fn'):
        try:
            source = inspect.getsource(fn._fn)
        except (OSError, TypeError):
            source = ""

    if "runtime.exec" in source or "runtime" in str(getattr(fn, '__globals__', {})):
        runtime = _get_runtime(provider, model)
        if hasattr(fn, '_fn') and fn._fn:
            fn._fn.__globals__['runtime'] = runtime
        elif hasattr(fn, '__globals__'):
            fn.__globals__['runtime'] = runtime

    # Parse arguments
    kwargs = {}
    for a in arg_list:
        if "=" in a:
            k, v = a.split("=", 1)
            kwargs[k] = v
        else:
            print(f"Error: argument must be key=value, got '{a}'")
            sys.exit(1)

    result = fn(**kwargs)
    print(result)


def _cmd_create_skill(name, provider=None, model=None):
    """Create a SKILL.md for a function."""
    import importlib
    import inspect
    from agentic.meta_functions import create_skill
    runtime = _get_runtime(provider, model)

    try:
        mod = importlib.import_module(f"agentic.functions.{name}")
        fn = getattr(mod, name)
    except (ImportError, AttributeError):
        print(f"Error: function '{name}' not found in agentic/functions/")
        sys.exit(1)

    # Get source and description
    try:
        if hasattr(fn, '_fn'):
            code = inspect.getsource(fn._fn)
        else:
            code = inspect.getsource(fn)
    except (OSError, TypeError):
        code = f"# Source not available for {name}"

    description = getattr(fn, '__doc__', '') or name

    print(f"Creating skill for '{name}'...")
    path = create_skill(fn_name=name, description=description, code=code, runtime=runtime)
    print(f"  Skill created at {path}")


if __name__ == "__main__":
    main()
