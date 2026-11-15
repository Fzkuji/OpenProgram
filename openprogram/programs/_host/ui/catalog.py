"""Application registrations: each records its own source directory.

An application loads from the directory it was installed from; nothing is
copied. The catalog is ``~/.openprogram/programs/catalog.json``; what the
runtime keeps for applications (data, runs, environments, state) is under
``~/.openprogram/programs/.runtime/`` (:func:`home`). ``catalog.json`` keeps, per application, the source directory, the
enabled/hidden/display settings and the manifest as reviewed at install
(its data identity and whether its Python code was trusted). The live
definition — the manifest read from the source plus the content ``digest``
of the source tree — is computed when the application loads (see
``loader``) and again on every reload.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import site
import subprocess
import sys
import uuid

from . import catalog_store
from openprogram.host._platform.filesystem import replace_file

ID = re.compile(r"[a-z][a-z0-9._-]{0,95}\Z")
ICON = re.compile(r"[a-z][a-z0-9-]{0,39}\Z")
CAPABILITIES = {"model.invoke", "storage.app", "files.project.read", "files.project.write"}
#: Capabilities that need the owner's explicit trust at install, besides a Python backend.
TRUSTED_CAPABILITIES = {"files.project.write"}
#: Directories that are never part of an application's content.
IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".venv"}
SETTINGS = ("enabled", "hidden", "display_title")


def home() -> Path:
    """What the application runtime keeps: data, runs, environments, state."""
    from openprogram.state.program_directory import runtime_root
    return runtime_root()


def contained(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative or Path(relative).is_absolute():
        raise ValueError("expected a relative package path")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("path escapes application directory")
    return path


def write_project_file(project: str | Path, relative: str, content: str | bytes) -> dict:
    """Write ``content`` to ``relative`` inside the project, atomically.

    Refuses an absolute path, a path that resolves (symbolic links
    included) outside the project root, and any path into ``.git``.
    The file is written beside its target and renamed over it, so a
    reader never sees a partial file.
    """
    root = Path(project).resolve()
    target = contained(root, relative)
    parts = [part.lower() for part in (*Path(relative).parts, *target.relative_to(root).parts)]
    if ".git" in parts:
        raise PermissionError("applications cannot write into .git")
    if target == root or target.is_dir():
        raise ValueError("expected a file path inside the project")
    if isinstance(content, str):
        content = content.encode()
    if not isinstance(content, bytes):
        raise TypeError("file content must be text or bytes")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.parent.resolve().is_relative_to(root):
        raise ValueError("path escapes project directory")
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(content)
        replace_file(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return {"path": target.relative_to(root).as_posix(), "bytes": len(content)}


def manifest(root: Path) -> dict:
    raw = json.loads((root / "application.json").read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not ID.fullmatch(str(raw.get("id", ""))):
        raise ValueError("invalid application id")
    for key in ("title", "version"):
        if not isinstance(raw.get(key), str) or not 0 < len(raw[key]) <= 160:
            raise ValueError(f"invalid {key}")
    # A glyph name the host draws from its own icon set; packages never
    # ship image files for it. Unknown names fall back to the generic glyph.
    if "icon" in raw and not (isinstance(raw["icon"], str) and ICON.fullmatch(raw["icon"])):
        raise ValueError("icon must be a lowercase glyph name such as 'calculator'")
    # One plain sentence the Launchpad, Applications page and Abilities show.
    if "description" in raw and not (
            isinstance(raw["description"], str) and 0 < len(raw["description"].strip()) <= 300
            and "\n" not in raw["description"]):
        raise ValueError("description must be one line of 1–300 characters")
    raw.setdefault("scope", "global")
    raw.setdefault("capabilities", [])
    raw.setdefault("dataSchema", 1)
    raw.setdefault("operations", {})
    if raw["scope"] not in {"global", "project"}:
        raise ValueError("scope must be global or project")
    if not isinstance(raw["capabilities"], list) or any(not isinstance(x, str) or x not in CAPABILITIES for x in raw["capabilities"]):
        raise ValueError("unsupported application capability")
    if type(raw["dataSchema"]) is not int or raw["dataSchema"] < 1:
        raise ValueError("invalid dataSchema")
    ui = raw.setdefault("ui", {})
    if not isinstance(ui, dict):
        raise ValueError("ui must be an object")
    ui.setdefault("root", ".")
    ui.setdefault("entry", "index.html")
    entry = contained(contained(root, ui.get("root", ".")), ui.get("entry", "index.html"))
    if not entry.is_file() or entry.suffix.lower() != ".html":
        raise ValueError("ui.entry must be an existing HTML file")
    if not isinstance(raw["operations"], dict):
        raise ValueError("operations must be an object")
    from jsonschema import Draft202012Validator
    for name, operation in raw["operations"].items():
        if not ID.fullmatch(name) or not isinstance(operation, dict):
            raise ValueError("invalid operation")
        for field in ("input", "output"):
            Draft202012Validator.check_schema(operation.get(field, {}))
    backend = raw.get("backend")
    if backend:
        if not isinstance(backend, dict):
            raise ValueError("backend must be an object")
        if backend.get("kind") != "python":
            raise ValueError("only Python backends are supported")
        entrypoint = backend.get("entry", "")
        if not re.fullmatch(r"[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*:[a-zA-Z_]\w*", entrypoint):
            raise ValueError("backend.entry must be module:object")
        module = entrypoint.split(":")[0].replace(".", "/")
        if not (contained(root, module + ".py").is_file() or contained(root, module + "/__init__.py").is_file()):
            raise ValueError("backend module is missing")
        deps = backend.get("dependencies", [])
        if not isinstance(deps, list) or any(not isinstance(d, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?==[A-Za-z0-9_.+-]+", d) for d in deps):
            raise ValueError("backend dependencies must be exact name==version requirements")
    elif raw["operations"]:
        raise ValueError("operations require a backend")
    elif TRUSTED_CAPABILITIES & set(raw["capabilities"]):
        raise ValueError("files.project.write requires a Python backend")
    return raw


def needs_trust(definition: dict) -> bool:
    """Whether running this package needs the owner's explicit trust."""
    return bool(definition.get("backend")) or bool(TRUSTED_CAPABILITIES & set(definition.get("capabilities", ())))


