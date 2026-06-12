"""``openprogram programs`` + provider-config wizard handlers."""
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
            with open(filepath, encoding="utf-8") as f:
                content = f.read()
            if '"""' in content:
                start = content.index('"""') + 3
                end = content.index('"""', start)
                desc = content[start:end].strip().split("\n")[0]
        except (OSError, UnicodeDecodeError):
            pass
        entries.append((name, desc))

    if not entries:
        print("No functions registered.")
        return

    print(f"Functions ({len(entries)}):\n")
    for name, desc in sorted(entries):
        print(f"  {name:24s}  {desc}")

    # Also surface the optional first-party *programs* (gui / research /
    # wiki agents) and whether each is installed on this machine.
    _print_programs_status()


# ---------------------------------------------------------------------------
# Programs (optional pip-installable agentic harnesses)
# ---------------------------------------------------------------------------

def _print_programs_status() -> None:
    """Print the install status of every catalogued program, then any
    third-party harnesses found in functions/agentics/."""
    from openprogram.functions._programs import KNOWN_PROGRAMS

    print(f"\nPrograms ({len(KNOWN_PROGRAMS)}):\n")
    for p in KNOWN_PROGRAMS:
        if p.is_installed():
            status = "installed"
        elif not p.public:
            status = "not yet published"
        else:
            status = f"available — openprogram programs install {p.extra}"
        print(f"  {p.function:24s}  [{status}]")
        print(f"  {'':24s}  {p.summary}")

    third_party = list(_iter_third_party_harnesses())
    if third_party:
        print(f"\nThird-party harnesses ({len(third_party)}):\n")
        for name, pkg in third_party:
            status = "ok" if pkg else "package contract NOT satisfied"
            print(f"  {name:24s}  [{status}]"
                  + (f"  (package: {os.path.basename(pkg)})" if pkg else ""))


def _iter_third_party_harnesses():
    """Yield (dir_name, package_dir_or_None) for every non-first-party
    harness directory (or dev symlink) under functions/agentics/."""
    from openprogram.functions._programs import KNOWN_PROGRAMS, agentics_dir
    from openprogram.functions._registry import (
        _NOT_A_HARNESS, _find_python_package,
    )

    base = agentics_dir()
    if not base or not os.path.isdir(base):
        return
    first_party = {p.repo_dir_name for p in KNOWN_PROGRAMS}
    for name in sorted(os.listdir(base)):
        if name.startswith(".") or name in _NOT_A_HARNESS or name in first_party:
            continue
        path = os.path.join(base, name)
        target = os.path.realpath(path)
        if not os.path.isdir(target):
            continue
        if not (os.path.isdir(os.path.join(target, ".git"))
                or os.path.islink(path)):
            # Bundled single-function dirs (ask_user, ...) are tracked
            # packages, not harness clones — a harness arrives as a git
            # clone or a dev symlink.
            continue
        yield name, _find_python_package(target)


def _cmd_programs_available() -> None:
    """List installable programs with one-line install instructions."""
    _print_programs_status()


def _resolve_programs(name: str):
    """Resolve a program selector ('gui'/'research'/'wiki'/'all') to a list."""
    from openprogram.functions._programs import KNOWN_PROGRAMS, get_program

    if name in ("all", "*"):
        return list(KNOWN_PROGRAMS)
    prog = get_program(name)
    return [prog] if prog else []


def _preinstall_cpu_torch_if_no_gpu() -> None:
    """On a GPU-less Linux box, preinstall the CPU torch wheel.

    PyPI's default Linux ``torch`` wheel bundles the full NVIDIA CUDA
    stack (~3 GB) — pure waste on a machine with no NVIDIA GPU. Pulling
    the CPU wheel (~200 MB) from PyTorch's own index FIRST means the
    harness's ``ultralytics`` dependency resolves against it instead of
    dragging in the CUDA build. macOS/Windows default wheels are already
    CUDA-free; a box WITH a GPU (nvidia-smi present) keeps pip's default
    CUDA wheel; an already-installed torch is never touched.
    """
    import importlib.util
    import shutil
    import subprocess
    if sys.platform != "linux":
        return
    if shutil.which("nvidia-smi"):
        return
    try:
        if importlib.util.find_spec("torch") is not None:
            return
    except (ImportError, ValueError):
        pass
    print("[deps] no NVIDIA GPU detected — preinstalling CPU torch "
          "(~200 MB) instead of the default CUDA build (~3 GB).")
    rc = subprocess.call([
        sys.executable, "-m", "pip", "install", "torch", "torchvision",
        "--index-url", "https://download.pytorch.org/whl/cpu",
    ])
    if rc != 0:
        print("[!] CPU torch preinstall failed — pip will fall back to "
              "the default (CUDA) wheel.")


