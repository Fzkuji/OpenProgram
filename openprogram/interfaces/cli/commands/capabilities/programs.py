"""``openprogram programs`` and the provider-config wizard.

``openprogram programs`` is the one command group for Programs: the
built-in tools and applications, and the user's Programs in
``~/.openprogram/programs/``. Its verbs are ``list``, ``run``, ``status``,
``cancel``, ``check``, ``install``, ``uninstall``, ``enable`` and
``disable``.
"""
from __future__ import annotations

import json
import os
import sys
from openprogram.host._platform.filesystem import replace_file


def _get_runtime(provider=None, model=None):
    """Get a Runtime via auto-detection or explicit provider/model override."""
    from openprogram.runtime.agent.runtime.factory import create_runtime
    return create_runtime(provider=provider, model=model)


def _cmd_configure(provider: str | None):
    """Interactive provider-setup. Drives openprogram.models.providers.settings.configuration."""
    from openprogram.models.providers.settings import configuration

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
                    value = input("       > ").strip()
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


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

def _source_dir(tool) -> str:
    import inspect as _inspect
    fn = getattr(tool, "_python_callable", None) or getattr(tool, "_fn", None) or tool
    try:
        source = _inspect.getsourcefile(_inspect.unwrap(fn))
    except (TypeError, OSError):
        source = None
    return os.path.dirname(source) if source else ""


def program_rows() -> list[dict]:
    """Every Program this installation knows, one row each.

    Built-in tools, built-in applications, the user's packages and
    applications, and installed harnesses. Each row has ``name``, ``kind``
    (``tool``, ``application``, ``package``, ``harness``), ``owner``,
    ``state``, ``directory``, ``enabled``, ``entries``, ``tiers`` (entry →
    exposure tier) and ``note``. Applications other than the built-in ones
    load in the worker; here their state is read from the catalog and their
    source, and their entries show the tier they load with (``deferred``).
    """
    import openprogram.programs  # noqa: F401 — loads every Program
    from openprogram.state.program_directory import program_dirs, programs_root
    from openprogram.programs._host.ui import catalog
    from openprogram.programs._host.packages import package_statuses
    from openprogram.programs._host.registry import (
        _contexts, _iter_external_harness_dirs, builtin_application_names, program_tools,
    )
    from openprogram.programs._host.runtime import Tier, all_tools, get, tool_tier

    def tier(entry: str) -> str:
        tool = get(entry)
        # Not loaded in this process: an installed application's entries,
        # which load deferred in the worker.
        return (tool_tier(tool) if tool is not None else Tier.DEFERRED).value

    def row(name, kind, owner, state, directory="", enabled=True, entries=(), note=""):
        return {"name": name, "kind": kind, "owner": owner, "state": state,
                "directory": str(directory), "enabled": enabled,
                "entries": sorted(entries), "tiers": {e: tier(e) for e in sorted(entries)},
                "note": note}

    rows: list[dict] = []
    owned = {tool for ctx in list(_contexts.values()) for tool in ctx.contributions.get("tools", ())}
    for tool in sorted(all_tools(), key=lambda t: t.name):
        if tool.name.startswith("_") or tool.name in owned:
            continue
        rows.append(row(tool.name, "tool", "tool", "loaded", _source_dir(tool), entries=[tool.name]))
    for owner, ctx in sorted(_contexts.items()):
        if owner.startswith("tool:"):
            rows.append(row(owner[5:], "tool", owner, "loaded", ctx.root, entries=program_tools(owner)))

    from openprogram.programs._host.registry import _builtin_applications_root, builtin_application_enabled
    builtin = builtin_application_names()
    for name in builtin:
        owner = f"application:{name}"
        loaded = owner in _contexts
        enabled = builtin_application_enabled(name)
        state = "disabled" if not enabled else ("loaded" if loaded else "error")
        entries = program_tools(owner)
        # Same wording as the Applications page's built-in group.
        note = "built in" if entries or not loaded else "built in; registers no tools"
        rows.append(row(name, "application", owner, state,
                        os.path.join(_builtin_applications_root(), name), enabled,
                        entries=entries, note=note))

    for status in package_statuses():
        rows.append(row(status["name"], "package", status["name"], status["state"], status["path"],
                        entries=status["entries"], note=status["error"] or status["note"]))

    recorded = set()
    for definition in catalog.installed():
        if definition.get("builtin"):
            continue
        app_id = definition["id"]
        source = definition.get("source") or definition.get("path", "")
        recorded.add(os.path.realpath(source))
        enabled = bool(definition.get("enabled", True))
        state = "error" if definition.get("error") else ("enabled" if enabled else "disabled")
        operations = [op for op, spec in (definition.get("operations") or {}).items() if spec.get("agent")]
        from openprogram.programs._host.ui.loader import entry_name
        rows.append(row(app_id, "application", f"application:{app_id}", state, source, enabled,
                        [entry_name(app_id, op) for op in operations], definition.get("error", "")))
    for name, directory in program_dirs().items():
        if not (directory / "application.json").is_file() or os.path.realpath(directory) in recorded:
            continue
        try:
            definition = catalog.manifest(directory)
        except (OSError, ValueError) as exc:
            rows.append(row(name, "application", "", "error", directory, False, note=f"application.json: {exc}"))
            continue
        if catalog.needs_trust(definition):
            rows.append(row(definition["id"], "application", f"application:{definition['id']}", "needs trust",
                            directory, False, note=f"Review it, then run `openprogram programs install {directory} --trust`."))
        else:
            rows.append(row(definition["id"], "application", f"application:{definition['id']}", "not recorded",
                            directory, True, note="The worker records it the next time it scans " + str(programs_root()) + "."))

    for name, root in _iter_external_harness_dirs():
        from openprogram.programs._host.registry import _find_python_package
        package = _find_python_package(root)
        package_name = os.path.basename(package) if package else name
        owner = f"external:{package_name}"
        state = "loaded" if program_tools(owner) else ("error" if package is None else "installed")
        rows.append(row(name, "harness", owner, state, root, True, program_tools(owner),
                        "" if package else "No package with agentics/__init__.py."))
    return rows


