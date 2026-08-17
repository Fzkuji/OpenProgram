"""
Helpers for locating research artifacts.

Previously this module mirrored project artifacts into <repo>/deliverables/
when project_dir pointed outside the workspace — a workaround for a codex
sandbox that could not write arbitrary paths. That is no longer needed:
entry agentic functions now accept an explicit `work_dir` parameter and
set runtime.workdir so codex --cd runs where the user asked.

The API is kept so `stages/idea.py` still compiles, but every helper now
operates on the original project_dir — no mirror.
"""

from __future__ import annotations

import os
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def expanded_project_dir(project_dir: str) -> Path:
    return Path(os.path.expanduser(project_dir)).resolve(strict=False)


def project_artifact_roots(project_dir: str) -> list[Path]:
    return [expanded_project_dir(project_dir)]


def find_project_artifact(project_dir: str, *relative_paths: str) -> Path | None:
    root = expanded_project_dir(project_dir)
    for rel_path in relative_paths:
        candidate = root / rel_path
        if candidate.exists():
            return candidate
    return None


def writable_project_dir(project_dir: str) -> Path:
    return expanded_project_dir(project_dir)
