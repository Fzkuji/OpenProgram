"""``/api/programs/*`` — runtime detection of installed agentic programs.

A harness installed after boot (``git clone`` into ``programs/applications/``
or ``openprogram programs install``) doesn't appear until its modules are
imported. This route re-runs discovery on demand so the new program's
functions go live without restarting the worker:

  * ``POST /api/programs/refresh`` — the manual "refresh" button. Re-scans
    and, if anything new registered, broadcasts ``programs:changed`` so
    every connected UI re-fetches ``/api/programs``.

The background watcher (``functions.watcher``) hits the same core
(``_registry.rescan``) + the same broadcast, so manual and automatic
detection are one code path with two triggers.
"""
from __future__ import annotations

import ast
import importlib.util
import inspect
import json as _json
import os
from collections import deque
from pathlib import Path

import openprogram
from fastapi.responses import JSONResponse


PROGRAMS_ROOT = Path(openprogram.__file__).resolve().parent / "programs"
_IGNORED = {
    ".git", ".pytest_cache", "__pycache__", "node_modules", "build", "dist",
    "output", "runs", ".venv", "venv",
}
_ROOT_ORDER = {"tools": 0, "workflow": 1, "applications": 2}
_MAX_SOURCE_FILES = 200
_MAX_SOURCE_BYTES = 1_000_000
_AGENTIC_PRIMITIVES = ("agent", "llm")
_PRIMITIVE_DEPENDENCIES = {
    "agent": ("llm",),
    "llm": (),
}
_VANILLA_RELATIVE = Path("tools")
_VANILLA_MODULE_PREFIX = "openprogram.programs.tools."
_AGENTIC_RELATIVE = Path("workflow")
_AGENTIC_MODULE_PREFIX = "openprogram.programs.workflow."
# Helpers and session adapters that live under workflow/ but are not
# runnable Programs. They stay importable; they do not become explorer rows.
_WORKFLOW_INTERNAL_NAMES = {
    "ask_user",
    "errors",
    "json_parsing",
    "resume_workflow",
}