def _cmd_list(*, as_json: bool = False) -> int:
    """Print every Program with its kind, owner, load state and directory."""
    from openprogram.programs._host.registry import load_errors
    from openprogram.state.program_directory import programs_root

    rows = program_rows()
    if as_json:
        print(json.dumps({"root": str(programs_root()), "programs": rows, "load_errors": load_errors()},
                         ensure_ascii=False, sort_keys=True))
        return 0
    order = {"tool": 0, "application": 1, "package": 2, "harness": 3}
    titles = {"tool": "Tools", "application": "Applications", "package": "Packages", "harness": "Harnesses"}
    for kind in sorted({r["kind"] for r in rows}, key=order.get):
        group = [r for r in rows if r["kind"] == kind]
        print(f"{titles[kind]} ({len(group)}):")
        for r in group:
            detail = r["directory"] if kind != "tool" else r["tiers"].get(r["name"], "")
            print(f"  {r['name']:<32} {r['state']:<13} {r['owner']:<34} {detail}")
            if kind != "tool" and r["entries"] and r["entries"] != [r["name"]]:
                shown = ", ".join(f"{e} ({r['tiers'][e]})" for e in r["entries"])
                print(f"  {'':<32} entries: {shown}")
            elif kind != "tool" and r["entries"]:
                print(f"  {'':<32} tier: {r['tiers'][r['name']]}")
            if r["note"]:
                print(f"  {'':<32} {r['note']}")
        print()
    print(f"Your Programs directory: {programs_root()}")
    return 0


# ---------------------------------------------------------------------------
# run / status / cancel
# ---------------------------------------------------------------------------

