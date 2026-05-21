"""``openprogram programs`` + ``openprogram configure`` handlers."""
from __future__ import annotations

import os
import sys


def _get_runtime(provider=None, model=None):
    """Get a Runtime via auto-detection or explicit provider/model override."""
    from openprogram.providers.registry import create_runtime
    return create_runtime(provider=provider, model=model)


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
    """List the registered agentic functions (functions/_registry.py)."""
    from openprogram.functions._registry import iter_agentic_files
    from openprogram.functions import agentics as _agentics_pkg

    entries: list[tuple[str, str]] = []
    for mod_name, filepath, _is_harness in iter_agentic_files(
        os.path.dirname(_agentics_pkg.__file__)
    ):
        name = mod_name
        desc = ""
        try:
            with open(filepath) as f:
                content = f.read()
            if '"""' in content:
                start = content.index('"""') + 3
                end = content.index('"""', start)
                desc = content[start:end].strip().split("\n")[0]
        except OSError:
            pass
        entries.append((name, desc))

    if not entries:
        print("No functions registered.")
        return

    print(f"Functions ({len(entries)}):\n")
    for name, desc in sorted(entries):
        print(f"  {name:24s}  {desc}")


def _cmd_run(name, arg_list, provider=None, model=None):
    """Run an existing function."""
    import inspect
    try:
        from openprogram.functions import resolve_function_module
        mod = resolve_function_module(name)
        loaded_func = getattr(mod, name)
    except (ImportError, AttributeError):
        print(f"Error: function '{name}' not found in openprogram/functions/agentics/")
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
