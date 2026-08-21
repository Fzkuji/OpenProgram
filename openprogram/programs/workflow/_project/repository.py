"""Git revisions, candidate directories, snapshots, and project file IO."""

from __future__ import annotations

import ast
import io
import json
import re
import shutil
import subprocess
import tarfile
import tomllib
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from openprogram.store.session.git_session import atomic_write_text

from ..errors import InvalidWorkflow
from . import catalog
from . import validation


def _git(project_dir: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(project_dir), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise InvalidWorkflow(f"git {' '.join(args)} failed: {detail}")
    return completed.stdout.strip()


def _read_workflow_dependencies(
    project_dir: Path,
    revision: str,
) -> dict[str, str]:
    try:
        content = _git(project_dir, "show", f"{revision}:pyproject.toml")
    except InvalidWorkflow:
        return {}
    data = tomllib.loads(content)
    tool = data.get("tool", {}).get("openprogram", {})
    raw = tool.get("workflow-dependencies")
    if not isinstance(raw, dict):
        return {}
    dependencies: dict[str, str] = {}
    for name, value in raw.items():
        project_id = catalog._safe_project_id(str(name))
        revision_value = str(value)
        if not re.fullmatch(r"[0-9a-f]{40}", revision_value):
            raise InvalidWorkflow("invalid pinned workflow dependency revision")
        dependencies[project_id] = revision_value
    return dependencies


def _project_manifest(candidate: dict) -> dict:
    helpers = sorted(path for path in candidate["files"] if path != "entry.py")
    return {
        "schema_version": validation.PROJECT_SCHEMA_VERSION,
        "files": [*helpers, "entry.py"],
        "entry_file": "entry.py",
        "entry_function": "workflow",
    }


def _write_candidate_directory(target: Path, candidate: dict) -> None:
    target.mkdir(parents=True, exist_ok=False)
    entrypoint = str(candidate["project_metadata"].get("entrypoint") or "")
    if entrypoint:
        package = target / "workflows" / entrypoint
        package.mkdir(parents=True)
        _write_repository_candidate(package, entrypoint, candidate)
        return
    atomic_write_text(target / "README.md", candidate["readme"])
    atomic_write_text(
        target / "workflow.json",
        json.dumps(_project_manifest(candidate), ensure_ascii=False, indent=2) + "\n",
    )
    for relative, source in candidate["files"].items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, source)


def _read_candidate_directory(directory: Path, metadata: dict) -> dict:
    if directory.is_symlink():
        raise InvalidWorkflow("workflow project directory must not be a symlink")
    entrypoint = str(metadata.get("entrypoint") or "")
    if entrypoint:
        return _read_repository_candidate(
            directory / "workflows" / entrypoint,
            expected_project_id=entrypoint,
        )
    manifest_path = directory / "workflow.json"
    readme_path = directory / "README.md"
    if manifest_path.is_symlink() or readme_path.is_symlink():
        raise InvalidWorkflow("workflow project files must not be symlinks")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != validation.PROJECT_SCHEMA_VERSION
    ):
        raise InvalidWorkflow("invalid workflow project manifest")
    paths = manifest.get("files")
    if not isinstance(paths, list) or len(paths) != len(set(map(str, paths))):
        raise InvalidWorkflow("invalid workflow project file list")
    allowed = {"README.md", "workflow.json", *map(str, paths)}
    for disk_path in directory.rglob("*"):
        if disk_path.is_symlink():
            raise InvalidWorkflow("workflow project files must not be symlinks")
        if (
            disk_path.is_file()
            and disk_path.relative_to(directory).as_posix() not in allowed
        ):
            raise InvalidWorkflow("workflow project contains an unlisted file")
    files = {}
    for raw_path in paths:
        relative = validation._validate_legacy_project_path(raw_path)
        path = directory / relative
        if path.is_symlink():
            raise InvalidWorkflow("workflow project files must not be symlinks")
        files[relative] = path.read_text(encoding="utf-8")
    candidate = validation._validate_project_candidate(
        {
            "project_metadata": metadata,
            "readme": readme_path.read_text(encoding="utf-8"),
            "files": files,
        },
        allow_legacy_entry=True,
    )
    if manifest != _project_manifest(candidate):
        raise InvalidWorkflow("workflow project manifest does not match its files")
    return candidate


def _workflow_imports(candidate: dict) -> list[str]:
    dependencies = set()
    for path, source in candidate["files"].items():
        if path.startswith("tests/"):
            continue
        for node in ast.parse(source, filename=path).body:
            if not isinstance(node, ast.ImportFrom) or node.level:
                continue
            parts = (node.module or "").split(".")
            if len(parts) == 2 and parts[0] == "workflows":
                dependencies.add(parts[1])
    return sorted(dependencies)


