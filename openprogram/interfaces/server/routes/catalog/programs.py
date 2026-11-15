"""``/api/programs/*``: the Abilities explorer and runtime program detection.

The explorer has two roots:

  * ``tools/<category>/<package>[/<entry>]``: the built-in tools under
    ``openprogram/programs/tools/``, grouped by their subdirectories.
  * ``applications/<name>[/<entry or source file>]``: every application,
    the built-in ones under ``openprogram/programs/applications/`` and the
    installed ones in the user's Programs directory (user packages,
    catalogued applications and recorded harness clones).

``GET /api/programs/explorer`` lists one level lazily, ``GET
/api/programs/logic`` analyses one entry's calls from its source, and
``POST /api/programs/refresh`` re-runs discovery so a program installed
after boot goes live without restarting the worker. If anything new
registered, it broadcasts ``programs:changed`` so every connected UI
re-fetches ``/api/programs``. The background watcher
(``programs._host.watcher``) uses the same core (``registry.rescan``) and
the same broadcast.
"""
from __future__ import annotations

import logging
import ast
import dataclasses
import importlib.util
import inspect
import shlex
import json as _json
import os
from collections import deque
from pathlib import Path

import openprogram
from fastapi.responses import JSONResponse

from .programs_cache import CatalogCache, parsed_source, request_memo, watch

_log = logging.getLogger(__name__)


PROGRAMS_ROOT = Path(openprogram.__file__).resolve().parent / "programs"
_IGNORED = {
    ".git", ".pytest_cache", "__pycache__", "node_modules", "build", "dist",
    "output", "runs", ".venv", "venv",
}
_ROOTS = ("tools", "applications")
_MAX_SOURCE_FILES = 200
_MAX_SOURCE_BYTES = 1_000_000
_AGENTIC_PRIMITIVES = ("agent", "llm")
_PRIMITIVE_DEPENDENCIES = {
    "agent": ("llm",),
    "llm": (),
}
_TOOLS = Path("tools")
_TOOL_MODULE_PREFIX = "openprogram.programs.tools."
_BUILTIN_APPLICATION_MODULE = "openprogram.programs.applications"
# Helpers and session adapters inside program packages that are not
# runnable Programs. They stay importable; they do not become explorer rows.
_INTERNAL_NAMES = {
    "ask_user",
    "errors",
    "json_parsing",
    "roles",
}


# ---------------------------------------------------------------------------
# Applications: built in and installed
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class _Application:
    """One row under ``applications/``.

    ``form`` says what the source directory holds: ``package`` (a Python
    package with ``PROGRAM_ENTRIES``), ``application`` (an
    ``application.json`` application) or ``harness`` (a recorded clone
    analysed as a repository). ``module`` is the import path of a package.
    ``name`` is the source directory's name. ``title``, ``description``
    and ``icon`` are what every surface shows for it (``_host.identity``).
    """

    name: str
    source: Path
    origin: str  # "builtin" | "installed"
    form: str
    module: str | None = None
    title: str = ""
    application_id: str = ""
    enabled: bool | None = None
    description: str = ""
    icon: str = ""
    version: str = ""
    #: The directory of the application.json whose operations it offers.
    manifest_source: Path | None = None