def _inside_programs(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    return any(
        resolved == root or root in resolved.parents
        for root in _catalog_roots()
    )


def _catalog_roots() -> list[Path]:
    """Installed catalog plus owner-recorded source catalogs."""
    from openprogram.programs._programs import owner_programs_roots

    roots = [PROGRAMS_ROOT.resolve(), *owner_programs_roots()]
    unique: dict[str, Path] = {}
    for root in roots:
        resolved = root.resolve()
        unique.setdefault(os.path.normcase(os.fspath(resolved)), resolved)
    return list(unique.values())


def _safe_directory(relative: str) -> Path:
    root = PROGRAMS_ROOT.resolve()
    candidate = (root / relative).resolve()
    if Path(relative).is_absolute() or not _inside_programs(candidate):
        raise ValueError("invalid programs path")
    if not candidate.is_dir():
        raise FileNotFoundError(relative)
    return candidate


def _program_kind(relative: str) -> str | None:
    parts = Path(relative).parts
    if parts[-1] == "__init__.py":
        return None
    if len(parts) >= 2 and parts[0] == "tools":
        return "vanilla_function"
    if len(parts) >= 2 and parts[0] == "workflow":
        return "workflow"
    if len(parts) == 2 and parts[0] == "applications":
        return "application"
    return None


def _visible_children(directory: Path) -> list[Path]:
    return sorted(
        (
            child for child in directory.iterdir()
            if _inside_programs(child)
            and child.name not in _IGNORED
            and not child.name.startswith(".")
            and (child.is_dir() or child.suffix in {".py", ".md", ".toml", ".yaml", ".yml", ".json"})
        ),
        key=lambda child: (
            _ROOT_ORDER.get(child.name, 99),
            not child.is_dir(),
            child.name.lower(),
        ),
    )[:200]


def _entity_paths() -> dict[str, Path]:
    entities: dict[str, Path] = {}
    vanilla_root = PROGRAMS_ROOT / _VANILLA_RELATIVE
    if vanilla_root.is_dir():
        callables = _registered_vanilla_callables()
        for category in _visible_children(vanilla_root):
            if not category.is_dir() or category.name.startswith("_"):
                continue
            for source in _visible_children(category):
                if source.name.startswith("_") or not (
                    source.is_dir() or source.suffix == ".py"
                ):
                    continue
                relative = (
                    _VANILLA_RELATIVE / category.name / source.stem
                ).as_posix()
                if callables.get(relative):
                    entities.setdefault(relative, source)

    for source_path, rows in _registered_agentic_callables().items():
        if len(rows) == 1:
            entities.setdefault(source_path, rows[0]["source"])
            continue
        for row in rows:
            entities.setdefault(
                f"{source_path}/{row['name']}", row["source"],
            )

    scan_roots = []
    scan_roots.extend(
        (root, parent)
        for root in _catalog_roots()
        for parent in (Path("workflow"), Path("applications"))
    )
    for root, parent in scan_roots:
        directory = root / parent
        if not directory.is_dir():
            continue
        for child in _visible_children(directory):
            name = child.stem if child.is_file() else child.name
            if name.startswith("_") or name in _WORKFLOW_INTERNAL_NAMES:
                continue
            if child.is_dir() or child.suffix == ".py":
                relative = (parent / name).as_posix()
                if _program_kind(relative):
                    entities.setdefault(relative, child)
    return entities


def _default_selection() -> str | None:
    entities = _entity_paths()
    for prefix in ("workflow/", "applications/"):
        matches = sorted(path for path in entities if path.startswith(prefix))
        if matches:
            return matches[0]
    callables = _registered_vanilla_callables()
    for source in sorted(callables):
        rows = callables[source]
        if len(rows) == 1:
            return source
        if rows:
            return f"{source}/{rows[0]['name']}"
    return None


def _callable_name(relative: str) -> str:
    """Return the registered function name for a Programs entity."""
    name = Path(relative).stem
    if relative.startswith("workflow/"):
        for source_path, rows in _registered_agentic_callables().items():
            if relative == source_path and len(rows) == 1:
                return rows[0]["name"]
            if relative.startswith(source_path + "/"):
                requested = relative.removeprefix(source_path + "/")
                if any(row["name"] == requested for row in rows):
                    return requested
        return name
    if not relative.startswith("applications/"):
        return name
    from openprogram.programs._programs import KNOWN_PROGRAMS

    for program in KNOWN_PROGRAMS:
        if name in {program.install_dir, program.package, program.repo_dir_name}:
            return program.function
    return name


def _registered_vanilla_callables() -> dict[str, list[dict[str, str]]]:
    """Index regular built-ins by their physical category/package path."""
    from openprogram.programs import agent_tools

    indexed: dict[str, list[dict[str, str]]] = {}
    for tool in agent_tools(toolset="full", include_disabled=True):
        if getattr(tool, "_is_agentic", False) or getattr(tool, "_mcp_server", None):
            continue
        module = str(getattr(tool, "_source_module", ""))
        if not module.startswith(_VANILLA_MODULE_PREFIX):
            continue
        parts = module.removeprefix(_VANILLA_MODULE_PREFIX).split(".")
        if len(parts) < 2:
            continue
        package = (_VANILLA_RELATIVE / parts[0] / parts[1]).as_posix()
        indexed.setdefault(package, []).append({
            "name": tool.name,
            "description": (tool.description or "").strip().split("\n", 1)[0],
        })
    for rows in indexed.values():
        rows.sort(key=lambda row: row["name"])
    return indexed


def _registered_agentic_callables() -> dict[str, list[dict]]:
    """Index Agentic Programs by source path, independent of folder depth."""
    from openprogram.agentic_programming.function import _registry

    indexed: dict[str, list[dict]] = {}
    for name, registered in _registry.copy().items():
        fn = getattr(registered, "_fn", None) or registered
        module = str(getattr(fn, "__module__", ""))
        if not module.startswith(_AGENTIC_MODULE_PREFIX):
            continue
        try:
            raw_source = inspect.getsourcefile(fn)
            source = Path(raw_source).resolve() if raw_source else None
        except (OSError, TypeError):
            source = None
        if source is None or not source.is_file() or not _inside_programs(source):
            continue
        try:
            relative_source = source.relative_to(PROGRAMS_ROOT.resolve())
        except ValueError:
            continue
        source_path = (
            relative_source.parent
            if source.name in {"__init__.py", "workflow.py"}
            else relative_source.with_suffix("")
        ).as_posix()
        if not source_path.startswith(_AGENTIC_RELATIVE.as_posix() + "/"):
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


def _agentic_entries(
    relative: str, indexed: dict[str, list[dict]] | None = None,
) -> list[dict]:
    """List immediate categories or registered functions below a path."""
    if indexed is None:
        indexed = _registered_agentic_callables()
    if relative in indexed:
        rows = indexed[relative]
        if len(rows) > 1:
            return [
                {
                    "name": row["name"],
                    "path": f"{relative}/{row['name']}",
                    "kind": "file",
                    "program_kind": "workflow",
                    "has_children": False,
                    "callable_name": row["name"],
                    "description": row["description"],
                    "logic_path": f"{relative}/{row['name']}",
                }
                for row in rows
            ]

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
            or remainder[0] in _WORKFLOW_INTERNAL_NAMES
        ):
            continue
        child_path = (prefix / remainder[0]).as_posix()
        if len(remainder) > 1 or len(rows) > 1:
            entries[child_path] = {
                "name": remainder[0],
                "path": child_path,
                "kind": "folder",
                "program_kind": None,
                "has_children": True,
            }
            continue
        row = rows[0]
        entries[child_path] = {
            "name": row["name"],
            "path": child_path,
            "kind": "file",
            "program_kind": "workflow",
            "has_children": False,
            "callable_name": row["name"],
            "description": row["description"],
            "logic_path": source_path,
        }
    return sorted(entries.values(), key=lambda entry: entry["name"].lower())


