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
_ROOT_ORDER = {"functions": 0, "workflows": 1, "applications": 2}
_MAX_SOURCE_FILES = 200
_MAX_SOURCE_BYTES = 1_000_000


def _inside_programs(path: Path) -> bool:
    root = PROGRAMS_ROOT.resolve()
    try:
        resolved = path.resolve()
    except OSError:
        return False
    return resolved == root or root in resolved.parents


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
    if len(parts) == 3 and parts[:2] == ("functions", "vanilla"):
        return "vanilla_function"
    if len(parts) == 3 and parts[:2] == ("functions", "agentic"):
        return "agentic_function"
    if len(parts) == 2 and parts[0] == "workflows":
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
    for parent in (
        Path("functions/vanilla"), Path("functions/agentic"),
        Path("workflows"), Path("applications"),
    ):
        directory = PROGRAMS_ROOT / parent
        if not directory.is_dir():
            continue
        for child in _visible_children(directory):
            if child.name.startswith("_"):
                continue
            if child.is_dir() or child.suffix == ".py":
                relative = child.relative_to(PROGRAMS_ROOT).as_posix()
                if _program_kind(relative):
                    entities[relative] = child
    return entities


def _default_selection() -> str | None:
    entities = _entity_paths()
    for prefix in ("workflows/", "functions/agentic/", "applications/", "functions/vanilla/"):
        matches = sorted(path for path in entities if path.startswith(prefix))
        if matches:
            return matches[0]
    return None


def _callable_name(relative: str) -> str:
    """Return the registered function name for a Programs entity."""
    name = Path(relative).stem
    if not relative.startswith("applications/"):
        return name
    from openprogram.programs._programs import KNOWN_PROGRAMS

    for program in KNOWN_PROGRAMS:
        if name in {program.install_dir, program.package, program.repo_dir_name}:
            return program.function
    return name


def _list_entries(relative: str) -> dict:
    _safe_directory(relative)
    branches = {
        "": ("functions", "workflows", "applications"),
        "functions": ("functions/vanilla", "functions/agentic"),
    }
    leaf_categories = {
        "functions/vanilla", "functions/agentic", "workflows", "applications",
    }
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
    elif relative in leaf_categories:
        entries = [
            {
                "name": Path(path).stem,
                "path": path,
                "kind": "folder" if source.is_dir() else "file",
                "program_kind": _program_kind(path),
                "has_children": False,
                "callable_name": _callable_name(path),
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
                    source_relative = source.relative_to(PROGRAMS_ROOT)
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
    modules, warnings = _imported_modules(entities[relative])
    for module in modules:
        for prefix, target in prefixes:
            if target != relative and (module == prefix or module.startswith(prefix + ".")):
                targets.add(target)
                break
    return sorted(targets), warnings


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
    nodes = [
        {
            "id": path,
            "name": Path(path).stem,
            "path": path,
            "program_kind": _program_kind(path),
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
