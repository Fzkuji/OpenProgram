"""Read the workflow project index, search catalog, and resolve identifiers."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from ..errors import InvalidWorkflow
from . import validation

PROJECT_CANDIDATE_LIMIT = 8
WORKFLOW_INPUT_SCHEMA = {
    "type": "object",
    "properties": {"task": {"type": "string"}},
    "required": ["task"],
}
WORKFLOW_OUTPUT_SCHEMA = {"type": "object"}


def _workflow_projects_root() -> Path:
    import openprogram.programs as programs_package

    from openprogram.programs._programs import owner_programs_roots

    package_root = Path(programs_package.__file__).resolve().parent
    source_roots = owner_programs_roots()
    if package_root in source_roots or not source_roots:
        return package_root / "workflow"
    return source_roots[0] / "workflow"


def _safe_project_id(value: object) -> str:
    project_id = str(value or "")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", project_id):
        raise InvalidWorkflow("invalid workflow project id")
    return project_id


def _project_tokens(value: object) -> set[str]:
    text = str(value or "").lower()
    tokens = set(re.findall(r"[a-z0-9_]+", text))
    for segment in re.findall(r"[\u4e00-\u9fff]+", text):
        tokens.update(segment[index : index + 2] for index in range(len(segment) - 1))
        if len(segment) == 1:
            tokens.add(segment)
    return tokens


def _project_pyproject(
    project_id: str,
    metadata: dict,
    *,
    workflow_dependencies: dict[str, str] | None = None,
) -> str:
    text = (
        "[project]\n"
        f"name = {json.dumps(project_id)}\n"
        'version = "0.1.0"\n'
        f"description = {json.dumps(metadata['summary'], ensure_ascii=False)}\n"
        f"keywords = {json.dumps(metadata['tags'], ensure_ascii=False)}\n\n"
        "[tool.openprogram]\n"
        f"display-name = {json.dumps(metadata['name'], ensure_ascii=False)}\n"
    )
    entrypoint = str(metadata.get("entrypoint") or "")
    if entrypoint:
        text += (
            '\n[project.entry-points."openprogram.workflows"]\n'
            f"{entrypoint} = "
            f"{json.dumps(f'workflows.{entrypoint}:{entrypoint}')}\n"
        )
    if workflow_dependencies:
        text += "\n[tool.openprogram.workflow-dependencies]\n"
        for name in sorted(workflow_dependencies):
            text += f"{name} = {json.dumps(str(workflow_dependencies[name]))}\n"
    return text


def _read_repository_metadata(
    project_dir: Path,
    *,
    expected_project_id: str = "",
) -> dict:
    path = project_dir / "pyproject.toml"
    if path.is_symlink():
        raise InvalidWorkflow("workflow project pyproject must not be a symlink")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    project = data.get("project")
    tool = data.get("tool", {}).get("openprogram", {})
    if not isinstance(project, dict) or not isinstance(tool, dict):
        raise InvalidWorkflow("invalid workflow project pyproject")
    project_id = _safe_project_id(project.get("name"))
    if project_id != (expected_project_id or project_dir.name):
        raise InvalidWorkflow("workflow project name does not match its directory")
    display_name = str(tool.get("display-name") or project_id).strip()
    entrypoint_groups = project.get("entry-points", {})
    if not isinstance(entrypoint_groups, dict):
        raise InvalidWorkflow("workflow project entry points must be a table")
    metadata = validation._validate_project_metadata(
        {
            "name": display_name,
            "summary": project.get("description"),
            "tags": project.get("keywords"),
        }
    )
    entrypoints = entrypoint_groups.get("openprogram.workflows", {})
    if entrypoints:
        if display_name != project_id:
            raise InvalidWorkflow(
                "workflow project display-name must match its project name"
            )
        if not isinstance(entrypoints, dict) or len(entrypoints) != 1:
            raise InvalidWorkflow("workflow project must expose one entry point")
        entrypoint, target = next(iter(entrypoints.items()))
        if entrypoint != project_id or target != f"workflows.{entrypoint}:{entrypoint}":
            raise InvalidWorkflow(
                "workflow project entry point does not match its package"
            )
        metadata["entrypoint"] = entrypoint
    return metadata


def _read_project_index(project_dir: Path) -> dict:
    from . import repository

    if project_dir.is_symlink():
        raise InvalidWorkflow("workflow project directory must not be a symlink")
    if not (project_dir / ".git").exists():
        raise InvalidWorkflow("workflow project must be a Git repository")
    project_id = _safe_project_id(project_dir.name)
    return {
        "project_id": project_id,
        "active_revision": repository._git(project_dir, "rev-parse", "HEAD"),
        "project_metadata": _read_repository_metadata(project_dir),
    }


def _search_projects(task: str) -> list[dict]:
    root = _workflow_projects_root()
    if not root.exists() or root.is_symlink():
        return []
    query = _project_tokens(task)
    matches = []
    for project_dir in root.iterdir():
        if not project_dir.is_dir() or project_dir.name.startswith("."):
            continue
        try:
            row = _read_project_index(project_dir)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        metadata = row["project_metadata"]
        haystack = " ".join(
            [
                metadata["name"],
                metadata["summary"],
                *metadata["tags"],
            ]
        )
        score = len(query & _project_tokens(haystack))
        matches.append((score, row))
    matches.sort(key=lambda item: (-item[0], item[1]["project_id"]))
    return [row for _, row in matches[:PROJECT_CANDIDATE_LIMIT]]


def _slugify_project_name(name: str) -> str:
    import hashlib

    slug = "-".join(re.findall(r"[a-z0-9]+", name.lower()))[:64].strip("-")
    if slug:
        return slug
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:10]
    return f"workflow-{digest}"
