"""Search published workflow projects."""

from __future__ import annotations

import json

from openprogram.agentic_programming.function import agentic_function

from ._project import catalog


@agentic_function(
    input={
        "task": {
            "description": "The task to match against the workflow catalog",
            "multiline": True,
        },
    },
)
def search_workflows(task: str) -> dict:
    """Deterministically search the local workflow catalog (read-only).

    Returns ranked candidates with their pinned Git revision, contract
    schemas, matched terms, and declared permissions. Never calls a
    model, writes files, executes a candidate, or publishes.
    """
    query = catalog._project_tokens(task)
    matches: list[tuple[int, dict]] = []
    root = catalog._workflow_projects_root()
    if root.exists() and not root.is_symlink():
        for project_dir in sorted(root.iterdir()):
            if not project_dir.is_dir() or project_dir.name.startswith("."):
                continue
            try:
                row = catalog._read_project_index(project_dir)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            metadata = row["project_metadata"]
            entrypoint = str(metadata.get("entrypoint") or "")
            # Legacy (entry.py) projects are resume-only; auto_workflow is
            # the user-only orchestration entry, never a candidate.
            if not entrypoint or entrypoint == "auto_workflow":
                continue
            haystack = " ".join(
                [
                    metadata["name"],
                    metadata["summary"],
                    *metadata["tags"],
                ]
            )
            matched = sorted(query & catalog._project_tokens(haystack))
            matches.append(
                (
                    len(matched),
                    {
                        "workflow_id": row["project_id"],
                        "revision": row["active_revision"],
                        "name": metadata["name"],
                        "summary": metadata["summary"],
                        "tags": metadata["tags"],
                        "retrieval_score": len(matched),
                        "matched_terms": matched,
                        "input_schema": catalog.WORKFLOW_INPUT_SCHEMA,
                        "output_schema": catalog.WORKFLOW_OUTPUT_SCHEMA,
                        "permissions": [],
                    },
                )
            )
    matches.sort(key=lambda item: (-item[0], item[1]["workflow_id"]))
    return {
        "workflows": [row for _, row in matches[: catalog.PROJECT_CANDIDATE_LIMIT]],
    }