def _list_entries(relative: str) -> dict:
    registered_agentic = (
        _registered_agentic_callables()
        if relative.startswith("workflow/")
        else {}
    )
    if relative not in registered_agentic:
        _safe_directory(relative)
    branches = {
        "": ("tools", "workflow", "applications"),
    }
    leaf_categories = {"applications"}
    if relative in branches:
        entries = [
            {
                "name": Path(path).name,
                "path": path,
                "kind": "folder",
                "program_kind": None,
                "has_children": True,
            }
            for path in branches[relative]
            if (PROGRAMS_ROOT / path).is_dir()
        ]
    elif relative == "workflow" or relative.startswith("workflow/"):
        entries = _agentic_entries(relative, registered_agentic or None)
        if relative == "workflow":
            existing = {entry["path"] for entry in entries}
            for path, source in sorted(_entity_paths().items()):
                if Path(path).parent.as_posix() != "workflow" or path in existing:
                    continue
                if Path(path).stem in _WORKFLOW_INTERNAL_NAMES:
                    continue
                entries.append({
                    "name": Path(path).stem,
                    "path": path,
                    "kind": "folder" if source.is_dir() else "file",
                    "program_kind": "workflow",
                    "has_children": False,
                    "callable_name": _callable_name(path),
                    "logic_path": path,
                })
            entries.sort(key=lambda entry: entry["name"].lower())
        if not entries:
            raise FileNotFoundError(relative)
    elif relative == "tools":
        entries = [
            {
                "name": child.name,
                "path": (_VANILLA_RELATIVE / child.name).as_posix(),
                "kind": "folder",
                "program_kind": None,
                "has_children": True,
            }
            for child in _visible_children(PROGRAMS_ROOT / _VANILLA_RELATIVE)
            if child.is_dir() and not child.name.startswith("_")
        ]
    elif Path(relative).parent == _VANILLA_RELATIVE:
        directory = PROGRAMS_ROOT / relative
        callables = _registered_vanilla_callables()
        entries = []
        for source in _visible_children(directory):
            if source.name.startswith("_") or not (
                source.is_dir() or source.suffix == ".py"
            ):
                continue
            source_path = (Path(relative) / source.stem).as_posix()
            rows = callables.get(source_path, [])
            if len(rows) == 1:
                row = rows[0]
                entries.append({
                    "name": row["name"],
                    "path": source_path,
                    "kind": "file",
                    "program_kind": "vanilla_function",
                    "has_children": False,
                    "callable_name": row["name"],
                    "description": row["description"],
                    "logic_path": source_path,
                })
            elif rows:
                entries.append({
                    "name": source.stem,
                    "path": source_path,
                    "kind": "folder",
                    "program_kind": None,
                    "has_children": True,
                })
        entries.sort(key=lambda entry: entry["name"].lower())
    elif relative in _entity_paths() and relative.startswith("tools/"):
        entries = [
            {
                "name": row["name"],
                "path": f"{relative}/{row['name']}",
                "kind": "file",
                "program_kind": "vanilla_function",
                "has_children": False,
                "callable_name": row["name"],
                "description": row["description"],
                "logic_path": relative,
            }
            for row in _registered_vanilla_callables().get(relative, [])
        ]
    elif relative in leaf_categories:
        entries = [
            {
                "name": Path(path).stem,
                "path": path,
                "kind": "folder" if source.is_dir() else "file",
                "program_kind": _program_kind(path),
                "has_children": False,
                "callable_name": _callable_name(path),
                "logic_path": path,
            }
            for path, source in sorted(_entity_paths().items())
            if Path(path).parent.as_posix() == relative
        ]
    else:
        raise FileNotFoundError(relative)
    payload = {"path": relative, "entries": entries}
    if not relative:
        payload["default_selection"] = _default_selection()
    return payload