def _looks_like_git_ref(name: str) -> bool:
    """True when the install selector is a git source rather than a
    first-party program name: a full URL (https/ssh/git/file) or a
    GitHub ``owner/repo`` shorthand."""
    if name.startswith(("http://", "https://", "git@", "ssh://", "file://")):
        return True
    return name.count("/") == 1 and " " not in name and not name.startswith("/")


def _normalize_git_ref(ref: str) -> str:
    """Expand ``owner/repo`` shorthand to a GitHub HTTPS URL."""
    if ref.startswith(("http://", "https://", "git@", "ssh://", "file://")):
        return ref
    return f"https://github.com/{ref}"


def _install_third_party(ref: str, *, upgrade: bool = False) -> None:
    """Install ANY harness repo by URL — same flow as first-party programs.

    Clone into ``functions/agentics/<Repo-Name>/``, install the repo's
    own declared deps (its pyproject/setup.py, else requirements.txt),
    then verify the harness contract (an importable package exposing
    ``<pkg>/agentics/__init__.py`` — see docs/installing-harnesses.md).
    The clone auto-registers on next launch via directory discovery; no
    catalogue edit is needed.
    """
    import subprocess
    from openprogram.functions._programs import agentics_dir
    from openprogram.functions._registry import _find_python_package

    base = agentics_dir()
    if not base or not os.path.isdir(base):
        print(f"Cannot locate functions/agentics directory ({base!r}).")
        sys.exit(1)

    url = _normalize_git_ref(ref)
    repo_name = url.rstrip("/").split("/")[-1]
    if repo_name.endswith(".git"):
        repo_name = repo_name[:-4]
    dest = os.path.join(base, repo_name)

    if os.path.islink(dest):
        print(f"[skip] {repo_name}: {dest} is a dev symlink — managed by "
              f"you, not the installer. Remove it first to install a clone.")
        return
    already = os.path.isdir(os.path.join(dest, ".git"))
    if os.path.isdir(dest) and not already:
        print(f"[x] {repo_name}: {dest} exists but is not a git clone — "
              f"refusing to touch it.")
        sys.exit(1)

    if already and not upgrade:
        print(f"[ok] {repo_name}: already cloned at {dest} "
              f"(re-run with --upgrade to pull latest).")
    elif already and upgrade:
        print(f"\n[upgrade] {repo_name} <- {url}")
        rc = subprocess.call(["git", "-C", dest, "pull", "--ff-only"])
        if rc != 0:
            print(f"[x] {repo_name}: git pull failed (exit {rc}).")
            sys.exit(1)
    else:
        print(f"\n[install] {repo_name} -> {dest}")
        rc = subprocess.call(
            ["git", "clone", "--depth", "1", url, dest])
        if rc != 0:
            print(f"[x] {repo_name}: git clone failed (exit {rc}).")
            sys.exit(1)

    # The harness is self-describing: prefer its pyproject/setup.py,
    # fall back to requirements.txt. (Same rule as first-party — and the
    # harness must NOT declare openprogram itself; see the contract.)
    if os.path.isfile(os.path.join(dest, "pyproject.toml")) \
            or os.path.isfile(os.path.join(dest, "setup.py")):
        dep_cmd = [sys.executable, "-m", "pip", "install"]
        if upgrade:
            dep_cmd.append("--upgrade")
        dep_cmd.append(dest)
        if subprocess.call(dep_cmd) != 0:
            print(f"[!] {repo_name}: cloned but installing its declared "
                  f"dependencies failed. It may not run until they're "
                  f"present — see the harness README.")
    elif os.path.isfile(os.path.join(dest, "requirements.txt")):
        if subprocess.call([
            sys.executable, "-m", "pip", "install", "-r",
            os.path.join(dest, "requirements.txt"),
        ]) != 0:
            print(f"[!] {repo_name}: cloned but requirements.txt install "
                  f"failed — see the harness README.")

    pkg = _find_python_package(dest)
    if pkg:
        print(f"[ok] {repo_name} installed at {dest} "
              f"(package: {os.path.basename(pkg)}). "
              f"It will register on next launch.")
    else:
        print(f"[!] {repo_name}: cloned, but no package with an "
              f"agentics/__init__.py was found — it will NOT register. "
              f"The harness contract is described in "
              f"docs/installing-harnesses.md.")