def trust_prompt(definition: dict) -> str:
    """The refusal shown when trust is missing, naming what it authorizes."""
    reasons = ["Python backends run trusted local code"]
    if "files.project.write" in definition.get("capabilities", ()):
        reasons.append("files.project.write lets it create and replace files in the bound project")
    return "; ".join(reasons) + "; pass trust=true after reviewing the source"


def _walk(root: Path):
    """Every content file of an application tree, in a stable order."""
    for folder, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS)
        for name in dirs + files:
            if (Path(folder) / name).is_symlink():
                raise ValueError("application packages cannot contain symlinks")
        for name in sorted(files):
            if name.endswith((".pyc", ".pyo")):
                continue
            yield Path(folder) / name


def check_tree(root: Path) -> None:
    """Refuse a package with symlinks, special files or more than 100 MiB."""
    size = 0
    for path in _walk(root):
        if not path.is_file():
            raise ValueError("application package contains a non-regular file")
        size += path.stat().st_size
        if size > 100 * 1024 * 1024:
            raise ValueError("application package exceeds 100 MiB")


def package_digest(root: Path) -> str:
    """The content hash of an application tree (ignored directories excluded)."""
    digest = hashlib.sha256()
    for path in sorted(_walk(root), key=lambda p: p.relative_to(root).as_posix()):
        if path.is_file():
            content = path.read_bytes()
            digest.update(json.dumps([path.relative_to(root).as_posix(), len(content)]).encode())
            digest.update(content)
    return digest.hexdigest()


def tree_stamp(root: Path) -> tuple:
    """A cheap change marker of an application tree: paths, sizes and mtimes."""
    try:
        return tuple((p.relative_to(root).as_posix(), p.stat().st_size, p.stat().st_mtime_ns) for p in _walk(root))
    except (OSError, ValueError):
        return ()


# registrations

def records() -> list[dict]:
    """Every application row of the catalog, uninstalled ones included."""
    return [r for r in catalog_store.read()
            if r.get("kind") == "application" and isinstance(r.get("application"), dict)]


def record(app_id: str) -> dict | None:
    """The installed (not uninstalled) row of ``app_id``."""
    return next((r for r in records() if not r.get("uninstalled") and r["application"].get("id") == app_id), None)


def source_root(row: dict) -> Path:
    """Where a registered application loads from.

    Its ``source`` directory. A registration made before applications
    loaded from their source points ``path`` at a copied version; that
    copy is used only while the source no longer holds this application
    (the directory is gone, or now holds another application id).
    """
    source = row.get("source") or row["application"].get("source")
    candidates = [Path(p) for p in dict.fromkeys((source, row.get("path"))) if p]
    found = [p for p in candidates if (p / "application.json").is_file()]
    for root in found:
        try:
            if json.loads((root / "application.json").read_text(encoding="utf-8")).get("id") == row["application"].get("id"):
                return root
        except (OSError, ValueError, AttributeError):
            continue
    if found:
        return found[0]
    raise FileNotFoundError(f"application source {source or row.get('path')} is missing")