def _module_prefix(relative: str) -> str:
    path = Path(relative)
    parts = list(path.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return "openprogram.programs." + ".".join(parts)


def _python_sources(path: Path) -> tuple[list[Path], bool]:
    if path.is_file():
        sources = [path] if path.suffix == ".py" and _inside_programs(path) else []
        return sources, False
    sources: list[Path] = []
    for directory, names, files in os.walk(path, followlinks=False):
        base = Path(directory)
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
            tree = ast.parse(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, SyntaxError):
            warnings.add("source_parse_failed")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level:
                    source_relative = next(
                        source.relative_to(root)
                        for root in _catalog_roots()
                        if source == root or root in source.parents
                    )
                    source_module = _module_prefix(source_relative.as_posix())
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


def _direct_calls(
    relative: str, entities: dict[str, Path]
) -> tuple[list[str], set[str]]:
    prefixes = sorted(
        ((_module_prefix(path), path) for path in entities),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    targets: set[str] = set()
    if relative.startswith("workflow/"):
        modules, warnings = _called_import_modules(
            entities[relative], _callable_name(relative),
        )
    else:
        modules, warnings = _imported_modules(entities[relative])
    for module in modules:
        for prefix, target in prefixes:
            if target != relative and (module == prefix or module.startswith(prefix + ".")):
                targets.add(target)
                break
    if relative.startswith("workflow/"):
        nodes, _, local_warnings = _analysis_nodes(
            entities[relative], _callable_name(relative),
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
                and target.startswith("workflow/")
                and target_source == entities[relative]
                and _callable_name(target) in called_names
            ):
                targets.add(target)
    return sorted(targets), warnings


def _analysis_nodes(
    path: Path, entry_name: str | None = None,
) -> tuple[list[ast.AST], list[ast.Module], set[str]]:
    """Return one entry's reachable local functions and parsed modules."""
    trees: list[ast.Module] = []
    warnings: set[str] = set()
    sources, truncated = _python_sources(path)
    if truncated:
        warnings.add("source_file_limit")
    for source in sources:
        try:
            if source.stat().st_size > _MAX_SOURCE_BYTES:
                warnings.add("oversized_source")
                continue
            trees.append(ast.parse(source.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError, SyntaxError):
            warnings.add("source_parse_failed")
            continue

    if not entry_name:
        return list(trees), trees, warnings
    functions = {
        node.name: node
        for tree in trees
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    entry = functions.get(entry_name)
    if entry is None:
        warnings.add("callable_source_not_found")
        return [], trees, warnings
    reachable: list[ast.AST] = []
    pending = deque([entry])
    seen: set[str] = set()
    while pending:
        node = pending.popleft()
        if node.name in seen:
            continue
        seen.add(node.name)
        reachable.append(node)
        for call in ast.walk(node):
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
                helper = functions.get(call.func.id)
                if helper is not None and helper.name not in seen:
                    pending.append(helper)
    return reachable, trees, warnings


def _called_import_modules(
    path: Path, entry_name: str,
) -> tuple[set[str], set[str]]:
    """Return imported call targets reachable from one Agentic entry."""
    nodes, trees, warnings = _analysis_nodes(path, entry_name)
    aliases: dict[str, str] = {}
    for tree in trees:
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    local = alias.asname or alias.name.split(".", 1)[0]
                    aliases[local] = alias.name
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level:
                    try:
                        relative = path.resolve().relative_to(
                            PROGRAMS_ROOT.resolve(),
                        )
                        source_module = _module_prefix(relative.as_posix())
                        package = (
                            source_module
                            if path.is_dir()
                            else source_module.rpartition(".")[0]
                        )
                        module = importlib.util.resolve_name(
                            "." * node.level + module, package,
                        )
                    except (ImportError, ValueError):
                        continue
                for alias in node.names:
                    if alias.name != "*":
                        aliases[alias.asname or alias.name] = (
                            f"{module}.{alias.name}" if module else alias.name
                        )

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
                if module == "openprogram.agentic_programming":
                    for alias in node.names:
                        if alias.name in _AGENTIC_PRIMITIVES:
                            direct[alias.asname or alias.name] = alias.name
                elif module.startswith("openprogram.agentic_programming."):
                    primitive = module.rsplit(".", 1)[-1]
                    for alias in node.names:
                        if alias.name in _AGENTIC_PRIMITIVES:
                            direct[alias.asname or alias.name] = alias.name
                        elif primitive in _AGENTIC_PRIMITIVES and alias.name == primitive:
                            direct[alias.asname or alias.name] = primitive
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "openprogram.agentic_programming":
                        modules[alias.asname or "openprogram"] = alias.name

    for root in nodes:
        for node in ast.walk(root):
            if not isinstance(node, ast.Call):
                continue
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
    depths = {relative: 0}
    queue = deque([relative])
    edges: list[dict[str, str]] = []
    seen_edges: set[tuple[str, str]] = set()
    analysis_warnings: set[str] = set()
    while queue and len(depths) < 128:
        source = queue.popleft()
        targets, warnings = _direct_calls(source, entities)
        analysis_warnings.update(warnings)
        for target in targets:
            edge = (source, target)
            if edge not in seen_edges:
                seen_edges.add(edge)
                edges.append({"source": source, "target": target})
            if target not in depths:
                depths[target] = depths[source] + 1
                queue.append(target)
        entry_name = (
            _callable_name(source)
            if source.startswith("workflow/")
            else None
        )
        primitives, warnings = _called_agentic_primitives(
            entities[source], entry_name=entry_name,
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
    nodes = [
        {
            "id": path,
            "name": Path(path).stem,
            "path": path,
            "program_kind": (
                "runtime_primitive"
                if path.startswith("agentic_programming/")
                else _program_kind(path)
            ),
            "depth": depth,
        }
        for path, depth in sorted(depths.items(), key=lambda item: (item[1], Path(item[0]).stem))
    ]
    if queue:
        analysis_warnings.add("node_limit")
    return {
        "root": relative,
        "nodes": nodes,
        "edges": edges,
        "analysis_complete": not analysis_warnings,
        "analysis_warnings": sorted(analysis_warnings),
    }


def _emit(event: str, data: dict) -> None:
    """Broadcast a typed event to all connected WS clients. No-op if the
    server module isn't initialised yet (e.g. during tests). Mirrors
    ``routes/skills.py``'s helper."""
    try:
        from openprogram.webui import server as _server
        _server._broadcast(_json.dumps({"type": event, **data}, default=str))
    except Exception:
        pass


def register(app) -> None:
    @app.get("/api/programs/explorer")
    def programs_explorer(path: str = ""):
        try:
            return JSONResponse(content=_list_entries(path.strip("/")))
        except ValueError:
            return JSONResponse(
                content={"error": "invalid programs path"}, status_code=400,
            )
        except FileNotFoundError:
            return JSONResponse(
                content={"error": "programs path not found"}, status_code=404,
            )

    @app.get("/api/programs/logic")
    def programs_logic(path: str):
        try:
            return JSONResponse(content=_program_logic(path.strip("/")))
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
        """Re-scan ``programs/applications/`` for newly-installed programs.

        Returns ``{"added": [...], "total": N}``. Broadcasts
        ``programs:changed`` when ``added`` is non-empty so the function
        list refreshes live in every open tab.
        """
        try:
            from openprogram.programs._registry import rescan
            result = rescan()
        except Exception as e:  # noqa: BLE001
            return JSONResponse(
                content={"ok": False, "error": f"{type(e).__name__}: {e}"},
                status_code=500,
            )
        if result.get("added"):
            _emit("programs:changed", {"added": result["added"]})
        return JSONResponse(content={"ok": True, **result})