def _resolve_workflow_dependencies(
    candidate: dict,
    *,
    pinned_snapshot: Optional[Path] = None,
    pinned_dependencies: Optional[dict] = None,
) -> dict[str, tuple[dict, str]]:
    root = str(candidate["project_metadata"].get("entrypoint") or "")
    if not root:
        return {}
    pins = dict(pinned_dependencies or {})
    for name, revision in pins.items():
        catalog._safe_project_id(name)
        if not re.fullmatch(r"[0-9a-f]{40}", str(revision)):
            raise InvalidWorkflow("invalid pinned workflow dependency revision")
    resolved: dict[str, tuple[dict, str]] = {}
    visited: set[str] = set()
    visiting: list[str] = []

    def _pinned_dependency_candidate(dependency: str) -> tuple[dict, str]:
        revision = str(pins[dependency])
        package = (
            pinned_snapshot / "workflows" / dependency
            if pinned_snapshot is not None
            else None
        )
        if package is not None and package.exists():
            return (
                _read_repository_candidate(
                    package,
                    expected_project_id=dependency,
                ),
                revision,
            )
        dependency_dir = catalog._workflow_projects_root() / dependency
        candidate, checked = _checkout_revision(dependency_dir, revision)
        if checked != revision:
            raise InvalidWorkflow(
                f"workflow dependency {dependency} revision {revision} is unavailable"
            )
        return candidate, revision

    def visit(name: str, current: dict) -> None:
        if name in visiting:
            cycle = " -> ".join([*visiting[visiting.index(name) :], name])
            raise InvalidWorkflow(f"workflow dependency cycle: {cycle}")
        if name in visited:
            return
        visiting.append(name)
        try:
            for dependency in _workflow_imports(current):
                if dependency in visiting:
                    cycle = " -> ".join(
                        [
                            *visiting[visiting.index(dependency) :],
                            dependency,
                        ]
                    )
                    raise InvalidWorkflow(f"workflow dependency cycle: {cycle}")
                if dependency in visited:
                    continue
                if dependency in pins:
                    dependency_candidate, revision = _pinned_dependency_candidate(
                        dependency,
                    )
                else:
                    try:
                        index, dependency_candidate, _ = _active_project(dependency)
                    except (InvalidWorkflow, OSError) as exc:
                        raise InvalidWorkflow(
                            f"workflow dependency {dependency} is unavailable: {exc}"
                        ) from exc
                    revision = index["active_revision"]
                if (
                    dependency_candidate["project_metadata"].get("entrypoint")
                    != dependency
                ):
                    raise InvalidWorkflow(
                        f"workflow dependency {dependency} is not a standard package"
                    )
                visit(dependency, dependency_candidate)
                resolved[dependency] = (
                    dependency_candidate,
                    revision,
                )
        finally:
            visiting.pop()
        visited.add(name)

    visit(root, candidate)
    return resolved


def _replace_snapshot(
    instance: Path,
    candidate: dict,
    *,
    pinned_dependencies: Optional[dict] = None,
) -> dict[str, str]:
    staging = instance / f".snapshot-{uuid.uuid4().hex}.tmp"
    snapshot = instance / "snapshot"
    backup = instance / f".snapshot-{uuid.uuid4().hex}.old"
    dependencies = _resolve_workflow_dependencies(
        candidate,
        pinned_snapshot=instance / "snapshot",
        pinned_dependencies=pinned_dependencies,
    )
    try:
        _write_candidate_directory(staging, candidate)
        for name, (dependency, _revision) in dependencies.items():
            package = staging / "workflows" / name
            package.mkdir()
            _write_repository_candidate(package, name, dependency)
            _read_repository_candidate(
                package,
                expected_project_id=name,
            )
        _read_candidate_directory(staging, candidate["project_metadata"])
        if snapshot.exists():
            snapshot.replace(backup)
        staging.replace(snapshot)
        if backup.exists():
            shutil.rmtree(backup)
        return {
            name: revision for name, (_dependency, revision) in dependencies.items()
        }
    finally:
        if staging.exists():
            shutil.rmtree(staging)
        if backup.exists() and not snapshot.exists():
            backup.replace(snapshot)