def trusted(row: dict) -> bool:
    """Whether the owner trusted this registration's code at install."""
    reviewed = row["application"]
    return bool(reviewed.get("trusted")) or bool(reviewed.get("backend"))


def resolve(root: Path, row: dict | None = None) -> dict:
    """The live definition of the package at ``root``: manifest and digest.

    With a registration, the package must keep the data identity
    (``dataSchema``, ``scope``) it was installed with, and must not need
    trust it was installed without.
    """
    definition = manifest(root)
    if row is not None:
        reviewed = row["application"]
        if definition["id"] != reviewed.get("id"):
            raise ValueError("application id changed in its source; install it as a new application")
        if definition["dataSchema"] != reviewed.get("dataSchema", 1) or definition["scope"] != reviewed.get("scope", "global"):
            raise ValueError("dataSchema/scope changes require an explicit migration; not loaded")
        if needs_trust(definition) and not trusted(row):
            raise ValueError("the source now needs trust (" + trust_prompt(definition) + "); reinstall it")
    check_tree(root)
    return {**definition, "digest": package_digest(root), "path": str(root)}


def _with_settings(definition: dict, row: dict | None) -> dict:
    if row is None:
        return dict(definition)
    reviewed = row["application"]
    value = {**definition, "source": row.get("source") or reviewed.get("source", definition.get("path", ""))}
    for key in SETTINGS:
        if key in reviewed:
            value[key] = reviewed[key]
    return value


def installed() -> list[dict]:
    """Every installed application as it currently loads, for listings.

    A loaded application shows the definition it loaded with; one that is
    not loaded (disabled, or listed outside the worker) is read from its
    source. One whose source no longer loads shows its registration with
    the ``error``.
    """
    from . import loader
    result = []
    for row in records():
        if row.get("uninstalled"):
            continue
        app_id = row["application"]["id"]
        definition = loader.loaded(app_id, row)
        if definition is None:
            try:
                definition = resolve(source_root(row), row)
            except (OSError, ValueError) as exc:
                definition = {**row["application"], "path": row.get("path", ""), "error": str(exc)}
            else:
                if loader.error(app_id):
                    definition["error"] = loader.error(app_id)
        result.append(_with_settings(definition, row))
    result.extend(_with_builtin_settings(definition) for definition in loader.builtin_definitions())
    return result


def _with_builtin_settings(definition: dict) -> dict:
    settings = builtin_settings(definition["id"])
    shown = {key: settings[key] for key in ("hidden", "display_title") if key in settings}
    return dict(definition, **shown, builtin=True, enabled=bool(settings.get("enabled", True)))


def get(app_id: str, *, enabled: bool = True) -> dict:
    """An installed application's definition.

    ``enabled`` (the default) requires it to be enabled and loaded: its
    definition is the one it loaded with, digest included.
    """
    from . import loader
    builtin = loader.builtin(app_id)
    if builtin is not None:
        return _with_builtin_settings(builtin)
    row = record(app_id)
    if row is not None:
        definition = loader.loaded(app_id, row)
        if definition is not None:
            return _with_settings(definition, row)
        if not enabled:
            return _with_settings({**row["application"], "path": row.get("path", "")}, row)
    raise FileNotFoundError("application is not installed or enabled")


# Python environments

def base_interpreter() -> str:
    """The real Python behind this worker, to seed application environments.

    A worker can run as a named launcher (``OpenProgram``, ``OpenProgram
    Dev``) that cannot seed one: ``venv`` copies the launcher and its pip
    bootstrap fails. A verified release runtime names its Python in its
    manifest; any other runtime, a development one included, is found from
    ``sys.base_prefix``."""
    from openprogram.operations.self_update.github_release.detect import managed_runtime_root
    runtime = managed_runtime_root()
    if runtime is not None:
        runtime_manifest = json.loads((runtime / "runtime-manifest.json").read_text(encoding="utf-8"))
        return str((runtime / runtime_manifest["python"]).resolve())
    base = Path(sys.base_prefix)
    candidate = base / "python.exe" if os.name == "nt" else base / "bin" / f"python{sys.version_info[0]}.{sys.version_info[1]}"
    return str(candidate) if candidate.is_file() else sys.executable


