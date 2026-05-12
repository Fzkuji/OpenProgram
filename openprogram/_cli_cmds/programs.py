"""``openprogram programs`` + ``openprogram configure`` handlers."""
from __future__ import annotations

import os
import sys


def _get_runtime(provider=None, model=None):
    """Get a Runtime via auto-detection or explicit provider/model override."""
    from openprogram.providers.registry import create_runtime
    return create_runtime(provider=provider, model=model)


def _get_functions_dir() -> str:
    import openprogram
    pkg = os.path.dirname(openprogram.__file__)
    return os.path.join(pkg, "programs", "functions", "third_party")


def _cmd_configure(provider: str | None):
    """Interactive provider-setup. Drives openprogram.providers.configuration."""
    from openprogram.providers import configuration

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
        while True:
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
                continue
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
    """List all saved third-party functions."""
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
    create(description=description, runtime=runtime, name=name, as_skill=as_skill)
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
    import inspect
    try:
        from openprogram.programs.functions import resolve_function_module
        mod = resolve_function_module(name)
        loaded_func = getattr(mod, name)
    except (ImportError, AttributeError):
        print(f"Error: function '{name}' not found in openprogram/programs/functions/third_party/")
        sys.exit(1)

    unwrapped_func = loaded_func._fn if hasattr(loaded_func, "_fn") else loaded_func
    source = ""
    try:
        source = inspect.getsource(unwrapped_func)
    except (OSError, TypeError):
        pass

    if "runtime.exec" in source or "runtime" in str(getattr(loaded_func, "__globals__", {})):
        runtime = _get_runtime(provider, model)
        if hasattr(loaded_func, "_fn") and loaded_func._fn:
            loaded_func._fn.__globals__["runtime"] = runtime
        elif hasattr(loaded_func, "__globals__"):
            loaded_func.__globals__["runtime"] = runtime

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
    """Create a SKILL.md for an existing function."""
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

    unwrapped_func = loaded_func._fn if hasattr(loaded_func, "_fn") else loaded_func
    try:
        code = inspect.getsource(unwrapped_func)
    except (OSError, TypeError):
        code = f"# Source not available for {name}"

    description = getattr(loaded_func, "__doc__", "") or name

    print(f"Creating skill for '{name}'...")
    path = create_skill(fn_name=name, description=description, code=code, runtime=runtime)
    print(f"  Skill created at {path}")
