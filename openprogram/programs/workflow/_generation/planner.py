"""Generate and parse workflow candidates, including reuse-or-create."""

from __future__ import annotations

import ast
import json
import re
import traceback
from pathlib import Path
from typing import Callable, Optional

from ..errors import InvalidWorkflow
from .._project import catalog
from .._project import repository
from .._project import validation
from . import prompts


def _run_planner_turn(
    session_id: str,
    prompt: str,
    *,
    agent_id: str,
    spawn_caller: Optional[str],
    label: str,
) -> str:
    _ = session_id, agent_id, spawn_caller, label
    from openprogram.agentic_programming import agent

    return agent(prompt, tools=list(prompts.PLANNER_TOOLS))


def _function_catalog(functions: dict[str, Callable]) -> str:
    rows = []
    for name, function in sorted(functions.items()):
        inner = getattr(function, "_fn", function)
        module = str(getattr(inner, "__module__", "") or "")
        if module.startswith("openprogram.programs.workflow."):
            rows.append(f"- from {module} import {name}")
        else:
            rows.append(f"- {name}(...)")
    return "\n".join(rows) or "(none)"


def _workflow_import_catalog() -> str:
    root = catalog._workflow_projects_root()
    if not root.exists() or root.is_symlink():
        return "(none)"
    rows = []
    for project_dir in sorted(root.iterdir()):
        if (
            not project_dir.is_dir()
            or project_dir.is_symlink()
            or project_dir.name.startswith(".")
            or not (project_dir / ".git").exists()
        ):
            continue
        try:
            candidate, revision = repository._checkout_head(project_dir)
            metadata = candidate["project_metadata"]
            entrypoint = str(metadata.get("entrypoint") or "")
            if not entrypoint:
                continue
            rows.append(
                f"- from workflows.{entrypoint} import {entrypoint}"
                f"  # {metadata['summary']} @ {revision}"
            )
        except (InvalidWorkflow, OSError):
            continue
    return "\n".join(rows) or "(none)"


_CODE_BLOCK = re.compile(r"```python\s*\n(.*?)\n?\s*```", re.DOTALL | re.IGNORECASE)


def _extract_source(reply: str) -> str:
    match = _CODE_BLOCK.search(reply or "")
    if not match:
        raise InvalidWorkflow("planner reply did not contain a Python code block")
    return match.group(1).strip() + "\n"


def _validate_source(source: str) -> ast.Module:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
        raise InvalidWorkflow(detail) from exc
    if any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(tree)):
        raise InvalidWorkflow("workflow imports are forbidden")
    workflows = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "workflow"
    ]
    if len(workflows) != 1 or not isinstance(workflows[0], ast.FunctionDef):
        raise InvalidWorkflow("workflow source must define exactly one def workflow()")
    args = workflows[0].args
    if args.posonlyargs or args.args or args.kwonlyargs or args.vararg or args.kwarg:
        raise InvalidWorkflow("workflow() must not accept arguments")
    return tree


def _validated_reply(reply: str) -> str:
    source = _extract_source(reply)
    _validate_source(source)
    return source