def _write_repository_candidate(
    directory: Path,
    project_id: str,
    candidate: dict,
    *,
    workflow_dependencies: Optional[dict[str, str]] = None,
) -> None:
    for child in directory.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    atomic_write_text(
        directory / "pyproject.toml",
        catalog._project_pyproject(
            project_id,
            candidate["project_metadata"],
            workflow_dependencies=workflow_dependencies,
        ),
    )
    atomic_write_text(directory / "README.md", candidate["readme"])
    for relative, source in candidate["files"].items():
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, source)


def _read_repository_candidate(
    directory: Path,
    *,
    allow_legacy_entry: bool = False,
    expected_project_id: str = "",
) -> dict:
    metadata = catalog._read_repository_metadata(
        directory,
        expected_project_id=expected_project_id,
    )
    readme = directory / "README.md"
    if readme.is_symlink():
        raise InvalidWorkflow("workflow project README must not be a symlink")
    files: dict[str, str] = {}
    for path in directory.rglob("*"):
        parts = path.relative_to(directory).parts
        if ".git" in parts:
            continue
        if path.is_symlink():
            raise InvalidWorkflow("workflow project files must not be symlinks")
        if not path.is_file():
            continue
        relative = path.relative_to(directory).as_posix()
        if relative in {"README.md", "pyproject.toml"}:
            continue
        if metadata.get("entrypoint"):
            source_path = validation._validate_package_path(relative)
        else:
            source_path = validation._validate_legacy_project_path(relative)
        files[source_path] = path.read_text(encoding="utf-8")
    return validation._validate_project_candidate(
        {
            "project_metadata": metadata,
            "readme": readme.read_text(encoding="utf-8"),
            "files": files,
        },
        allow_legacy_entry=allow_legacy_entry,
    )


def _checkout_revision(project_dir: Path, revision: str) -> tuple[dict, str]:
    if project_dir.is_symlink() or not (project_dir / ".git").exists():
        raise InvalidWorkflow("workflow project must be a Git repository")
    revision = str(revision)
    archive = subprocess.run(
        ["git", "-C", str(project_dir), "archive", "--format=tar", revision],
        check=False,
        capture_output=True,
    )
    if archive.returncode:
        raise InvalidWorkflow(
            f"git archive failed: {archive.stderr.decode(errors='replace').strip()}"
        )
    with tempfile.TemporaryDirectory(prefix="openprogram-workflow-checkout-") as raw:
        checkout = Path(raw) / project_dir.name
        checkout.mkdir()
        try:
            with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as bundle:
                for member in bundle.getmembers():
                    member_path = Path(member.name)
                    if member_path.is_absolute() or ".." in member_path.parts:
                        raise InvalidWorkflow("invalid path in workflow Git archive")
                bundle.extractall(checkout, filter="data")
        except tarfile.TarError as exc:
            raise InvalidWorkflow("workflow Git archive is invalid") from exc
        return _read_repository_candidate(
            checkout,
            allow_legacy_entry=True,
        ), revision


def _checkout_head(project_dir: Path) -> tuple[dict, str]:
    revision = _git(project_dir, "rev-parse", "HEAD")
    return _checkout_revision(project_dir, revision)


def _active_project(project_id: str) -> tuple[dict, dict, Path]:
    project_id = catalog._safe_project_id(project_id)
    project_dir = catalog._workflow_projects_root() / project_id
    index = catalog._read_project_index(project_dir)
    candidate, revision = _checkout_head(project_dir)
    if revision != index["active_revision"]:
        raise InvalidWorkflow("workflow project HEAD changed while reading")
    return index, candidate, project_dir


def _copy_pinned_snapshot(
    instance: Path,
    project_id: str,
    revision: str,
) -> tuple[dict, dict]:
    project_id = catalog._safe_project_id(project_id)
    project_dir = catalog._workflow_projects_root() / project_id
    index = catalog._read_project_index(project_dir)
    candidate, checked_revision = _checkout_revision(project_dir, revision)
    if checked_revision != revision:
        raise InvalidWorkflow(
            f"workflow {project_id} revision {revision} is unavailable"
        )
    stored_dependencies = _read_workflow_dependencies(project_dir, revision)
    index = dict(index)
    index["workflow_dependencies"] = _replace_snapshot(
        instance,
        candidate,
        pinned_dependencies=stored_dependencies or None,
    )
    return index, candidate


def _copy_active_snapshot(instance: Path, project_id: str) -> tuple[dict, dict]:
    index = catalog._read_project_index(catalog._workflow_projects_root() / project_id)
    return _copy_pinned_snapshot(instance, project_id, index["active_revision"])