def _contained(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _manifest(directory: Path) -> dict:
    """An ``application.json``, empty when absent or unreadable."""
    try:
        manifest = _json.loads((directory / "application.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return manifest if isinstance(manifest, dict) else {}


_IDENTITY_FILES = ("application.json", "__init__.py", "README.md")
_identities: dict[tuple, dict] = {}


def _identity(directory: Path) -> dict:
    """``title``/``description``/``icon``/``version`` of an application directory.

    Kept until one of the files it is read from changes, so a listing
    re-reads only what was edited.
    """
    from openprogram.programs._host.identity import identity

    stamps = []
    for name in _IDENTITY_FILES:
        try:
            stat = (directory / name).stat()
            stamps.append((stat.st_mtime_ns, stat.st_size))
        except OSError:
            stamps.append(None)
    key = (str(directory), *stamps)
    found = _identities.get(key)
    if found is None:
        found = {**identity(directory), "version": str(_manifest(directory).get("version") or "")}
        _identities[key] = found
    return dict(found)


@request_memo
def _builtin_applications_dir() -> Path:
    """``openprogram/programs/applications``, as the registry resolves it."""
    from openprogram.programs._host import registry

    return Path(registry._builtin_applications_root()).resolve()


@request_memo
def _user_programs_root() -> Path:
    """The user's Programs directory (``~/.openprogram/programs``)."""
    from openprogram.state.program_directory import programs_root

    return programs_root().resolve()


def _builtin_applications() -> list[_Application]:
    from openprogram.programs._host import registry

    root = _builtin_applications_dir()
    watch(root)
    found = []
    for name in registry.builtin_application_names():
        directory = root / name
        watch(directory)
        try:
            source = directory.resolve()
        except OSError:
            continue
        if not _contained(source, root):
            continue
        package = (directory / "__init__.py").is_file()
        found.append(_Application(
            name=name, source=source, origin="builtin",
            form="package" if package else "application",
            module=f"{_BUILTIN_APPLICATION_MODULE}.{name}" if package else None,
            application_id=str(_manifest(directory).get("id") or ""),
            enabled=registry.builtin_application_enabled(name),
            manifest_source=None if package else source,
            **_identity(directory),
        ))
    return found


def _user_packages() -> list[_Application]:
    from openprogram.state.program_directory import program_dirs, program_kinds

    root = _user_programs_root()
    watch(root)
    found = []
    for name, directory in program_dirs(root).items():
        watch(directory)
        if "package" not in program_kinds(directory):
            continue
        found.append(_Application(
            name=name, source=directory.resolve(), origin="installed",
            form="package", module=name, **_identity(directory),
        ))
    return found


def _catalogued_applications() -> list[_Application]:
    from openprogram.state.program_directory import catalog_path
    from openprogram.programs._host.ui import catalog

    watch(catalog_path())
    try:
        rows = catalog.records()
    except (OSError, ValueError):
        return []
    found = []
    for row in rows:
        definition = row["application"]
        app_id = str(definition.get("id") or "")
        if row.get("uninstalled") or not app_id:
            continue
        try:
            source = catalog.source_root(row).resolve()
        except (OSError, ValueError):
            continue
        watch(source)
        shown = _identity(source)
        found.append(_Application(
            name=source.name, source=source, origin="installed", form="application",
            application_id=app_id, enabled=bool(definition.get("enabled", True)),
            manifest_source=source,
            **{**shown, "title": str(definition.get("display_title") or shown["title"])},
        ))
    return found


def _harness_applications() -> list[_Application]:
    from openprogram.programs._host.sources import owner_controlled_program_sources

    found = []
    for row in owner_controlled_program_sources():
        directory = Path(row["path"])
        watch(directory)
        found.append(_Application(
            name=directory.name, source=directory.resolve(), origin="installed",
            form="harness", **_identity(directory),
        ))
    return found


@request_memo
def _applications() -> dict[str, _Application]:
    """Every application by directory name, alphabetical.

    A catalogued application whose source is a user package directory is
    that package's row (it carries the application id and setting); one
    whose source lies inside another application's directory is a plain
    subfolder of that application, not a row of its own. A name taken by
    an earlier row falls back to the application id.
    """
    found: dict[str, _Application] = {}
    by_source: dict[Path, str] = {}

    def add(app: _Application) -> None:
        if app.name in found:
            return
        found[app.name] = app
        by_source.setdefault(app.source, app.name)

    for app in (*_builtin_applications(), *_user_packages()):
        add(app)
    for app in _catalogued_applications():
        owner = by_source.get(app.source)
        if owner is not None:
            found[owner] = dataclasses.replace(
                found[owner], application_id=app.application_id,
                title=app.title, description=app.description or found[owner].description,
                icon=app.icon or found[owner].icon, version=app.version,
                enabled=app.enabled, manifest_source=app.source,
            )
            continue
        parent = next((by_source[source] for source in by_source if source in app.source.parents), None)
        if parent is not None:
            # The package's window: the package shows the application's
            # title, description and icon and opens it, while the
            # application's setting stays with its own subfolder.
            if found[parent].form == "package" and not found[parent].application_id:
                found[parent] = dataclasses.replace(
                    found[parent], application_id=app.application_id, title=app.title,
                    description=app.description or found[parent].description,
                    icon=app.icon, version=app.version, manifest_source=app.source,
                )
            continue
        if app.name in found:
            app = dataclasses.replace(app, name=app.application_id)
        add(app)
    for app in _harness_applications():
        if app.source not in by_source:
            add(app)
    return dict(sorted(found.items(), key=lambda item: item[0].lower()))


def _application_of(relative: str) -> _Application | None:
    parts = Path(relative).parts
    if len(parts) < 2 or parts[0] != "applications":
        return None
    return _applications().get(parts[1])


@request_memo
def _package_states() -> dict[str, dict]:
    """Load state of each user package, keyed by name, and of anything else
    that failed in the latest load pass, under its Program's name."""
    from openprogram.programs._host.packages import package_statuses
    from openprogram.programs._host.registry import load_errors

    states = {row["name"]: row for row in package_statuses()}
    for row in load_errors():
        name = row["name"].split(":", 1)[-1]
        states.setdefault(name, {"name": name, "state": "error", "error": row["error"], "note": ""})
    return states


# ---------------------------------------------------------------------------
# Catalog paths and sources
# ---------------------------------------------------------------------------

@request_memo
def _program_roots() -> tuple[Path, ...]:
    """Directories whose sources the explorer reads."""
    return (
        PROGRAMS_ROOT.resolve(),
        _builtin_applications_dir(),
        _user_programs_root(),
        *(app.source for app in _applications().values()),
    )


def _inside_programs(path: Path) -> bool:
    watch(path)
    try:
        resolved = path.resolve()
    except OSError:
        return False
    return any(_contained(resolved, root) for root in _program_roots())


def _catalog_relative(source: Path) -> Path | None:
    """The catalog path of an absolute source path, or None outside the catalog."""
    try:
        return _TOOLS / source.relative_to((PROGRAMS_ROOT / _TOOLS).resolve())
    except ValueError:
        pass
    # The most specific application wins: a catalogued application can
    # sit inside a user package.
    for app in sorted(_applications().values(), key=lambda app: -len(app.source.parts)):
        try:
            return Path("applications") / app.name / source.relative_to(app.source)
        except ValueError:
            continue
    return None


def _source_for(relative: str) -> Path | None:
    """The file or directory behind a catalog path, when it exists."""
    parts = Path(relative).parts
    if len(parts) >= 2 and parts[0] == "tools":
        base = PROGRAMS_ROOT.joinpath(*parts)
    elif (app := _application_of(relative)) is not None:
        base = app.source.joinpath(*parts[2:])
    else:
        return None
    for candidate in (base, base.with_name(base.name + ".py")):
        watch(candidate)
        if candidate.exists() and _inside_programs(candidate):
            return candidate
    return None


def _check_path(relative: str) -> None:
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("invalid programs path")


@request_memo
def _builtin_modules() -> dict[str, tuple[Path, str]]:
    """Built-in Programs that are Python packages: directory and import path.

    The tool packages in ``registry.TOOL_PROGRAMS`` and the built-in
    applications that are packages.
    """
    from openprogram.programs._host.registry import TOOL_PROGRAMS

    found: dict[str, tuple[Path, str]] = {}
    for name, module in TOOL_PROGRAMS.items():
        directory = PROGRAMS_ROOT.joinpath(*module.removeprefix("openprogram.programs.").split("."))
        watch(directory)
        if directory.is_dir():
            found[name] = (directory.resolve(), module)
    for app in _applications().values():
        if app.origin == "builtin" and app.module:
            found.setdefault(app.name, (app.source, app.module))
    return found


def _builtin_module_matcher():
    """A test for modules of the built-in Programs that are packages."""
    packages = tuple(module for _directory, module in _builtin_modules().values())
    prefixes = tuple(f"{package}." for package in packages)
    return lambda module: module in packages or module.startswith(prefixes)


def _is_package_directory(relative: str) -> bool:
    """True when the catalog path is a program directory, not a single file."""
    app = _application_of(relative)
    if app is not None and app.form == "harness":
        return False
    source = _source_for(relative)
    return source is not None and source.is_dir()


def _package_source_files(
    relative: str, occupied_names: set[str], occupied_sources: set[Path] = frozenset(),
) -> list[tuple[str, Path]]:
    """Supporting top-level ``.py`` files of a program directory."""
    if not _is_package_directory(relative):
        return []
    directory = _source_for(relative)
    found: list[tuple[str, Path]] = []
    for child in _visible_children(directory):
        if (
            not child.is_file()
            or child.suffix != ".py"
            or child.name == "__init__.py"
            or child.name.startswith("_")
            or child.stem in occupied_names
            or child.stem in _INTERNAL_NAMES
            or child.resolve() in occupied_sources
        ):
            continue
        found.append((f"{relative}/{child.stem}", child))
    return found


def _package_source_entries(
    relative: str, occupied_names: set[str], occupied_sources: set[Path] = frozenset(),
) -> list[dict]:
    return [
        {
            "name": Path(path).name,
            "path": path,
            "kind": "file",
            "program_kind": None,
            "has_children": False,
            "logic_path": path,
        }
        for path, _source in _package_source_files(relative, occupied_names, occupied_sources)
    ]


def _row_sources(rows: list[dict]) -> set[Path]:
    return {Path(row["source"]).resolve() for row in rows if Path(row["source"]).is_file()}


def _visible_children(directory: Path) -> list[Path]:
    watch(directory)
    return sorted(
        (
            child for child in directory.iterdir()
            if _inside_programs(child)
            and child.name not in _IGNORED
            and not child.name.startswith(".")
            and (child.is_dir() or child.suffix in {".py", ".md", ".toml", ".yaml", ".yml", ".json"})
        ),
        key=lambda child: (not child.is_dir(), child.name.lower()),
    )[:200]


@request_memo
def _entity_paths() -> dict[str, Path]:
    """Every analysable catalog path and the source behind it."""
    entities: dict[str, Path] = {}
    tools_root = PROGRAMS_ROOT / _TOOLS
    if tools_root.is_dir():
        callables = _registered_tool_callables()
        for category in _visible_children(tools_root):
            if not category.is_dir() or category.name.startswith("_"):
                continue
            for source in _visible_children(category):
                if source.name.startswith("_") or not (
                    source.is_dir() or source.suffix == ".py"
                ):
                    continue
                relative = (_TOOLS / category.name / source.stem).as_posix()
                if callables.get(relative):
                    entities.setdefault(relative, source)

    registered = _registered_agent_methods()
    for source_path, rows in registered.items():
        if len(rows) == 1:
            entities.setdefault(source_path, rows[0]["source"])
        else:
            for row in rows:
                entities.setdefault(
                    f"{source_path}/{row['name']}", row["source"],
                )
        occupied = {row["name"] for row in rows}
        for path, source in _package_source_files(source_path, occupied, _row_sources(rows)):
            entities.setdefault(path, source)

    for name, app in _applications().items():
        path = f"applications/{name}"
        entities.setdefault(path, app.source)
        if path not in registered:
            for child, source in _package_source_files(path, set()):
                entities.setdefault(child, source)
    return entities


def _default_selection() -> str | None:
    """The first application, or the first tool when there is none."""
    applications = _application_entries()
    if applications:
        return applications[0].get("logic_path") or applications[0]["path"]
    callables = _registered_tool_callables()
    for source in sorted(callables):
        rows = callables[source]
        if len(rows) == 1:
            return source
        if rows:
            return f"{source}/{rows[0]['name']}"
    return None


def _agentic_entry_name(relative: str) -> str | None:
    """Registered Agentic function for this catalog path, if any."""
    for source_path, rows in _registered_agent_methods().items():
        if relative == source_path:
            if len(rows) == 1:
                return rows[0]["name"]
            return None
        if relative.startswith(source_path + "/"):
            requested = relative.removeprefix(source_path + "/")
            if any(row["name"] == requested for row in rows):
                return requested
    return None


def _is_agentic_path(relative: str) -> bool:
    """Paths analysed by reachable calls: applications and registered entries."""
    if relative.startswith("applications/"):
        return True
    return any(
        relative == source_path or relative.startswith(source_path + "/")
        for source_path in _registered_agent_methods()
    )


def _analysis_entry_name(relative: str) -> str | None:
    """Function to analyse for call logic, or the whole source for helpers."""
    registered = _agentic_entry_name(relative)
    if registered:
        return registered
    parent = Path(relative).parent.as_posix()
    if parent in _registered_agent_methods():
        return None
    if relative.startswith("applications/"):
        name = Path(relative).name
        source = _entity_paths().get(relative)
        if source is not None and name in _toplevel_names(source):
            return name
    return None


def _program_kind(relative: str) -> str | None:
    """``tool``, ``application`` or None for a supporting source file."""
    parts = Path(relative).parts
    if len(parts) < 2 or parts[-1] == "__init__.py":
        return None
    kind = {"tools": "tool", "applications": "application"}.get(parts[0])
    if kind is None or _agentic_entry_name(relative):
        return kind
    parent = Path(relative).parent.as_posix()
    if parent in _registered_agent_methods():
        return None
    if parts[0] == "applications" and len(parts) == 3 and relative in dict(
        _package_source_files(parent, set())
    ):
        return None
    return kind


def _logic_program_kind(relative: str) -> str | None:
    if relative.startswith("agentic_programming/"):
        return "runtime_primitive"
    return _program_kind(relative)


def _callable_name(relative: str) -> str:
    """Return the registered function name for a Programs entity."""
    found = _agentic_entry_name(relative)
    if found:
        return found
    name = Path(relative).name
    parts = Path(relative).parts
    if len(parts) != 2 or parts[0] != "applications":
        return name
    app = _application_of(relative)
    if app is not None and app.form == "harness":
        names = _harness_entry_names(app)
        if len(names) == 1:
            return names[0]
    return name


def _harness_entry_names(app: _Application) -> list[str]:
    """The Agent entries a recorded harness has registered, by name."""
    from openprogram.programs._host.registry import _find_python_package, program_tools

    package = _find_python_package(str(app.source))
    if package is None:
        return []
    return sorted(program_tools(f"external:{os.path.basename(package)}"))


def _tool_parameters(schema) -> list[dict]:
    """``name``/``description``/``required`` of each argument in a JSON schema."""
    if not isinstance(schema, dict) or not isinstance(schema.get("properties"), dict):
        return []
    required = set(schema.get("required") or ())
    return [
        {
            "name": name,
            "description": " ".join(str((spec or {}).get("description") or "").split()),
            "required": name in required,
        }
        for name, spec in schema["properties"].items()
        if isinstance(name, str)
    ]


@request_memo
def _registered_tool_callables() -> dict[str, list[dict[str, str]]]:
    """Index regular built-in tools by their category/package path."""
    from openprogram.programs import agent_tools

    indexed: dict[str, list[dict[str, str]]] = {}
    in_builtin_package = _builtin_module_matcher()
    for tool in agent_tools(toolset="full", include_disabled=True):
        if getattr(tool, "_is_agent_method", False) or getattr(tool, "_mcp_server", None):
            continue
        module = str(getattr(tool, "_source_module", ""))
        if not module.startswith(_TOOL_MODULE_PREFIX) or in_builtin_package(module):
            continue
        parts = module.removeprefix(_TOOL_MODULE_PREFIX).split(".")
        if len(parts) < 2:
            continue
        package = (_TOOLS / parts[0] / parts[1]).as_posix()
        indexed.setdefault(package, []).append({
            "name": tool.name,
            "description": (tool.description or "").strip().split("\n", 1)[0],
            "parameters": _tool_parameters(getattr(tool, "parameters", None)),
        })
    for rows in indexed.values():
        rows.sort(key=lambda row: row["name"])
    return indexed


@request_memo
def _registered_agent_methods() -> dict[str, list[dict]]:
    """Index registered Program entries by catalog path, at any folder depth.

    The entries of the built-in packages (tool packages and applications)
    and of the user packages; their catalog paths fall under ``tools/`` or
    ``applications/``.
    """
    from openprogram.programs._host.runtime import _registry

    from openprogram.programs._host.packages import working_copy_path

    indexed: dict[str, list[dict]] = {}
    in_builtin_package = _builtin_module_matcher()
    for name, registered in _registry.copy().items():
        fn = inspect.unwrap(getattr(registered, "_python_callable", None) or registered)
        module = str(getattr(fn, "__module__", ""))
        if not in_builtin_package(module) and not getattr(
                registered, "_workflow_owner", "").isidentifier():
            continue
        try:
            raw_source = inspect.getsourcefile(fn)
            # A user package runs from its checked copy; the tree shows its directory.
            source = working_copy_path(Path(raw_source).resolve()) if raw_source else None
        except (OSError, TypeError):
            source = None
        if source is not None:
            watch(source)
        if source is None or not source.is_file() or not _inside_programs(source):
            continue
        relative_source = _catalog_relative(source)
        if relative_source is None:
            continue
        source_path = (
            relative_source.parent
            if (
                source.name in {"__init__.py", "workflow.py"}
                or source.stem == relative_source.parent.name
            )
            else relative_source.with_suffix("")
        ).as_posix()
        if source_path.split("/", 1)[0] not in _ROOTS or "/" not in source_path:
            continue
        indexed.setdefault(source_path, []).append({
            "name": name,
            "description": (getattr(registered, "description", "") or "")
            .strip().split("\n", 1)[0],
            "source": source.parent if source.name == "__init__.py" else source,
        })
    for rows in indexed.values():
        rows.sort(key=lambda row: row["name"])
    return indexed


# ---------------------------------------------------------------------------
# Listings
# ---------------------------------------------------------------------------

def _agentic_entries(
    relative: str, indexed: dict[str, list[dict]] | None = None,
) -> list[dict]:
    """List immediate categories, registered functions, or package files."""
    if indexed is None:
        indexed = _registered_agent_methods()
    kind = "tool" if relative.startswith("tools/") else "application"
    if relative in indexed:
        rows = indexed[relative]
        package = _is_package_directory(relative)
        if len(rows) > 1 or package:
            entries = [
                {
                    "name": row["name"],
                    "path": (
                        f"{relative}/{row['name']}"
                        if package or len(rows) > 1
                        else relative
                    ),
                    "kind": "file",
                    "program_kind": kind,
                    "has_children": False,
                    "callable_name": row["name"],
                    "description": row["description"],
                    "logic_path": (
                        f"{relative}/{row['name']}"
                        if len(rows) > 1
                        else relative
                    ),
                }
                for row in rows
            ]
            occupied = {entry["name"] for entry in entries}
            entries.extend(_package_source_entries(relative, occupied, _row_sources(rows)))
            entries.sort(key=lambda entry: entry["name"].lower())
            return entries

    prefix = Path(relative)
    entries: dict[str, dict] = {}
    for source_path, rows in indexed.items():
        source = Path(source_path)
        try:
            remainder = source.relative_to(prefix).parts
        except ValueError:
            continue
        if (
            not remainder
            or remainder[0].startswith("_")
            or remainder[0] in _INTERNAL_NAMES
        ):
            continue
        child_path = (prefix / remainder[0]).as_posix()
        if (
            len(remainder) > 1
            or len(rows) > 1
            or _is_package_directory(child_path)
        ):
            folder = {
                "name": remainder[0],
                "path": child_path,
                "kind": "folder",
                "program_kind": None,
                "has_children": True,
            }
            if len(rows) == 1 and len(remainder) == 1:
                folder["program_kind"] = kind
                folder["callable_name"] = rows[0]["name"]
                folder["logic_path"] = child_path
                folder["description"] = rows[0]["description"]
            entries[child_path] = folder
            continue
        row = rows[0]
        entries[child_path] = {
            "name": row["name"],
            "path": child_path,
            "kind": "file",
            "program_kind": kind,
            "has_children": False,
            "callable_name": row["name"],
            "description": row["description"],
            "logic_path": source_path,
        }
    return sorted(entries.values(), key=lambda entry: entry["name"].lower())


def _tool_entries(relative: str) -> list[dict]:
    """One level under ``tools/``: categories, tools and tool packages."""
    entries: dict[str, dict] = {}
    if relative == _TOOLS.as_posix():
        for child in _visible_children(PROGRAMS_ROOT / _TOOLS):
            if child.is_dir() and not child.name.startswith("_"):
                path = (_TOOLS / child.name).as_posix()
                entries[path] = {
                    "name": child.name, "path": path, "kind": "folder",
                    "program_kind": None, "has_children": True,
                }
        return list(entries.values())
    callables = _registered_tool_callables()
    directory = PROGRAMS_ROOT / relative
    if Path(relative).parent == _TOOLS and directory.is_dir() and _inside_programs(directory):
        for source in _visible_children(directory):
            if source.name.startswith("_") or not (
                source.is_dir() or source.suffix == ".py"
            ):
                continue
            source_path = (Path(relative) / source.stem).as_posix()
            rows = callables.get(source_path, [])
            if len(rows) == 1:
                row = rows[0]
                entries[source_path] = {
                    "name": row["name"],
                    "path": source_path,
                    "kind": "file",
                    "program_kind": "tool",
                    "has_children": False,
                    "callable_name": row["name"],
                    "description": row["description"],
                    "logic_path": source_path,
                    "parameters": row.get("parameters", []),
                }
            elif rows:
                entries[source_path] = {
                    "name": source.stem,
                    "path": source_path,
                    "kind": "folder",
                    "program_kind": None,
                    "has_children": True,
                }
    elif relative in callables:
        for row in callables[relative]:
            path = f"{relative}/{row['name']}"
            entries[path] = {
                "name": row["name"],
                "path": path,
                "kind": "file",
                "program_kind": "tool",
                "has_children": False,
                "callable_name": row["name"],
                "description": row["description"],
                "logic_path": relative,
                "parameters": row.get("parameters", []),
            }
    for entry in _agentic_entries(relative):
        entries.setdefault(entry["path"], entry)
    return sorted(entries.values(), key=lambda entry: entry["name"].lower())


def _harness_entries(relative: str) -> list[dict]:
    """The Agent entries a recorded harness has registered."""
    from openprogram.programs._host.runtime import _registry

    app = _application_of(relative)
    if app is None:
        return []
    entries = []
    for name in _harness_entry_names(app):
        description = getattr(_registry.get(name), "description", "") or ""
        entries.append({
            "name": name,
            "path": f"{relative}/{name}",
            "kind": "file",
            "program_kind": "application",
            "has_children": False,
            "callable_name": name,
            "description": description.strip().split("\n", 1)[0],
            "logic_path": relative,
        })
    return entries


def _application_children(relative: str) -> list[dict]:
    """One level inside an application: its entries and source files."""
    app = _application_of(relative)
    if app is None:
        return []
    if app.form == "harness":
        return _harness_entries(relative) if len(Path(relative).parts) == 2 else []
    entries = {entry["path"]: entry for entry in _agentic_entries(relative)}
    registered = _registered_agent_methods()
    if relative not in registered:
        names = {entry["name"] for entry in entries.values()}
        for entry in _package_source_entries(relative, names):
            entries.setdefault(entry["path"], entry)
    for entry in _subfolder_entries(relative):
        entries.setdefault(entry["path"], entry)
    return sorted(entries.values(), key=lambda entry: entry["name"].lower())


def _listable(directory: Path) -> bool:
    """True when a directory holds a subfolder or a module the explorer lists."""
    return any(
        child.is_dir() or (
            child.suffix == ".py" and child.name != "__init__.py"
            and not child.name.startswith("_") and child.stem not in _INTERNAL_NAMES
        )
        for child in _visible_children(directory)
        if not child.name.startswith("_") and child.name not in _INTERNAL_NAMES
    )


def _subfolder_entries(relative: str) -> list[dict]:
    """The real subdirectories of an application directory, as plain folders."""
    if not _is_package_directory(relative):
        return []
    entries = []
    for child in _visible_children(_source_for(relative)):
        if not child.is_dir() or child.name.startswith("_") or child.name in _INTERNAL_NAMES:
            continue
        entries.append({
            "name": child.name,
            "path": f"{relative}/{child.name}",
            "kind": "folder",
            "program_kind": None,
            "has_children": _listable(child),
        })
    return entries


def _application_agent_entries(relative: str) -> list[tuple[str, str, str]]:
    """``(catalog path, name, description)`` of every entry an application registered.

    The host registers an application's Agent entries wherever they live
    in its package. Only an application root has them.
    """
    parts = Path(relative).parts
    if len(parts) != 2 or parts[0] != "applications":
        return []
    found = []
    for source_path, rows in _registered_agent_methods().items():
        if source_path != relative and not source_path.startswith(relative + "/"):
            continue
        for row in rows:
            path = source_path if len(rows) == 1 else f"{source_path}/{row['name']}"
            found.append((path, row["name"], row["description"]))
    return sorted(found, key=lambda item: item[1])


def _manifest_agent_entries(app: _Application) -> list[dict]:
    """The ``agent: true`` operations of an application's ``application.json``.

    The host registers each as the tool ``<id>__<operation>``; the list
    shows the operation name with the first sentence of its description.
    Operations for the application's own page ("Editor only") are left out.
    """
    if app.manifest_source is None:
        return []
    from openprogram.programs._host.identity import first_sentence
    from openprogram.programs._host.ui.loader import entry_name

    manifest = _manifest(app.manifest_source)
    app_id = str(manifest.get("id") or app.application_id)
    operations = manifest.get("operations")
    if not app_id or not isinstance(operations, dict):
        return []
    found = []
    for name, operation in operations.items():
        if not isinstance(operation, dict) or operation.get("agent") is not True:
            continue
        description = str(operation.get("description") or "").strip()
        if description.lower().startswith("editor only"):
            continue
        found.append({
            "name": name,
            "description": first_sentence(description) if description else "",
            "tool": entry_name(app_id, name),
        })
    return found


def _package_exports(source: Path) -> list[str]:
    """``__all__`` of a package, or its public top-level functions."""
    init = source / "__init__.py" if source.is_dir() else source
    try:
        tree = parsed_source(init)
    except (OSError, UnicodeDecodeError, SyntaxError):
        return []
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)
            and isinstance(node.value, (ast.List, ast.Tuple))
        ):
            return [
                item.value for item in node.value.elts
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            ]
    return sorted(
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    )


def _application_note(app: _Application, has_entries: bool) -> dict | None:
    """Why an application has no Agent entry, or None when it has one.

    ``ui_only``: an ``application.json`` application, opened from
    Applications. ``library``: a package that registers no entry and
    exports Python functions for other Programs; ``install`` names the
    installable harness that provides the Agent of the same name.
    A harness and an installed package that has not loaded yet explain
    themselves elsewhere.
    """
    if has_entries or app.form == "harness":
        return None
    if app.form == "package" and app.origin == "installed":
        state = _package_states().get(app.name)
        if state is None or state["state"] != "loaded":
            return None
    if app.form == "application":
        return {"kind": "ui_only"}
    if app.form == "package" and app.name in _toplevel_names(app.source):
        return None
    return {"kind": "library", "exports": _package_exports(app.source)}


def _short_location(path: Path) -> str:
    """``path`` relative to the OpenProgram checkout, else ``~/…``, else as is."""
    checkout = Path(openprogram.__file__).resolve().parent.parent
    if _contained(path, checkout) and path != checkout:
        return path.relative_to(checkout).as_posix()
    home = Path.home()
    if _contained(path, home) and path != home:
        return "~/" + path.relative_to(home).as_posix()
    return str(path)


def _application_entry(app: _Application) -> dict:
    path = f"applications/{app.name}"
    rows = [
        {"name": name, "description": description}
        for _path, name, description in _application_agent_entries(path)
    ]
    listed = {row["name"] for row in rows}
    rows += [
        row for row in _manifest_agent_entries(app)
        if row["name"] not in listed and row["tool"] not in listed
    ]
    entry: dict = {
        "name": app.name,
        "path": path,
        "kind": "folder",
        "program_kind": "application",
        "has_children": bool(_application_children(path)),
        "logic_path": path,
        "origin": app.origin,
        "source_path": str(app.source),
        "location": _short_location(app.source),
    }
    if len(rows) == 1:
        entry["callable_name"] = rows[0].get("tool", rows[0]["name"])
        entry["description"] = rows[0]["description"]
    elif not rows and (
        app.form == "harness"
        # An installed package that has not loaded yet keeps its name so
        # the Use button can say why it is not callable.
        or (app.form == "package" and app.origin == "installed")
        or (app.form == "package" and app.name in _toplevel_names(app.source))
    ):
        entry["callable_name"] = _callable_name(path)
    note = _application_note(app, bool(rows))
    if note is not None:
        entry["note"] = note
    for key, value in (
        ("title", app.title), ("application_id", app.application_id),
        ("icon", app.icon), ("version", app.version), ("form", app.form),
    ):
        if value:
            entry[key] = value
    if app.description:
        # The application's own sentence; without one, a single entry's.
        entry["description"] = app.description
    entry["agent_entries"] = rows
    if app.enabled is not None:
        entry["enabled"] = app.enabled
    state = _package_states().get(app.name)
    if state is not None and (state["state"] == "error" or (app.form == "package" and app.origin == "installed")):
        entry.update({
            "load_state": state["state"], "load_error": state["error"],
            "load_note": state["note"],
        })
    return entry


def _application_entries() -> list[dict]:
    return [_application_entry(app) for app in _applications().values()]


def _list_entries(relative: str) -> dict:
    _check_path(relative)
    if not relative:
        entries = [
            {
                "name": name,
                "path": name,
                "kind": "folder",
                "program_kind": None,
                "has_children": True,
            }
            for name in _ROOTS
        ]
    elif relative == "applications":
        entries = _application_entries()
    elif relative.startswith("applications/"):
        entries = _application_children(relative)
    elif relative == "tools" or relative.startswith("tools/"):
        entries = _tool_entries(relative)
    else:
        raise FileNotFoundError(relative)
    if not entries and relative not in _ROOTS and relative:
        raise FileNotFoundError(relative)
    payload = {"path": relative, "entries": entries}
    if not relative:
        payload["default_selection"] = _default_selection()
    return payload


# ---------------------------------------------------------------------------
# Call logic
# ---------------------------------------------------------------------------

def _module_prefix(relative: str) -> str | None:
    """The import path of a catalog path, or None when it is not importable."""
    parts = list(Path(relative).parts)
    if parts and parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    if parts and parts[-1] == "__init__":
        parts.pop()
    if not parts:
        return None
    if parts[0] == "tools":
        return "openprogram.programs." + ".".join(parts)
    app = _application_of("/".join(parts))
    if app is not None and app.module:
        return ".".join([app.module, *parts[2:]])
    return None


@request_memo
def _python_sources(path: Path) -> tuple[list[Path], bool]:
    watch(path)
    if path.is_file():
        sources = [path] if path.suffix == ".py" and _inside_programs(path) else []
        return sources, False
    sources: list[Path] = []
    for directory, names, files in os.walk(path, followlinks=False):
        base = Path(directory)
        watch(base)
        names[:] = sorted(
            name for name in names
            if name not in _IGNORED
            and not name.startswith(".")
            and _inside_programs(base / name)
        )
        for name in sorted(files):
            source = base / name
            if name.endswith(".py") and _inside_programs(source):
                sources.append(source)
                if len(sources) > _MAX_SOURCE_FILES:
                    return sources[:_MAX_SOURCE_FILES], True
    return sources, False


def _imported_modules(path: Path) -> tuple[set[str], set[str]]:
    modules: set[str] = set()
    warnings: set[str] = set()
    sources, truncated = _python_sources(path)
    if truncated:
        warnings.add("source_file_limit")
    for source in sources:
        try:
            if source.stat().st_size > _MAX_SOURCE_BYTES:
                warnings.add("oversized_source")
                continue
            tree = parsed_source(source)
        except (OSError, UnicodeDecodeError, SyntaxError):
            warnings.add("source_parse_failed")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level:
                    source_relative = _catalog_relative(source)
                    source_module = (
                        _module_prefix(source_relative.as_posix())
                        if source_relative is not None else None
                    )
                    if source_module is None:
                        continue
                    package = (
                        source_module
                        if source.name == "__init__.py"
                        else source_module.rpartition(".")[0]
                    )
                    try:
                        module = importlib.util.resolve_name(
                            "." * node.level + module, package,
                        )
                    except (ImportError, ValueError):
                        continue
                if module:
                    modules.add(module)
                    modules.update(
                        f"{module}.{alias.name}" for alias in node.names
                        if alias.name != "*"
                    )
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "import_module"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                modules.add(node.args[0].value)
    return modules, warnings


@request_memo
def _toplevel_names(path: Path) -> set[str]:
    """Module-level functions and assignments in one Program source."""
    names: set[str] = set()
    sources, _truncated = _python_sources(path)
    for source in sources:
        try:
            tree = parsed_source(source)
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                names.update(
                    target.id for target in node.targets
                    if isinstance(target, ast.Name)
                )
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names.add(node.target.id)
    return names


def _package_symbol_index(
    entities: dict[str, Path],
) -> dict[tuple[str, str], str]:
    """Map ``(package path, top-level name)`` to the entity that defines it."""
    registered = _registered_agent_methods()
    index: dict[tuple[str, str], str] = {}
    for path, source in entities.items():
        if not _is_agentic_path(path):
            continue
        package = (
            path
            if path in registered or _is_package_directory(path)
            else Path(path).parent.as_posix()
        )
        for name in _toplevel_names(source):
            index.setdefault((package, name), path)
    return index


def _resolve_package_import(
    module: str,
    prefix: str,
    target: str,
    symbols: dict[tuple[str, str], str],
) -> str | None:
    """Map a package-attribute import to the sibling that defines it.

    Goal helpers call through ``import ...goal as _goal`` so tests can
    patch the package. ``_goal.save_goal`` is ``state.py``, not the
    public ``goal()`` Program — matching the package prefix would
    otherwise inherit ``agent()`` / ``llm()``.
    """
    remainder = module[len(prefix):].lstrip(".")
    attr = remainder.split(".", 1)[0] if remainder else ""
    if not attr:
        return target
    specific = symbols.get((target, attr))
    if specific:
        return specific
    registered = _registered_agent_methods()
    if target not in registered:
        return target
    if attr == _agentic_entry_name(target):
        return target
    return None


_UNSET = object()


def _direct_calls(
    relative: str,
    entities: dict[str, Path],
    symbols: dict[tuple[str, str], str] | None = None,
    *,
    entry_name: str | None | object = _UNSET,
    reached: dict[str, str | None] | None = None,
) -> tuple[list[str], set[str]]:
    prefixes = sorted(
        (
            (prefix, path) for path in entities
            if (prefix := _module_prefix(path)) is not None
        ),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    if symbols is None:
        symbols = _package_symbol_index(entities)
    if entry_name is _UNSET:
        entry_name = _analysis_entry_name(relative)
    targets: set[str] = set()

    def record(target: str, attr: str = "") -> None:
        targets.add(target)
        if reached is None:
            return
        mark = attr or None
        if target not in reached:
            reached[target] = mark
        elif reached[target] != mark:
            reached[target] = None

    analyzed_name = entry_name if isinstance(entry_name, str) else None
    if _is_agentic_path(relative):
        modules, warnings = _called_import_modules(
            entities[relative], analyzed_name,
        )
    else:
        modules, warnings = _imported_modules(entities[relative])
    for module in modules:
        for prefix, target in prefixes:
            if not (module == prefix or module.startswith(prefix + ".")):
                continue
            remainder = module[len(prefix):].lstrip(".")
            attr = remainder.split(".", 1)[0] if remainder else ""
            resolved = _resolve_package_import(module, prefix, target, symbols)
            if resolved and resolved != relative:
                record(resolved, attr)
            break
    if _is_agentic_path(relative):
        nodes, _, local_warnings = _analysis_nodes(
            entities[relative], analyzed_name,
        )
        warnings.update(local_warnings)
        called_names = {
            node.func.id
            for root in nodes
            for node in ast.walk(root)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        for target, target_source in entities.items():
            if (
                target != relative
                and _is_agentic_path(target)
                and target_source == entities[relative]
                and _callable_name(target) in called_names
            ):
                record(target, _callable_name(target))
    return sorted(targets), warnings


def _analysis_nodes(
    path: Path, entry_name: str | None = None,
) -> tuple[list[ast.AST], list[ast.Module], set[str]]:
    """Return one entry's reachable local functions and parsed modules.

    ``entry_name`` is a function, a method declared as an entry, or any
    other top-level name a caller reached: a class (all of its methods
    run once it is constructed), a module-level value (its initialiser)
    or a submodule of a package (its whole source). Only a name none of
    these define is ``entry_not_resolved``.
    """
    trees: list[ast.Module] = []
    tree_sources: list[Path] = []
    warnings: set[str] = set()
    sources, truncated = _python_sources(path)
    if truncated:
        warnings.add("source_file_limit")
    for source in sources:
        try:
            if source.stat().st_size > _MAX_SOURCE_BYTES:
                warnings.add("oversized_source")
                continue
            trees.append(parsed_source(source))
            tree_sources.append(source)
        except (OSError, UnicodeDecodeError, SyntaxError):
            warnings.add("source_parse_failed")
            continue

    if not entry_name:
        return list(trees), trees, warnings
    from ...function_catalog import _program_source_entries
    functions = {}
    owners = {}
    classes: dict[str, ast.ClassDef] = {}
    values: dict[str, ast.AST] = {}
    entry = None
    for tree in trees:
        for item in tree.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions[item.name] = item
            elif isinstance(item, ast.ClassDef):
                classes.setdefault(item.name, item)
                for method in item.body:
                    if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        functions[f"{item.name}.{method.name}"] = method
                        owners[id(method)] = item.name
            elif isinstance(item, ast.Assign):
                for target in item.targets:
                    if isinstance(target, ast.Name):
                        values.setdefault(target.id, item)
            elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                values.setdefault(item.target.id, item)
        declared = _program_source_entries(ast.unparse(tree))
        declaration = declared.get(entry_name)
        if declaration:
            owner = declaration["owner"]
            method = declaration["node"].name
            entry = functions.get(f"{owner}.{method}" if owner else method)
    if entry is None:
        entry = functions.get(entry_name)
    starts: list[ast.AST] = [entry] if entry is not None else []
    if not starts and entry_name in classes:
        starts = [
            method for method in classes[entry_name].body
            if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
        ] or [classes[entry_name]]
    if not starts and entry_name in values:
        starts = [values[entry_name]]
    if not starts and path.is_dir():
        submodule = [path / entry_name, path / f"{entry_name}.py"]
        starts = [
            tree for tree, source in zip(trees, tree_sources)
            if any(source == root or _contained(source, root) for root in submodule)
        ]
    if not starts:
        warnings.add("entry_not_resolved")
        return [], trees, warnings
    reachable: list[ast.AST] = []
    pending = deque(starts)
    seen: set[int] = set()
    while pending:
        node = pending.popleft()
        if id(node) in seen:
            continue
        seen.add(id(node))
        reachable.append(node)
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            name = func.id if isinstance(func, ast.Name) else None
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                if func.value.id in {"self", "cls"} and id(node) in owners:
                    name = f"{owners[id(node)]}.{func.attr}"
            helper = functions.get(name) if name else None
            if helper is not None and id(helper) not in seen:
                pending.append(helper)
    return reachable, trees, warnings


def _called_import_modules(
    path: Path, entry_name: str | None,
) -> tuple[set[str], set[str]]:
    """Return imported call targets reachable from one Agentic entry."""
    nodes, trees, warnings = _analysis_nodes(path, entry_name)
    aliases: dict[str, str] = {}

    def record_import(node: ast.AST) -> None:
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".", 1)[0]
                aliases[local] = alias.name
            return
        if not isinstance(node, ast.ImportFrom):
            return
        module = node.module or ""
        if node.level:
            try:
                relative = _catalog_relative(path.resolve())
                source_module = (
                    _module_prefix(relative.as_posix()) if relative is not None else None
                )
                if source_module is None:
                    return
                package = (
                    source_module
                    if path.is_dir() or path.name == "__init__.py"
                    else source_module.rpartition(".")[0]
                )
                module = importlib.util.resolve_name(
                    "." * node.level + module, package,
                )
            except (ImportError, ValueError):
                return
        for alias in node.names:
            if alias.name != "*":
                aliases[alias.asname or alias.name] = (
                    f"{module}.{alias.name}" if module else alias.name
                )

    for tree in trees:
        for node in tree.body:
            record_import(node)
    for root in nodes:
        for node in ast.walk(root):
            record_import(node)

    def imported_name(expr: ast.expr) -> str | None:
        if isinstance(expr, ast.Name):
            return aliases.get(expr.id)
        if isinstance(expr, ast.Attribute):
            base = imported_name(expr.value)
            return f"{base}.{expr.attr}" if base else None
        return None

    modules = {
        imported
        for root in nodes
        for node in ast.walk(root)
        if isinstance(node, ast.Call)
        if (imported := imported_name(node.func)) is not None
    }
    return modules, warnings


def _called_agentic_primitives(
    path: Path, *, entry_name: str | None = None,
) -> tuple[set[str], set[str]]:
    """Return goal/agent/llm primitives reachable from one Program entry."""
    called: set[str] = set()
    nodes, trees, warnings = _analysis_nodes(path, entry_name)

    direct: dict[str, str] = {}
    modules: dict[str, str] = {}
    for tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "openprogram.runtime.agent":
                    for alias in node.names:
                        if alias.name in _AGENTIC_PRIMITIVES:
                            direct[alias.asname or alias.name] = alias.name
                elif module.startswith("openprogram.runtime.agent."):
                    primitive = module.rsplit(".", 1)[-1]
                    for alias in node.names:
                        if alias.name in _AGENTIC_PRIMITIVES:
                            direct[alias.asname or alias.name] = alias.name
                        elif primitive in _AGENTIC_PRIMITIVES and alias.name == primitive:
                            direct[alias.asname or alias.name] = primitive
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "openprogram.runtime.agent":
                        modules[alias.asname or "openprogram"] = alias.name

    # Roles: module-level ``name = Agent(...)``. ``role(...)`` runs a tool
    # loop (agent); ``role.choose(...)`` is one model call (llm).
    roles: set[str] = set()
    for tree in trees:
        for node in tree.body:
            if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Name) and node.value.func.id == "Agent"):
                roles.update(target.id for target in node.targets if isinstance(target, ast.Name))
    for root in nodes:
        for node in ast.walk(root):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id in roles:
                called.add("agent")
            elif (isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in roles):
                called.add("llm" if node.func.attr == "choose" else "agent")
            if isinstance(node.func, ast.Name):
                primitive = direct.get(node.func.id)
                if primitive:
                    called.add(primitive)
            elif (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in _AGENTIC_PRIMITIVES
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in modules
            ):
                called.add(node.func.attr)
    return called, warnings