def _parse_json_reply(reply: str) -> dict:
    text = str(reply or "").strip()
    match = re.search(r"```(?:json)?\s*\n(.*?)\n?\s*```", text, re.DOTALL)
    if match:
        text = match.group(1).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidWorkflow(f"planner reply was not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise InvalidWorkflow("planner reply must be one JSON object")
    return value


def _request_auto_decision(
    task: str,
    candidates: list[dict],
    *,
    session_id: str,
    agent_id: str,
    spawn_caller: Optional[str],
) -> dict:
    prompt = prompts._auto_decision_prompt(task, candidates)
    candidate_ids = {row["workflow_id"] for row in candidates}
    last_error = ""
    for attempt in range(1, prompts.AUTO_DECISION_ATTEMPTS + 1):
        reply = _run_planner_turn(
            session_id,
            prompt,
            agent_id=agent_id,
            spawn_caller=spawn_caller,
            label="workflow selection",
        )
        try:
            decision = _parse_json_reply(reply)
            action = str(decision.get("action") or "")
            if action not in {"reuse", "create"}:
                raise InvalidWorkflow("auto workflow action must be reuse or create")
            if action == "create":
                if not candidates:
                    if set(decision) not in (
                        {"action"},
                        {"action", "missing_capability"},
                    ):
                        raise InvalidWorkflow(
                            "empty-catalog create decision has unsupported fields"
                        )
                    return {"action": action}
                if set(decision) != {"action", "missing_capability"}:
                    raise InvalidWorkflow(
                        "create decision with candidates must name missing_capability"
                    )
                missing = str(decision.get("missing_capability") or "").strip()
                if not missing:
                    raise InvalidWorkflow("missing_capability must be non-empty")
                return {"action": action, "missing_capability": missing}
            if set(decision) != {"action", "workflow_id"}:
                raise InvalidWorkflow(
                    "reuse decision must contain only action and workflow_id"
                )
            workflow_id = catalog._safe_project_id(decision.get("workflow_id"))
            if workflow_id not in candidate_ids:
                raise InvalidWorkflow(
                    "reuse workflow_id must come from the current candidates"
                )
            return {"action": action, "workflow_id": workflow_id}
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt == prompts.AUTO_DECISION_ATTEMPTS:
                raise InvalidWorkflow(
                    "workflow selection failed after "
                    f"{prompts.AUTO_DECISION_ATTEMPTS} attempts: {last_error}"
                ) from exc
            prompt = (
                prompts._auto_decision_prompt(task, candidates)
                + f"\n\n<concrete_error>\n{last_error}\n"
                "</concrete_error>\nReturn a corrected decision JSON object."
            )
    raise InvalidWorkflow("workflow selection failed: " + last_error)


def _request_project_candidate(
    task: str,
    functions: dict[str, Callable],
    *,
    session_id: str,
    agent_id: str,
    spawn_caller: Optional[str],
    base: Optional[dict] = None,
    error: str = "",
    state: Optional[dict] = None,
    require_new_name: bool = False,
    pinned_snapshot: Optional[Path] = None,
    pinned_dependencies: Optional[dict] = None,
) -> dict:
    prompt = prompts._author_prompt(
        task, functions, base=base, error=error, state=state
    )
    last_error = ""
    for attempt in range(1, prompts.PROJECT_AUTHOR_ATTEMPTS + 1):
        reply = _run_planner_turn(
            session_id,
            prompt,
            agent_id=agent_id,
            spawn_caller=spawn_caller,
            label="workflow project author",
        )
        try:
            candidate = validation._validate_project_candidate(_parse_json_reply(reply))
            entrypoint = str(candidate["project_metadata"].get("entrypoint") or "")
            base_entrypoint = str(
                (base or {}).get("project_metadata", {}).get("entrypoint") or ""
            )
            if base_entrypoint and entrypoint != base_entrypoint:
                raise InvalidWorkflow(
                    "revised workflow package must keep its public name"
                )
            if (
                require_new_name
                and entrypoint
                and (catalog._workflow_projects_root() / entrypoint).exists()
            ):
                raise InvalidWorkflow(f"workflow project already exists: {entrypoint}")
            repository._resolve_workflow_dependencies(
                candidate,
                pinned_snapshot=pinned_snapshot,
                pinned_dependencies=pinned_dependencies,
            )
            return candidate
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt == prompts.PROJECT_AUTHOR_ATTEMPTS:
                raise InvalidWorkflow(
                    "workflow project author failed validation after "
                    f"{prompts.PROJECT_AUTHOR_ATTEMPTS} attempts: {last_error}"
                ) from exc
            prompt = prompts._author_prompt(
                task,
                functions,
                base=base,
                error=last_error,
                state=state,
            )
    raise InvalidWorkflow("workflow project author failed validation: " + last_error)


def _request_valid_source(
    task: str,
    source: str,
    state: dict,
    *,
    session_id: str,
    agent_id: str,
    spawn_caller: Optional[str],
    functions: dict[str, Callable],
) -> str:
    prompt = (
        prompts._plan_prompt(task, functions)
        if not source
        else prompts._rewrite_prompt(
            task, source, state, state.get("last_error", ""), functions
        )
    )
    candidate = source
    while True:
        try:
            reply = _run_planner_turn(
                session_id,
                prompt,
                agent_id=agent_id,
                spawn_caller=spawn_caller,
                label="agentic workflow planner",
            )
            if not source and reply.strip() == "SINGLE":
                return "SINGLE"
            candidate = reply
            candidate = _extract_source(candidate)
            _validate_source(candidate)
            return candidate
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            state["last_error"] = error
            if "reply" in locals() and not candidate:
                candidate = reply
            prompt = prompts._rewrite_prompt(task, candidate, state, error, functions)