def _candidates_equal(left: dict, right: dict) -> bool:
    return (
        left.get("readme") == right.get("readme")
        and left.get("files") == right.get("files")
        and left.get("project_metadata") == right.get("project_metadata")
    )


def _publish_snapshot(
    instance: Path,
    *,
    project_id: str,
    action: str,
    metadata: dict,
    workflow_dependencies: Optional[dict[str, str]] = None,
) -> tuple[str, str]:
    root = catalog._workflow_projects_root()
    if root.is_symlink():
        raise InvalidWorkflow("workflow project root must not be a symlink")
    root.mkdir(parents=True, exist_ok=True)
    from openprogram.auth.credentials import _private_file_lock

    with _private_file_lock(root / ".git-publish", root=root, timeout=30):
        if action == "create":
            project_id = str(metadata.get("entrypoint") or "")
            if not project_id:
                project_id = catalog._slugify_project_name(metadata["name"])
            project_id = catalog._safe_project_id(project_id)
            if (root / project_id).exists():
                raise InvalidWorkflow(f"workflow project already exists: {project_id}")
        else:
            project_id = catalog._safe_project_id(project_id)
        project_dir = root / project_id
        if project_dir.is_symlink():
            raise InvalidWorkflow("workflow project directory must not be a symlink")
        candidate = _read_candidate_directory(
            instance / "snapshot",
            metadata,
        )
        if action == "create":
            staging = Path(tempfile.mkdtemp(prefix=f".{project_id}-", dir=root))
            try:
                _git(staging, "init", "-b", "main")
                _write_repository_candidate(
                    staging,
                    project_id,
                    candidate,
                    workflow_dependencies=workflow_dependencies,
                )
                _read_repository_candidate(
                    staging,
                    expected_project_id=project_id,
                    allow_legacy_entry=True,
                )
                _git(staging, "add", "--all")
                _git(
                    staging,
                    "-c",
                    "user.name=OpenProgram",
                    "-c",
                    "user.email=openprogram@localhost",
                    "commit",
                    "-m",
                    "Create workflow project",
                )
                staging.replace(project_dir)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
        else:
            catalog._read_project_index(project_dir)
            if _git(project_dir, "status", "--porcelain"):
                raise InvalidWorkflow("workflow project has uncommitted changes")
            worktree = Path(tempfile.mkdtemp(prefix=f".{project_id}-", dir=root))
            shutil.rmtree(worktree)
            try:
                _git(project_dir, "worktree", "add", "--detach", str(worktree), "HEAD")
                _write_repository_candidate(
                    worktree,
                    project_id,
                    candidate,
                    workflow_dependencies=workflow_dependencies,
                )
                _read_repository_candidate(
                    worktree,
                    expected_project_id=project_id,
                    allow_legacy_entry=True,
                )
                _git(worktree, "add", "--all")
                if _git(worktree, "status", "--porcelain"):
                    _git(
                        worktree,
                        "-c",
                        "user.name=OpenProgram",
                        "-c",
                        "user.email=openprogram@localhost",
                        "commit",
                        "-m",
                        "Revise workflow project",
                    )
                    revision = _git(worktree, "rev-parse", "HEAD")
                    _git(project_dir, "merge", "--ff-only", revision)
            finally:
                try:
                    _git(project_dir, "worktree", "remove", "--force", str(worktree))
                except InvalidWorkflow:
                    if worktree.exists():
                        shutil.rmtree(worktree)
        # Read HEAD inside the lock so a concurrent publish can't advance
        # it between our commit and this read.
        head = _git(project_dir, "rev-parse", "HEAD")
    return project_id, head


def _publish_candidate(candidate: dict, *, project_id: str, action: str) -> dict:
    """Snapshot a validated candidate in a scratch dir and atomically publish."""
    with tempfile.TemporaryDirectory(
        prefix="openprogram-workflow-author-",
    ) as raw:
        instance = Path(raw) / "candidate"
        instance.mkdir()
        workflow_dependencies = _replace_snapshot(instance, candidate)
        if action == "revise":
            project_dir = catalog._workflow_projects_root() / catalog._safe_project_id(
                project_id
            )
            active_revision = catalog._read_project_index(project_dir)[
                "active_revision"
            ]
            base_candidate, _ = _checkout_revision(project_dir, active_revision)
            if _candidates_equal(candidate, base_candidate):
                raise InvalidWorkflow("revision unchanged")
        published_id, revision = _publish_snapshot(
            instance,
            project_id=project_id,
            action=action,
            metadata=candidate["project_metadata"],
            workflow_dependencies=workflow_dependencies,
        )
    return {"workflow_id": published_id, "revision": revision}