def _primitive_id(name: str) -> str:
    return f"agentic_programming/{name}"


def _program_logic(relative: str) -> dict:
    entities = _entity_paths()
    if relative not in entities:
        raise FileNotFoundError(relative)
    app = _application_of(relative)
    if (
        app is not None and app.form == "harness"
        and len(Path(relative).parts) == 2 and entities[relative].is_dir()
    ):
        from ..execution.program_calls import package_calls

        names = _harness_entry_names(app)
        result = package_calls(entities[relative], relative, names[0] if len(names) == 1 else None)
        from openprogram.programs._host.sources import owner_controlled_program_sources
        source = next((row.get("source", "") for row in owner_controlled_program_sources()
                       if Path(row["path"]).resolve() == entities[relative].resolve()), "")
        commands = ["openprogram programs list"]
        if str(source).startswith(("https://", "http://", "ssh://", "git@")):
            commands.append(f"openprogram programs install {shlex.quote(source)} --upgrade")
        commands.append(f"openprogram programs uninstall {shlex.quote(Path(relative).name)}")
        result["nodes"][0]["management_commands"] = commands
        return result
    symbols = _package_symbol_index(entities)
    focus: dict[str, str | None] = {relative: _analysis_entry_name(relative)}
    depths = {relative: 0}
    queue = deque([relative])
    edges: list[dict[str, str]] = []
    seen_edges: set[tuple[str, str]] = set()
    analysis_warnings: set[str] = set()

    def expand(source: str, analysed: str, entry_name) -> None:
        """Add the calls of ``analysed`` (focused on ``entry_name``) as edges from ``source``."""
        incoming: dict[str, str | None] = {}
        targets, warnings = _direct_calls(
            analysed, entities, symbols, entry_name=entry_name, reached=incoming,
        )
        analysis_warnings.update(warnings)
        for target in targets:
            if target == source:
                continue
            edge = (source, target)
            if edge not in seen_edges:
                seen_edges.add(edge)
                edges.append({"source": source, "target": target})
            if target not in depths:
                depths[target] = depths[source] + 1
                queue.append(target)
            mark = incoming.get(target)
            if target not in focus:
                focus[target] = mark
            elif focus[target] != mark:
                focus[target] = None
        primitives, warnings = _called_agentic_primitives(
            entities[analysed],
            entry_name=entry_name if isinstance(entry_name, str) else None,
        )
        analysis_warnings.update(warnings)
        pending = deque((source, primitive) for primitive in sorted(primitives))
        while pending:
            primitive_source, primitive = pending.popleft()
            target = _primitive_id(primitive)
            edge = (primitive_source, target)
            if edge not in seen_edges:
                seen_edges.add(edge)
                edges.append({"source": primitive_source, "target": target})
            if target not in depths:
                depths[target] = depths[primitive_source] + 1
            for dependency in _PRIMITIVE_DEPENDENCIES[primitive]:
                pending.append((target, dependency))

    # An application stands for the entries the host registered for it:
    # its call logic is the union of theirs.
    agent_entries = _application_agent_entries(relative)
    while queue and len(depths) < 128:
        source = queue.popleft()
        if source == relative and agent_entries:
            for path, name, _description in agent_entries:
                if path in entities:
                    expand(source, path, name)
        else:
            expand(source, source, focus.get(source, _UNSET))
    nodes = [
        {
            "id": path,
            "name": Path(path).name,
            "path": path,
            "program_kind": _logic_program_kind(path),
            "depth": depth,
        }
        for path, depth in sorted(depths.items(), key=lambda item: (item[1], Path(item[0]).name))
    ]
    if queue:
        analysis_warnings.add("node_limit")
    result = {
        "root": relative,
        "nodes": nodes,
        "edges": edges,
        "analysis_complete": not analysis_warnings,
        "analysis_warnings": sorted(analysis_warnings),
    }
    if len(Path(relative).parts) == 2 and app is not None:
        result["entries"] = [name for _path, name, _description in agent_entries]
    return result