def environment_key(definition: dict) -> str:
    """One Python environment per application and dependency set.

    Editing the source does not need a new environment; changing the
    pinned dependencies does.
    """
    dependencies = sorted((definition.get("backend") or {}).get("dependencies", []))
    return hashlib.sha256(json.dumps([definition["id"], dependencies]).encode()).hexdigest()


def python_path(key: str) -> Path:
    return home() / "environments" / key / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def write_host_path_file(location: Path, paths: list[str]) -> Path:
    """Tell an application environment where the host's packages are.

    The reader of a ``.pth`` file is the interpreter's ``site`` module, not
    this product. Python 3.11 decodes it in the locale encoding only; later
    versions try UTF-8 and then the locale. The locale encoding is the one
    every supported version reads, so a host path with a non-ASCII user name
    survives on a GBK or cp1252 Windows.
    """
    target = location / "openprogram-host.pth"
    target.write_text("\n".join(paths) + "\n", encoding="locale")
    return target


def ensure_environment(definition: dict) -> Path:
    """The backend's Python, creating its environment on first use."""
    key = environment_key(definition)
    executable = python_path(key)
    environment = executable.parent.parent
    ready = environment / ".openprogram-ready"
    if ready.exists():
        return executable
    if environment.exists():
        shutil.rmtree(environment)
    environment.parent.mkdir(parents=True, exist_ok=True)
    # The base environment is read-only; application dependencies
    # install only into this application's own environment.
    try:
        subprocess.run(
            [base_interpreter(), "-m", "venv", "--system-site-packages", str(environment)],
            check=True, capture_output=True, timeout=300,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ValueError("application Python environment creation failed") from exc
    # Inherit the host framework's dependency locations after this
    # application's own site-packages, including a host virtualenv.
    sites = list(environment.glob("lib/python*/site-packages"))
    if os.name == "nt":
        sites = [environment / "Lib" / "site-packages"]
    for location in sites:
        write_host_path_file(location, site.getsitepackages())
    dependencies = definition["backend"].get("dependencies", [])
    if dependencies:
        try:
            subprocess.run([str(executable), "-m", "pip", "install", "--disable-pip-version-check", *dependencies],
                           check=True, capture_output=True, timeout=300)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise ValueError("application dependency installation failed") from exc
    ready.write_text(key, encoding="utf-8")
    return executable


def check_backend(root: Path, definition: dict) -> None:
    """Import the backend in its own environment and find every operation."""
    executable = ensure_environment(definition)
    probe = (
        "import importlib,json,sys; sys.dont_write_bytecode=True; "
        "sys.path.insert(0,sys.argv[1]); "
        "m,n=sys.argv[2].split(':'); ops=getattr(importlib.import_module(m),n); "
        "assert isinstance(ops,dict), 'backend entry must be an operation mapping'; "
        "assert all(callable(ops.get(k)) for k in json.loads(sys.argv[3])), 'declared operation missing or not callable'"
    )
    import openprogram
    probe_env = dict(os.environ)
    probe_env["PYTHONPATH"] = str(Path(openprogram.__file__).resolve().parent.parent)
    probe_env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        subprocess.run([str(executable), "-c", probe, str(root), definition["backend"]["entry"], json.dumps(list(definition["operations"]))],
                       check=True, capture_output=True, timeout=30, env=probe_env, cwd=str(root))
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ValueError("application backend failed its import/operation check") from exc


# install, remove, configure

def install(path: str, *, replace: bool = False, trust: bool = False) -> dict:
    """Record an application directory; it loads from there, nothing is copied."""
    source = Path(path).expanduser().resolve(strict=True)
    from openprogram.state.program_directory import removed_root
    if not source.is_dir() or any(source.is_relative_to(p.resolve()) for p in (home(), removed_root())):
        raise ValueError("select an application source directory outside managed storage")
    definition = manifest(source)
    if needs_trust(definition) and not trust:
        raise ValueError(trust_prompt(definition))
    from . import loader
    if definition["id"] in loader.builtin_names():
        raise ValueError("application id belongs to a built-in application")
    check_tree(source)
    digest = package_digest(source)
    if definition.get("backend"):
        check_backend(source, definition)
        if package_digest(source) != digest:
            raise ValueError("application backend changed its package during validation")
    result = {**definition, "digest": digest, "enabled": True, "source": str(source)}
    if needs_trust(definition):
        result["trusted"] = True

    def activate(rows):
        previous = next((r["application"] for r in rows if r.get("kind") == "application" and r.get("application", {}).get("id") == definition["id"]), None)
        if previous:
            if previous.get("source") != str(source):
                raise ValueError("application id belongs to a different source")
            if previous.get("digest") != digest and not replace:
                raise ValueError("application already installed; use replace")
            if previous.get("dataSchema") != definition["dataSchema"] or previous.get("scope") != definition["scope"]:
                raise ValueError("dataSchema/scope changes require an explicit migration; upgrade not activated")
        kept = [r for r in rows if not (r.get("kind") == "application" and r.get("application", {}).get("id") == definition["id"])]
        # ``path`` stays populated for readers of the older layout, where
        # it named the copied version; it is the source directory now.
        kept.append({"kind": "application", "path": str(source), "source": str(source), "application": result})
        catalog_store.write(kept)

    catalog_store.update(activate)
    return result


def adopt_user_applications() -> list[str]:
    """Record the application directories placed in ``~/.openprogram/programs/``.

    A directory there with ``application.json`` and no catalog row (an
    uninstalled row counts: uninstall is not undone behind the owner's
    back) is installed as if by ``openprogram programs install``. One
    that needs trust (a Python backend, ``files.project.write``) is left
    for an explicit ``install --trust``. Returns the ids recorded.
    """
    import logging
    from openprogram.state.program_directory import program_dirs
    known = set()
    for row in records():
        for value in (row.get("source"), row["application"].get("source")):
            if value:
                known.add(os.path.realpath(value))
    adopted = []
    for directory in program_dirs().values():
        if not (directory / "application.json").is_file() or os.path.realpath(directory) in known:
            continue
        try:
            definition = manifest(directory)
            if needs_trust(definition):
                continue
            adopted.append(install(str(directory))["id"])
        except (OSError, ValueError) as exc:
            logging.getLogger(__name__).warning("application in %s was not recorded: %s", directory, exc)
    return adopted


def remove(app_id: str) -> None:
    get(app_id, enabled=False)
    def mutate(rows):
        for row in rows:
            if row.get("kind") == "application" and row.get("application", {}).get("id") == app_id:
                # Keep source/schema/scope identity beside retained business
                # data. Reinstall is subject to the same migration check.
                row["uninstalled"] = True
                row["application"]["enabled"] = False
        catalog_store.write(rows)
    catalog_store.update(mutate)


#: The catalog row kind holding a built-in application's settings.
BUILTIN_KIND = "builtin-application"


def builtin_settings(name: str) -> dict:
    """The settings saved for the built-in application ``name`` (``{}`` when none).

    Read without the catalog lock: every process imports the registry,
    which asks this for each built-in, and the catalog is replaced
    atomically.
    """
    try:
        rows = catalog_store._read()
    except (OSError, ValueError):
        return {}
    row = next((r for r in rows if r.get("kind") == BUILTIN_KIND
                and r["application"].get("id") == name), None)
    return dict(row["application"]) if row else {}


def builtin_enabled(name: str) -> bool:
    """Whether the built-in application ``name`` loads; built-ins start enabled."""
    return bool(builtin_settings(name).get("enabled", True))


def _configure_builtin(app_id: str, changes: dict) -> dict:
    result: dict = {}

    def mutate(rows):
        row = next((r for r in rows if r.get("kind") == BUILTIN_KIND
                    and r["application"].get("id") == app_id), None)
        if row is None:
            row = {"kind": BUILTIN_KIND, "application": {"id": app_id}}
            rows.append(row)
        row["application"].update(changes)
        result.update({"enabled": True, **row["application"], "builtin": True})
        catalog_store.write(rows)
    catalog_store.update(mutate)
    return result


def configure(app_id: str, **changes) -> dict:
    """Change an application's settings: an installed one's catalog row, or
    the settings row of a built-in application under ``programs/applications/``."""
    allowed = set(SETTINGS)
    if set(changes) - allowed:
        raise ValueError("unsupported application setting")
    for key, value in changes.items():
        if key in {"enabled", "hidden"} and type(value) is not bool:
            raise ValueError(f"{key} must be boolean")
        if key == "display_title" and (not isinstance(value, str) or not 1 <= len(value) <= 160):
            raise ValueError("display_title must contain 1–160 characters")
    from . import loader
    if app_id in loader.builtin_names():
        return _configure_builtin(app_id, changes)
    result = {}
    def mutate(rows):
        for row in rows:
            if row.get("kind") == "application" and not row.get("uninstalled") and row.get("application", {}).get("id") == app_id:
                row["application"].update(changes)
                result.update(row["application"])
        if not result:
            raise FileNotFoundError(app_id)
        catalog_store.write(rows)
    catalog_store.update(mutate)
    return result