def _cmd_install(name: str, *, upgrade: bool = False) -> None:
    """Install one (or all) program(s) by cloning into functions/agentics/.

    Each program is ``git clone``-d into
    ``openprogram/functions/agentics/<Repo-Name>/`` as a real, editable
    directory (no symlinks, no site-packages). Heavy programs (gui) also
    pull their native deps from the matching ``openprogram[<extra>]``
    group. The clone is git-ignored by the parent repo, so it stays an
    independent checkout you can ``git pull`` / edit in place.

    Third-party harnesses install the same way by git source:
    ``openprogram programs install https://github.com/owner/Some-Harness``
    (or the ``owner/Some-Harness`` shorthand).
    """
    import subprocess
    from openprogram.functions._programs import agentics_dir

    if _looks_like_git_ref(name):
        _install_third_party(name, upgrade=upgrade)
        return

    progs = _resolve_programs(name)
    if not progs:
        from openprogram.functions._programs import KNOWN_PROGRAMS
        opts = ", ".join(p.extra for p in KNOWN_PROGRAMS) + ", all"
        print(f"Unknown program: {name!r}. Choices: {opts}, "
              f"or a git URL / owner/repo for third-party harnesses.")
        sys.exit(1)

    base = agentics_dir()
    if not base or not os.path.isdir(base):
        print(f"Cannot locate functions/agentics directory ({base!r}).")
        sys.exit(1)

    for prog in progs:
        if not prog.public:
            print(f"[skip] {prog.function}: {prog.repo} is not published yet. "
                  f"Skipping.")
            continue

        dest = prog.clone_dir(base)
        already = os.path.isdir(os.path.join(dest, ".git"))

        if already and not upgrade:
            print(f"[ok] {prog.function}: already cloned at {dest} "
                  f"(re-run with --upgrade to pull latest).")
        else:
            if prog.heavy:
                print(f"\n[install] {prog.function} -> {dest}\n  heavy: also "
                      f"pulls large deps (torch via ultralytics). May take a while.")
            else:
                print(f"\n[install] {prog.function} -> {dest}")

            if already and upgrade:
                rc = subprocess.call(["git", "-C", dest, "pull", "--ff-only"])
            else:
                rc = subprocess.call([
                    "git", "clone", "--depth", "1", "--branch", prog.branch,
                    prog.git_url(), dest,
                ])
            if rc != 0:
                print(f"[x] {prog.function}: git clone/pull failed (exit {rc}).")
                continue

        # Install the harness's OWN declared dependencies — the harness
        # is self-describing (its pyproject.toml / setup.py lists what it
        # needs). OpenProgram does NOT carry per-harness deps; each
        # harness owns its dependency list. So we ``pip install`` the
        # clone itself, which resolves whatever it declares (cv2, torch,
        # … for GUI; nothing extra for the light ones).
        if prog.heavy:
            _preinstall_cpu_torch_if_no_gpu()
        has_pyproject = os.path.isfile(os.path.join(dest, "pyproject.toml")) \
            or os.path.isfile(os.path.join(dest, "setup.py"))
        if has_pyproject:
            dep_cmd = [sys.executable, "-m", "pip", "install"]
            if upgrade:
                dep_cmd.append("--upgrade")
            # Non-editable: deps land in site-packages, the harness code
            # still runs from the in-tree clone (which is on sys.path via
            # the registry loader). ``--no-deps`` is NOT passed — we want
            # the harness's declared deps.
            dep_cmd.append(dest)
            rc = subprocess.call(dep_cmd)
            if rc != 0:
                print(f"[!] {prog.function}: cloned but installing its "
                      f"dependencies failed (exit {rc}). It may not run "
                      f"until they're present — see the harness README.")
                continue

        # Confirm the package imports (registers the function).
        if prog.in_tree_pkg_dir(base):
            print(f"[ok] {prog.function} installed at {dest}. "
                  f"It will appear on next launch.")
        else:
            print(f"[!] {prog.function}: cloned but package '{prog.package}' "
                  f"not found under {dest} — check the repo layout.")


def _cmd_uninstall(name: str) -> None:
    """Uninstall one (or all) program(s) — remove the in-tree clone.

    Third-party harnesses are addressed by their clone-dir name
    (``openprogram programs uninstall Some-Harness``)."""
    import shutil
    from openprogram.functions._programs import agentics_dir

    progs = _resolve_programs(name)
    if not progs:
        # Third-party harness: address by clone-dir name under agentics/.
        third_party = dict(_iter_third_party_harnesses())
        if name in third_party:
            base = agentics_dir()
            dest = os.path.join(base, name)
            if os.path.islink(dest):
                os.unlink(dest)
                print(f"[ok] {name}: dev symlink removed ({dest}); "
                      f"the linked checkout itself is untouched.")
            else:
                try:
                    shutil.rmtree(dest)
                    print(f"[ok] {name} removed ({dest}).")
                except OSError as e:
                    print(f"[x] {name}: could not remove {dest}: {e}")
            return
        print(f"Unknown program: {name!r}. First-party: gui/research/wiki; "
              f"third-party harnesses by clone-dir name "
              f"(see `openprogram programs available`).")
        sys.exit(1)
    base = agentics_dir()
    for prog in progs:
        dest = prog.clone_dir(base)
        if not (dest and os.path.isdir(dest)):
            print(f"[skip] {prog.function}: not installed.")
            continue
        try:
            shutil.rmtree(dest)
            print(f"[ok] {prog.function} removed ({dest}).")
        except OSError as e:
            print(f"[x] {prog.function}: could not remove {dest}: {e}")


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