def _emit(event: str, data: dict) -> None:
    """Broadcast a typed event to all connected WS clients. No-op if the
    server module isn't initialised yet (e.g. during tests). Mirrors
    ``routes/skills.py``'s helper."""
    try:
        from openprogram.interfaces.server import server as _server
        _server._broadcast(_json.dumps({"type": event, **data}, default=str))
    except Exception:
        # Server not initialised (tests, CLI); clients refetch on load.
        _log.debug("%s broadcast skipped", event, exc_info=True)


def _canonical_catalog_path(path: str) -> str:
    return path.strip("/")


def _cache_identity() -> tuple:
    from openprogram.programs._host.runtime import _registry as tools
    from openprogram.programs._host.sources import owner_controlled_program_sources
    from openprogram.programs._host.packages import package_statuses
    from openprogram.programs._host.registry import load_errors
    return (
        str(PROGRAMS_ROOT),
        repr(owner_controlled_program_sources()),
        repr(package_statuses()),
        repr(load_errors()),
        tuple((name, id(tool), getattr(tool, "description", "")) for name, tool in tools.copy().items()),
    )


def register(app) -> None:
    cache = CatalogCache()
    @app.get("/api/programs/explorer")
    def programs_explorer(path: str = "", revision: str = ""):
        try:
            return JSONResponse(content=cache.get("explorer", _canonical_catalog_path(path), revision, _cache_identity, _list_entries))
        except ValueError:
            return JSONResponse(
                content={"error": "invalid programs path"}, status_code=400,
            )
        except FileNotFoundError:
            return JSONResponse(
                content={"error": "programs path not found"}, status_code=404,
            )

    @app.get("/api/programs/logic")
    def programs_logic(path: str, revision: str = ""):
        try:
            return JSONResponse(content=cache.get("logic", _canonical_catalog_path(path), revision, _cache_identity, _program_logic))
        except ValueError:
            return JSONResponse(
                content={"error": "invalid programs path"}, status_code=400,
            )
        except FileNotFoundError:
            return JSONResponse(
                content={"error": "program not found"}, status_code=404,
            )

    @app.post("/api/programs/refresh")
    async def refresh_programs():
        """Re-scan ``~/.openprogram/programs/`` for newly-installed programs.

        Returns ``{"added": [...], "total": N}``. Broadcasts
        ``programs:changed`` when ``added`` is non-empty so the function
        list refreshes live in every open tab.
        """
        try:
            from openprogram.programs._host.registry import rescan
            result = rescan()
        except Exception as e:  # noqa: BLE001
            return JSONResponse(
                content={"ok": False, "error": f"{type(e).__name__}: {e}"},
                status_code=500,
            )
        if result.get("added"):
            _emit("programs:changed", {"added": result["added"]})
        return JSONResponse(content={"ok": True, **result})