def _cmd_run_operation(app_id: str, operation: str, input_json: str, *, project: str = "",
                       request_key: str | None = None) -> None:
    from openprogram.programs._authoring import application_client as applications
    try:
        value = json.loads(input_json)
    except ValueError as exc:
        raise SystemExit(f"--input is not JSON: {exc}") from None
    result = applications.run(app_id, operation, value, project_id=project, request_key=request_key)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _cmd_run_status(run_id: str, *, cancel: bool = False) -> None:
    from openprogram.programs._authoring import application_client as applications
    result = applications.cancel(run_id) if cancel else applications.status(run_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------

def _cmd_check(directory: str, *, message: str = "", as_json: bool = False) -> int:
    from openprogram.programs._host.check import CheckFailed, check_program

    try:
        report = check_program(directory, message=message)
    except CheckFailed as exc:
        report = exc.report
    except (OSError, RuntimeError, ValueError) as exc:
        report = {"ok": False, "problems": [f"{type(exc).__name__}: {exc}"]}
    if as_json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0 if report.get("ok") else 1
    if not report.get("ok"):
        for problem in report.get("problems") or []:
            print(f"error: {problem}", file=sys.stderr)
        if report.get("traceback"):
            print(report["traceback"].rstrip(), file=sys.stderr)
        tests = report.get("tests") or {}
        if tests.get("ran") and not tests.get("passed"):
            print(tests.get("output", "").rstrip(), file=sys.stderr)
            print("Tests failed; nothing was committed.", file=sys.stderr)
        return 1
    application = report.get("application")
    if application:
        trust = " It needs trust: install it with --trust after reviewing it." if application["needs_trust"] else ""
        operations = ", ".join(application["agent_operations"]) or "none"
        print(f"Application {application['id']} {application['version']} is valid; "
              f"Agent-visible operations: {operations}.{trust}")
    if "package" in report.get("kinds", []):
        tests = report.get("tests") or {}
        if tests.get("ran"):
            where = "in the OS sandbox" if tests.get("sandboxed") else "without a sandbox"
            print(f"Tests passed {where}.")
        else:
            print("No tests in tests/.")
        entries = ", ".join(report.get("entries") or [])
        action = "Committed" if report.get("committed") else "Already committed"
        print(f"{action} {report['program']} at {report['revision'][:12]}; entries: {entries}")
    return 0


# ---------------------------------------------------------------------------
# install / uninstall / enable / disable
# ---------------------------------------------------------------------------

def _looks_like_git_ref(name: str) -> bool:
    """A git source: a URL (https/ssh/git/file) or GitHub ``owner/repo``."""
    if name.startswith(("http://", "https://", "git@", "ssh://", "file://")):
        return True
    return (name.count("/") == 1 and " " not in name and not name.startswith((".", "/", "~"))
            and not os.path.exists(os.path.expanduser(name)))


def _normalize_git_ref(ref: str) -> str:
    """Expand ``owner/repo`` shorthand to a GitHub HTTPS URL."""
    if ref.startswith(("http://", "https://", "git@", "ssh://", "file://")) or os.path.isdir(ref):
        return ref
    return f"https://github.com/{ref}"


def _install_dependencies(dest: str, label: str, *, upgrade: bool = False) -> None:
    """Install a harness's own declared dependencies from its clone.

    A managed install must stay as it was built, so they go to a per-user directory.
    """
    import subprocess
    from openprogram.operations.self_update.github_release.detect import InstallMethod, detect_install_method

    pip = [sys.executable, "-m", "pip", "install"]
    if detect_install_method() is InstallMethod.MANAGED_RELEASE:
        from openprogram.programs._host.sources import dependency_directory

        directory = dependency_directory()
        directory.mkdir(parents=True, exist_ok=True)
        # Without --upgrade, pip skips anything already in the target directory.
        pip += ["--target", str(directory), "--upgrade"]
        upgrade = False
    if os.path.isfile(os.path.join(dest, "pyproject.toml")) or os.path.isfile(os.path.join(dest, "setup.py")):
        command = [*pip, *(["--upgrade"] if upgrade else []), dest]
    elif os.path.isfile(os.path.join(dest, "requirements.txt")):
        command = [*pip, "-r", os.path.join(dest, "requirements.txt")]
    else:
        return
    if subprocess.call(command) != 0:
        print(f"[!] {label}: cloned, but installing its declared dependencies failed. "
              f"It may not run until they are present; see its README.")


def _record_application(directory: str, *, replace: bool = False, trust: bool = False) -> dict:
    """Record an application directory in the catalog through the worker."""
    from openprogram.programs._authoring import application_client as applications
    return applications.request("/api/applications/install", method="POST", body={
        "path": os.path.abspath(os.path.expanduser(directory)), "replace": replace, "trust": trust,
    })


def _clone_name(url: str, clone: str) -> str:
    """The directory name a fresh clone gets in the Programs directory."""
    import re
    from openprogram.state.program_directory import program_kinds
    from pathlib import Path
    name = url.rstrip("/").split("/")[-1].split(":")[-1]
    name = name[:-4] if name.endswith(".git") else name
    if "package" in program_kinds(Path(clone)):
        # A package imports under its directory name.
        name = re.sub(r"\W", "_", name).lower()
        if name[:1].isdigit():
            name = "_" + name
    return name


def _install_git(ref: str, *, upgrade: bool = False, replace: bool = False, trust: bool = False) -> None:
    """Clone a git source into ``~/.openprogram/programs/<name>``.

    The clone is inspected before it takes its name: a Python package
    (``__init__.py``) loads as a user package once it is clean, an
    application (``application.json``) is recorded in the catalog, and a
    harness (``<package>/agentics/__init__.py``) is recorded as an
    owner-approved source and gets its declared dependencies installed.
    """
    import shutil
    import subprocess
    import uuid
    from openprogram.state.program_directory import program_kinds, programs_root
    from openprogram.programs._host.sources import record_program_source
    from openprogram.programs._host.registry import _find_python_package

    root = programs_root()
    root.mkdir(parents=True, exist_ok=True)
    url = _normalize_git_ref(ref)
    staging = root / f".install-{uuid.uuid4().hex}"
    print(f"[install] {url}")
    if subprocess.call(["git", "clone", "--depth", "1", url, str(staging)]) != 0:
        shutil.rmtree(staging, ignore_errors=True)
        print(f"[x] git clone of {url} failed.")
        sys.exit(1)
    name = _clone_name(url, str(staging))
    dest = root / name
    if dest.is_symlink():
        # A development link: verify and record it, never touch its target.
        shutil.rmtree(staging, ignore_errors=True)
        if not _find_python_package(str(dest)):
            print(f"[x] {name}: the link at {dest} has no package with agentics/__init__.py; not recorded.")
            sys.exit(1)
        record_program_source(str(dest), source=url, kind="git-symlink", base=str(root))
        print(f"[ok] {name}: recorded the development link at {dest}; its target is unchanged.")
        return
    if dest.exists():
        shutil.rmtree(staging, ignore_errors=True)
        if not (dest / ".git").exists():
            print(f"[x] {name}: {dest} exists but is not a git clone; refusing to touch it.")
            sys.exit(1)
        if not upgrade:
            print(f"[ok] {name}: already in {dest} (re-run with --upgrade to pull the latest).")
        elif subprocess.call(["git", "-C", str(dest), "pull", "--ff-only"]) != 0:
            print(f"[x] {name}: git pull failed.")
            sys.exit(1)
    else:
        replace_file(staging, dest)
        print(f"[ok] cloned into {dest}")
    kinds = program_kinds(dest)
    harness = _find_python_package(str(dest))
    if harness and "package" not in kinds:
        _install_dependencies(str(dest), name, upgrade=upgrade)
        record_program_source(str(dest), source=url, kind="git", base=str(root))
        print(f"[ok] {name}: harness package {os.path.basename(harness)} recorded; it loads on the next scan.")
    if "package" in kinds:
        from openprogram.programs._host.packages import name_problem
        problem = name_problem(name, dest)
        print(f"[!] {name}: {problem}" if problem else
              f"[ok] {name}: package; it loads once `openprogram programs check` has passed.")
    if "application" in kinds:
        try:
            result = _record_application(str(dest), replace=replace or upgrade, trust=trust)
        except (OSError, ValueError) as exc:
            print(f"[!] {name}: application not recorded: {exc}")
        else:
            print(f"[ok] application {result['id']} recorded from {dest}")
    if not kinds and not harness:
        print(f"[!] {name}: neither a package (__init__.py), an application (application.json) "
              f"nor a harness (<package>/agentics/__init__.py); nothing will load from {dest}.")


def _cmd_install(source: str, *, upgrade: bool = False, replace: bool = False, trust: bool = False) -> None:
    """Install a Program from a local directory or a git source."""
    from pathlib import Path
    from openprogram.state.program_directory import program_kinds, programs_root

    local = Path(os.path.expanduser(source))
    if local.is_dir():
        kinds = program_kinds(local)
        if "application" in kinds:
            result = _record_application(str(local), replace=replace, trust=trust)
            print(f"[ok] application {result['id']} recorded; it loads from {local.resolve()}")
            return
        if "package" in kinds:
            if local.resolve().parent == programs_root().resolve():
                print(f"[ok] {local.name} is already in {programs_root()}; "
                      f"`openprogram programs check {local}` makes it callable.")
                return
            if not (local / ".git").exists():
                print(f"[x] {local} is not a Git repository. Commit it first, or move it into "
                      f"{programs_root()} and run `openprogram programs check`.")
                sys.exit(1)
            _install_git(str(local.resolve()), upgrade=upgrade, replace=replace, trust=trust)
            return
        print(f"[x] {local} holds neither __init__.py nor application.json.")
        sys.exit(1)
    if _looks_like_git_ref(source):
        _install_git(source, upgrade=upgrade, replace=replace, trust=trust)
        return
    print(f"Unknown Program source: {source!r}. Give a directory, a git URL or GitHub owner/repo.")
    sys.exit(1)


def _set_aside(path: str) -> str:
    """Move a directory out of the scanned Programs directory, keeping it."""
    import time
    from openprogram.state.program_directory import removed_root
    target = removed_root() / f"{os.path.basename(path)}-{time.strftime('%Y%m%d-%H%M%S')}"
    target.parent.mkdir(parents=True, exist_ok=True)
    replace_file(path, target)
    return str(target)


def _cmd_uninstall(name: str) -> None:
    """Uninstall an application id or a directory in the Programs directory.

    An application is unregistered and keeps its data. A directory in
    ``~/.openprogram/programs/`` is moved to ``.removed/`` beside it, never
    deleted.
    """
    from urllib.parse import quote
    from openprogram.state.program_directory import programs_root
    from openprogram.programs._authoring import application_client as applications
    from openprogram.programs._host.ui import catalog
    from openprogram.programs._host.sources import (
        owner_controlled_program_sources, remove_program_source,
    )

    row = catalog.record(name)
    if row is not None:
        applications.request("/api/applications/" + quote(name, safe=""), method="DELETE")
        print(f"[ok] application {name} unregistered; its data is kept.")
        return
    directory = programs_root() / name
    if name and "/" not in name and not name.startswith(".") and (directory.is_dir() or directory.is_symlink()):
        for record in catalog.records():
            source = record.get("source") or record["application"].get("source", "")
            if not record.get("uninstalled") and os.path.realpath(source) == os.path.realpath(directory):
                app_id = record["application"]["id"]
                applications.request("/api/applications/" + quote(app_id, safe=""), method="DELETE")
                print(f"[ok] application {app_id} unregistered; its data is kept.")
        remove_program_source(str(directory))
        print(f"[ok] {name} moved to {_set_aside(str(directory))}")
        return
    for record in owner_controlled_program_sources():
        if os.path.basename(record["path"]) == name:
            print(f"[x] {name} is a harness recorded at {record['path']}; it is outside "
                  f"{programs_root()}, so remove that checkout yourself.")
            sys.exit(1)
    print(f"Unknown Program: {name!r}. `openprogram programs list` shows what is installed.")
    sys.exit(1)


def _cmd_enable(app_id: str, enabled: bool) -> None:
    """Enable or disable an application, built in or installed: its context
    (UI, resource provider, entries, services) loads or is disposed."""
    from urllib.parse import quote
    from openprogram.programs._authoring import application_client as applications
    from openprogram.programs._host.ui import catalog
    from openprogram.programs._host.registry import TOOL_PROGRAMS, builtin_application_names
    if catalog.record(app_id) is None and app_id not in builtin_application_names():
        from openprogram.programs._host.runtime import get
        if app_id in TOOL_PROGRAMS or get(app_id) is not None:
            raise SystemExit(f"{app_id} is a tool; tools are always given to the Agent and "
                             "cannot be enabled or disabled.")
        raise SystemExit(f"No application {app_id!r}. Only applications can be enabled "
                         "and disabled; a package loads whenever its checkout is clean.")
    result = applications.request("/api/applications/" + quote(app_id, safe=""), method="PATCH",
                                  body={"enabled": enabled})
    print(f"[ok] {app_id} {'enabled' if result.get('enabled') else 'disabled'}.")


def _cmd_run(name, arg_list, provider=None, model=None):
    """Run an existing function."""
    import inspect
    try:
        from openprogram.programs import resolve_function_module
        mod = resolve_function_module(name)
        loaded_func = getattr(mod, name)
    except (ImportError, AttributeError):
        print(f"Error: no Program entry named {name!r}. `openprogram programs list` shows them.")
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

    if provider or model:
        # Agent methods reach the model through the ambient runtime, so the
        # chosen provider must be in scope for the whole call.
        from openprogram.runtime.agent.runtime_scope import execution_scope
        with execution_scope(runtime=_get_runtime(provider, model)):
            result = loaded_func(**kwargs)
    else:
        result = loaded_func(**kwargs)
    print(result)
