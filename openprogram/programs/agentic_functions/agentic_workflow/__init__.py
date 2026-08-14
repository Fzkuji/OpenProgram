"""Execute reusable planner-generated Agentic Programming projects.

Every new invocation selects, revises, or creates a versioned multi-file
workflow project. Each invocation also owns an immutable project snapshot under
``<session-repo>/workflows/<run_id>/`` with automatic call-boundary checkpoints.
The legacy ``code.py`` format remains readable only when explicitly resuming an
existing run.
"""
from __future__ import annotations

import ast
import base64
import functools
import hashlib
import importlib
import inspect
import json
import re
import shutil
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Callable, Optional

from openprogram.agentic_programming.function import (
    CancelledError,
    agentic_function as _real_agentic_function,
    current_session_id,
)
from openprogram.store.session.git_session import atomic_write_text

HANDOFF_SUMMARY_MAX_CHARS = 1200
HANDOFF_KIND = "workflow_handoff_v1"
MAX_ITEMS_EXECUTED = 40
PLANNER_TOOLS = ("read", "grep", "glob", "list")
PROJECT_SCHEMA_VERSION = 1
PROJECT_CANDIDATE_LIMIT = 8
PROJECT_RUNTIME_NAMES = {
    "llm", "agent", "goal", "validate_and_retry", "route", "conditional",
}

DELIVERY_INSTRUCTIONS = """Workflow delivery contract:
- Unless the task explicitly asks for the content in chat, save substantive deliverables
  such as reports, code, and tables in the current working directory.
- Return only a short handoff describing completed work and useful warnings or next steps.
- Do not return a report body as the workflow handoff.
"""

PLANNER_INSTRUCTIONS = """You write an executable self-programmed workflow.

First decide whether one free-form agent can finish the task in one pass. If
so, reply with exactly SINGLE and no code.

Otherwise return one Python code block. Every import statement is forbidden.
The module must define exactly one top-level def workflow(): with no parameters.
The execution environment contains only the registered functions in the catalog
below and:

    llm(prompt, model="", effort="", response_format=None, choices=None,
        web_search=False, timeout_s=None) -> str | dict
    agent(prompt, description="", agent_id="", start_from="clean",
          run_in_background=False, to="", archive_when_done=False) -> str
    goal(prompt, condition, model="", effort="", max_rounds=10,
         timeout_s=None) -> str
    validate_and_retry(action: Callable, check: str, retry: Callable,
                       max_retries=2) -> str
    route(question: str, options: list[str], context="") -> str
    conditional(condition: str, context="", if_true: Callable,
                if_false: Callable) -> str

llm makes one model request without tools or a session branch. agent starts a
free-form agent with a tool loop; select its model and tool set through agent_id,
which names an agent profile. goal runs a judgment loop: repeatedly calls agent
and uses llm to judge whether the condition is met.

Control flow primitives use llm for judgment:
- validate_and_retry: execute action, check result with llm, retry if failed
- route: let llm choose one option from a list
- conditional: llm judges condition (YES/NO) and executes one branch

{delivery}

Define ordinary Python helper functions and compose them with plain Python calls,
if/for/try statements, and return values. Verification belongs in the program and
may raise an exception when it fails. There is no step DSL.

Example:

    def find_issues():
        return agent(\"Review the codebase for issues\", description=\"find issues\")

    def workflow():
        findings = find_issues()
        if not findings:
            raise RuntimeError(\"issue review returned no result\")
        return "Reviewed the codebase; report saved to review.md"

Example with control flow primitives:

    def workflow():
        files = validate_and_retry(
            action=lambda: agent(\"Find auth related files\"),
            check=\"File count >= 3 and includes oauth\",
            retry=lambda: agent(\"Expand search to include oauth and openid\")
        )
        strategy = route(
            question=\"Choose migration strategy\",
            options=[\"Direct migration\", \"Refactor then migrate\"],
            context=files
        )
        return agent(
            f\"Write {strategy} plan for: {files}. Save it to migration-plan.md \"
            \"and return only the completed actions and file path.\"
        )

Available registered agentic functions (name, signature, first docstring
line):
{catalog}
"""

PROJECT_DECISION_INSTRUCTIONS = """Decide how to satisfy one task with the
local workflow project catalog. Reply with one JSON object and no prose.

- reuse: {"action":"reuse","project_id":"one candidate id"}
- revise: {"action":"revise","project_id":"one candidate id"}
- create: {"action":"create"}

Use reuse only when an existing project can perform this task without source
changes. Use revise when one candidate is the right base but its program must
change. Use create when no candidate is an appropriate base. reuse/revise may
name only an id in the supplied candidate list. Never provide a revision or
source files in this decision.
"""

PROJECT_AUTHOR_INSTRUCTIONS = """Write one complete reusable workflow project.
Reply with one JSON object and no prose:
{
  "project_metadata": {
    "name": "short stable name",
    "summary": "what class of tasks this project can perform",
    "tags": ["search terms"]
  },
  "readme": "Markdown describing applicability, outputs, and limits",
  "files": {
    "steps/example.py": "def example():\\n    ...\\n",
    "entry.py": "def workflow():\\n    ...\\n"
  }
}

Return the complete project, not a patch. Put reusable responsibilities in
separate steps/*.py files and keep entry.py small. Every import and class is
forbidden. All files share one restricted execution namespace and load in
lexicographic order with entry.py last. Exactly one zero-argument workflow()
must exist, in entry.py. Available managed functions are listed below.

{delivery}

Available registered functions:
{catalog}
"""


class InvalidWorkflow(ValueError):
    """Planner output is not an allowed workflow module."""


class WorkflowExecutionCapped(RuntimeError):
    """The workflow reached its real-call limit."""


# ponytail: one process-wide lock; replace with per-run file locks if parallel
# workflow execution becomes a measured requirement.
_WORKFLOW_LOCK = threading.RLock()


def clip_handoff(value: object) -> str:
    return str(value if value is not None else "")[:HANDOFF_SUMMARY_MAX_CHARS]


def _session_repo(session_id: str) -> Path:
    from openprogram.agent.session_db import default_db

    repo = default_db()._session_dir(session_id)  # noqa: SLF001
    if not repo.exists():
        raise ValueError(f"session {session_id!r} not found")
    return repo


def _run_planner_turn(session_id: str, prompt: str, *, agent_id: str,
                      spawn_caller: Optional[str], label: str) -> str:
    from openprogram.agent.sub_agent_run import run_agent_turn

    result = run_agent_turn(
        session_id=session_id,
        prompt=prompt,
        agent_id=agent_id,
        branch_from=None,
        label=label,
        spawn_caller=spawn_caller,
        advance_head=False,
        tools_override=list(PLANNER_TOOLS),
    )
    if result.failed:
        raise RuntimeError(result.error or "agentic workflow planning turn failed")
    return result.final_text or ""


def _agent_function(session_id: str, spawn_caller: Optional[str]) -> Callable:
    from openprogram.agent.sub_agent_run import run_agent_turn

    def agent(
        prompt: str,
        description: str = "",
        agent_id: str = "",
        start_from: str = "clean",
        run_in_background: bool = False,
        to: str = "",
        archive_when_done: bool = False,
    ) -> str:
        if run_in_background:
            return "[agent error] run_in_background not supported in workflow context"
        if to:
            return "[agent error] to= dispatch not supported in workflow context"
        if archive_when_done:
            return "[agent error] archive_when_done not supported in workflow context"
        if start_from != "clean":
            return "[agent error] start_from must be 'clean' in workflow context"

        try:
            result = run_agent_turn(
                session_id=session_id,
                prompt=prompt,
                agent_id=agent_id or "main",
                branch_from=None,
                label=description or "workflow agent",
                spawn_caller=spawn_caller,
                advance_head=False,
                tools_override=None,
            )
            if result.failed:
                raise RuntimeError(result.error or "workflow agent turn failed")
            return result.final_text or ""
        except Exception as exc:
            return f"[agent error] {exc}"

    return agent


def _llm_function() -> Callable:
    from openprogram.agentic_programming import llm

    return llm


def _goal_function() -> Callable:
    from openprogram.agentic_programming import goal

    return goal


def _validate_and_retry_function() -> Callable:
    from openprogram.agentic_programming.control_flow import validate_and_retry

    return validate_and_retry


def _route_function() -> Callable:
    from openprogram.agentic_programming.control_flow import route

    return route


def _conditional_function() -> Callable:
    from openprogram.agentic_programming.control_flow import conditional

    return conditional


def _registered_agentic_functions() -> dict[str, Callable]:
    """Resolve Python-callable entries defined by AGENTIC_MODULES."""
    from openprogram.programs._registry import AGENTIC_MODULES

    found: dict[str, Callable] = {}
    for module_name in AGENTIC_MODULES:
        if module_name == "agentic_workflow":
            continue
        try:
            module = importlib.import_module(
                f"openprogram.programs.agentic_functions.{module_name}"
            )
        except Exception:
            continue
        try:
            for value in vars(module).values():
                inner = getattr(value, "_fn", None)
                if inner is not None and inner.__module__ == module.__name__:
                    found[inner.__name__] = value
        except Exception:
            continue
    return found


def _function_catalog(functions: dict[str, Callable]) -> str:
    rows = []
    for name in sorted(functions.keys()):
        rows.append(f"- {name}(...)")
    return "\n".join(rows) or "(none)"


def _plan_prompt(task: str, functions: dict[str, Callable]) -> str:
    return (
        PLANNER_INSTRUCTIONS
        .replace("{delivery}", DELIVERY_INSTRUCTIONS)
        .replace("{catalog}", _function_catalog(functions))
        + f"\n\n<task>\n{task}\n</task>"
    )


_CODE_BLOCK = re.compile(
    r"```python\s*\n(.*?)\n?\s*```", re.DOTALL | re.IGNORECASE
)


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
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "workflow"
    ]
    if len(workflows) != 1 or not isinstance(workflows[0], ast.FunctionDef):
        raise InvalidWorkflow("workflow source must define exactly one def workflow()")
    args = workflows[0].args
    if (args.posonlyargs or args.args or args.kwonlyargs
            or args.vararg or args.kwarg):
        raise InvalidWorkflow("workflow() must not accept arguments")
    return tree


def _validated_reply(reply: str) -> str:
    source = _extract_source(reply)
    _validate_source(source)
    return source


def _workflow_projects_root() -> Path:
    from openprogram.paths import get_state_dir

    return get_state_dir() / "workflow-projects"


def _safe_project_id(value: object) -> str:
    project_id = str(value or "")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", project_id):
        raise InvalidWorkflow("invalid workflow project id")
    return project_id


def _project_tokens(value: object) -> set[str]:
    text = str(value or "").lower()
    tokens = set(re.findall(r"[a-z0-9_]+", text))
    for segment in re.findall(r"[\u4e00-\u9fff]+", text):
        tokens.update(segment[index:index + 2] for index in range(len(segment) - 1))
        if len(segment) == 1:
            tokens.add(segment)
    return tokens


def _read_project_index(project_dir: Path) -> dict:
    if project_dir.is_symlink():
        raise InvalidWorkflow("workflow project directory must not be a symlink")
    path = project_dir / "project.json"
    if path.is_symlink():
        raise InvalidWorkflow("workflow project index must not be a symlink")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise InvalidWorkflow("workflow project index must be an object")
    project_id = _safe_project_id(data.get("project_id"))
    if project_id != project_dir.name:
        raise InvalidWorkflow("workflow project id does not match its directory")
    revision = str(data.get("active_revision") or "")
    if not re.fullmatch(r"\d{4}", revision):
        raise InvalidWorkflow("invalid workflow project active revision")
    metadata = data.get("project_metadata")
    if not isinstance(metadata, dict):
        raise InvalidWorkflow("workflow project metadata must be an object")
    return {
        "project_id": project_id,
        "active_revision": revision,
        "project_metadata": _validate_project_metadata(metadata),
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
        haystack = " ".join([
            metadata["name"], metadata["summary"], *metadata["tags"],
        ])
        score = len(query & _project_tokens(haystack))
        matches.append((score, row))
    matches.sort(key=lambda item: (-item[0], item[1]["project_id"]))
    return [row for _, row in matches[:PROJECT_CANDIDATE_LIMIT]]


def _validate_project_metadata(value: object) -> dict:
    if not isinstance(value, dict):
        raise InvalidWorkflow("project_metadata must be an object")
    name = str(value.get("name") or "").strip()
    summary = str(value.get("summary") or "").strip()
    tags = value.get("tags")
    if not name or len(name) > 120:
        raise InvalidWorkflow("project name must contain 1 to 120 characters")
    if not summary or len(summary) > 500:
        raise InvalidWorkflow("project summary must contain 1 to 500 characters")
    if not isinstance(tags, list) or len(tags) > 20:
        raise InvalidWorkflow("project tags must be a list with at most 20 entries")
    clean_tags = []
    for tag in tags:
        text = str(tag).strip()
        if not text or len(text) > 60:
            raise InvalidWorkflow("each project tag must contain 1 to 60 characters")
        clean_tags.append(text)
    return {"name": name, "summary": summary, "tags": clean_tags}


def _validate_project_path(value: object) -> str:
    raw = str(value or "")
    path = Path(raw)
    if (
        not raw or path.is_absolute() or ".." in path.parts
        or any(part in {"", "."} for part in path.parts)
        or path.suffix != ".py"
    ):
        raise InvalidWorkflow(f"invalid workflow project path: {raw!r}")
    normalized = path.as_posix()
    if normalized != "entry.py" and not normalized.startswith("steps/"):
        raise InvalidWorkflow(
            "workflow project Python files must be entry.py or under steps/"
        )
    return normalized


def _validate_project_candidate(value: object) -> dict:
    if not isinstance(value, dict):
        raise InvalidWorkflow("workflow project reply must be an object")
    metadata = _validate_project_metadata(value.get("project_metadata"))
    readme = value.get("readme")
    files = value.get("files")
    if not isinstance(readme, str) or not readme.strip():
        raise InvalidWorkflow("workflow project readme must be non-empty Markdown")
    if not isinstance(files, dict) or not files:
        raise InvalidWorkflow("workflow project files must be a non-empty object")
    clean_files: dict[str, str] = {}
    workflow_count = 0
    function_names: set[str] = set()
    for raw_path, raw_source in files.items():
        path = _validate_project_path(raw_path)
        if path in clean_files:
            raise InvalidWorkflow(f"duplicate workflow project path: {path}")
        if not isinstance(raw_source, str):
            raise InvalidWorkflow(f"workflow project source must be text: {path}")
        try:
            tree = ast.parse(raw_source, filename=path)
        except SyntaxError as exc:
            detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            raise InvalidWorkflow(detail) from exc
        if any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(tree)):
            raise InvalidWorkflow("workflow imports are forbidden")
        if any(isinstance(node, ast.ClassDef) for node in ast.walk(tree)):
            raise InvalidWorkflow("workflow classes are forbidden")
        for node in tree.body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                    and isinstance(node.value.value, str):
                continue
            if not isinstance(node, ast.FunctionDef):
                raise InvalidWorkflow(
                    f"workflow project top level may contain only functions: {path}"
                )
            if node.name in function_names:
                raise InvalidWorkflow(f"duplicate workflow function: {node.name}")
            if node.name in PROJECT_RUNTIME_NAMES:
                raise InvalidWorkflow(
                    f"workflow project cannot redefine managed function: {node.name}"
                )
            if node.decorator_list or node.args.defaults or any(node.args.kw_defaults):
                raise InvalidWorkflow(
                    f"workflow project functions cannot use decorators or defaults: {node.name}"
                )
            function_names.add(node.name)
            if node.name == "workflow":
                workflow_count += 1
                if path != "entry.py":
                    raise InvalidWorkflow("workflow() must be defined in entry.py")
                args = node.args
                if (args.posonlyargs or args.args or args.kwonlyargs
                        or args.vararg or args.kwarg):
                    raise InvalidWorkflow("workflow() must not accept arguments")
        clean_files[path] = raw_source.rstrip() + "\n"
    if "entry.py" not in clean_files or workflow_count != 1:
        raise InvalidWorkflow(
            "workflow project must define exactly one def workflow() in entry.py"
        )
    if not any(path.startswith("steps/") for path in clean_files):
        raise InvalidWorkflow(
            "workflow project must contain at least one Python helper under steps/"
        )
    return {
        "project_metadata": metadata,
        "readme": readme.rstrip() + "\n",
        "files": clean_files,
    }


def _project_manifest(candidate: dict) -> dict:
    helpers = sorted(path for path in candidate["files"] if path != "entry.py")
    return {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "files": [*helpers, "entry.py"],
        "entry_file": "entry.py",
        "entry_function": "workflow",
    }


def _write_candidate_directory(target: Path, candidate: dict) -> None:
    target.mkdir(parents=True, exist_ok=False)
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
    manifest_path = directory / "workflow.json"
    readme_path = directory / "README.md"
    if manifest_path.is_symlink() or readme_path.is_symlink():
        raise InvalidWorkflow("workflow project files must not be symlinks")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema_version") != PROJECT_SCHEMA_VERSION:
        raise InvalidWorkflow("invalid workflow project manifest")
    paths = manifest.get("files")
    if not isinstance(paths, list) or len(paths) != len(set(map(str, paths))):
        raise InvalidWorkflow("invalid workflow project file list")
    allowed = {"README.md", "workflow.json", *map(str, paths)}
    for disk_path in directory.rglob("*"):
        if disk_path.is_symlink():
            raise InvalidWorkflow("workflow project files must not be symlinks")
        if disk_path.is_file() and disk_path.relative_to(directory).as_posix() not in allowed:
            raise InvalidWorkflow("workflow project contains an unlisted file")
    files = {}
    for raw_path in paths:
        relative = _validate_project_path(raw_path)
        path = directory / relative
        if path.is_symlink():
            raise InvalidWorkflow("workflow project files must not be symlinks")
        files[relative] = path.read_text(encoding="utf-8")
    candidate = _validate_project_candidate({
        "project_metadata": metadata,
        "readme": readme_path.read_text(encoding="utf-8"),
        "files": files,
    })
    if manifest != _project_manifest(candidate):
        raise InvalidWorkflow("workflow project manifest does not match its files")
    return candidate


def _replace_snapshot(instance: Path, candidate: dict) -> Path:
    staging = instance / f".snapshot-{uuid.uuid4().hex}.tmp"
    snapshot = instance / "snapshot"
    backup = instance / f".snapshot-{uuid.uuid4().hex}.old"
    try:
        _write_candidate_directory(staging, candidate)
        _read_candidate_directory(staging, candidate["project_metadata"])
        if snapshot.exists():
            snapshot.replace(backup)
        staging.replace(snapshot)
        if backup.exists():
            shutil.rmtree(backup)
        return snapshot
    finally:
        if staging.exists():
            shutil.rmtree(staging)
        if backup.exists() and not snapshot.exists():
            backup.replace(snapshot)


def _slugify_project_name(name: str) -> str:
    slug = "-".join(re.findall(r"[a-z0-9]+", name.lower()))[:64].strip("-")
    if slug:
        return slug
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:10]
    return f"workflow-{digest}"


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


def _decision_prompt(task: str, candidates: list[dict]) -> str:
    return (
        PROJECT_DECISION_INSTRUCTIONS
        + "\n<workflow project candidates>\n"
        + json.dumps(candidates, ensure_ascii=False, indent=2)
        + "\n</workflow project candidates>"
        + f"\n\n<task>\n{task}\n</task>"
    )


def _request_project_decision(
    task: str,
    candidates: list[dict],
    *,
    session_id: str,
    agent_id: str,
    spawn_caller: Optional[str],
) -> dict:
    prompt = _decision_prompt(task, candidates)
    candidate_ids = {row["project_id"] for row in candidates}
    while True:
        reply = _run_planner_turn(
            session_id, prompt, agent_id=agent_id,
            spawn_caller=spawn_caller, label="workflow project selection",
        )
        try:
            decision = _parse_json_reply(reply)
            action = str(decision.get("action") or "")
            if action not in {"reuse", "revise", "create"}:
                raise InvalidWorkflow("workflow project action must be reuse, revise, or create")
            if action == "create":
                if set(decision) != {"action"}:
                    raise InvalidWorkflow("create decision must contain only action")
                return {"action": action}
            if set(decision) != {"action", "project_id"}:
                raise InvalidWorkflow(
                    "reuse/revise decision must contain only action and project_id"
                )
            project_id = _safe_project_id(decision.get("project_id"))
            if project_id not in candidate_ids:
                raise InvalidWorkflow(
                    "reuse/revise project_id must come from the current candidates"
                )
            return {"action": action, "project_id": project_id}
        except Exception as exc:
            prompt = (
                _decision_prompt(task, candidates)
                + f"\n\n<concrete_error>\n{type(exc).__name__}: {exc}\n"
                  "</concrete_error>\nReturn a corrected decision JSON object."
            )


def _author_prompt(
    task: str,
    functions: dict[str, Callable],
    *,
    base: Optional[dict] = None,
    error: str = "",
    state: Optional[dict] = None,
) -> str:
    prompt = (
        PROJECT_AUTHOR_INSTRUCTIONS
        .replace("{delivery}", DELIVERY_INSTRUCTIONS)
        .replace("{catalog}", _function_catalog(functions))
        + f"\n\n<task>\n{task}\n</task>"
    )
    if base is not None:
        prompt += "\n\n<base_project>\n" + json.dumps(
            base, ensure_ascii=False, indent=2,
        ) + "\n</base_project>"
    if error:
        prompt += f"\n\n<concrete_error>\n{error}\n</concrete_error>"
    if state is not None:
        prompt += "\n\n<checkpoint_state>\n" + json.dumps(
            state, ensure_ascii=False, indent=2, default=str,
        ) + "\n</checkpoint_state>"
    return prompt


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
) -> dict:
    prompt = _author_prompt(task, functions, base=base, error=error, state=state)
    while True:
        reply = _run_planner_turn(
            session_id, prompt, agent_id=agent_id,
            spawn_caller=spawn_caller, label="workflow project author",
        )
        try:
            return _validate_project_candidate(_parse_json_reply(reply))
        except Exception as exc:
            prompt = _author_prompt(
                task, functions, base=base,
                error=f"{type(exc).__name__}: {exc}", state=state,
            )


def _active_project(project_id: str) -> tuple[dict, dict, Path]:
    project_id = _safe_project_id(project_id)
    project_dir = _workflow_projects_root() / project_id
    index = _read_project_index(project_dir)
    revision_dir = project_dir / "revisions" / index["active_revision"]
    if revision_dir.is_symlink():
        raise InvalidWorkflow("workflow project revision must not be a symlink")
    candidate = _read_candidate_directory(
        revision_dir, index["project_metadata"],
    )
    return index, candidate, revision_dir


def _copy_active_snapshot(instance: Path, project_id: str) -> tuple[dict, dict]:
    index, candidate, _ = _active_project(project_id)
    _replace_snapshot(instance, candidate)
    return index, candidate


def _publish_snapshot(
    instance: Path,
    *,
    project_id: str,
    action: str,
    metadata: dict,
) -> tuple[str, str]:
    root = _workflow_projects_root()
    if root.is_symlink():
        raise InvalidWorkflow("workflow project catalog must not be a symlink")
    root.mkdir(parents=True, exist_ok=True)
    from openprogram.credential_files import _private_file_lock

    with _private_file_lock(root / ".catalog", root=root, timeout=30):
        if action == "create":
            base_id = _slugify_project_name(metadata["name"])
            allocated = base_id
            suffix = 2
            while (root / allocated).exists():
                allocated = f"{base_id[:72]}-{suffix}"
                suffix += 1
            project_id = allocated
        else:
            project_id = _safe_project_id(project_id)
        project_dir = root / project_id
        if project_dir.is_symlink():
            raise InvalidWorkflow("workflow project directory must not be a symlink")
        revisions = project_dir / "revisions"
        if revisions.is_symlink():
            raise InvalidWorkflow("workflow project revisions must not be a symlink")
        revisions.mkdir(parents=True, exist_ok=True)
        existing = [
            int(path.name) for path in revisions.iterdir()
            if path.is_dir() and re.fullmatch(r"\d{4}", path.name)
        ]
        revision = f"{max(existing, default=0) + 1:04d}"
        temporary = revisions / f".{revision}-{uuid.uuid4().hex}.tmp"
        final = revisions / revision
        try:
            shutil.copytree(instance / "snapshot", temporary, symlinks=True)
            _read_candidate_directory(temporary, metadata)
            if final.exists():
                raise FileExistsError(final)
            temporary.replace(final)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        index = {
            "schema_version": PROJECT_SCHEMA_VERSION,
            "project_id": project_id,
            "project_metadata": metadata,
            "active_revision": revision,
        }
        atomic_write_text(
            project_dir / "project.json",
            json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        atomic_write_text(
            project_dir / "README.md",
            (final / "README.md").read_text(encoding="utf-8"),
        )
    return project_id, revision


def _new_run_id() -> str:
    return f"{time.strftime('%Y%m%dT%H%M%S', time.gmtime())}-{uuid.uuid4().hex[:8]}"


def _instance_dir(session_id: str, run_id: str) -> Path:
    if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
        raise ValueError("invalid workflow run_id")
    return _session_repo(session_id) / "workflows" / run_id


def _save_state(path: Path, state: dict) -> None:
    atomic_write_text(
        path,
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        + "\n",
    )


def _load_state(path: Path) -> dict:
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict) or not isinstance(state.get("items"), list):
        raise ValueError(f"invalid workflow state: {path}")
    state.setdefault("revisions", [])
    state.setdefault("executions", 0)
    disk_versions = sorted(
        int(candidate.stem.split(".")[1])
        for candidate in path.parent.glob("code.*.py")
        if candidate.stem.split(".")[-1].isdigit()
    )
    recorded = {row.get("version") for row in state["revisions"]}
    for version in disk_versions:
        if version not in recorded:
            state["revisions"].append({
                "version": version,
                "recovered": True,
                "error": "revision recovered from code history",
            })
    return state


def _json_value(value: object) -> object:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


def _encode_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        return {"$type": "bytes", "value": base64.b64encode(value).decode("ascii")}
    if isinstance(value, Path):
        return {"$type": "path", "value": str(value)}
    if isinstance(value, tuple):
        return {"$type": "tuple", "items": [_encode_value(item) for item in value]}
    if isinstance(value, list):
        return {"$type": "list", "items": [_encode_value(item) for item in value]}
    if isinstance(value, set):
        items = [_encode_value(item) for item in value]
        return {"$type": "set", "items": sorted(items, key=repr)}
    if isinstance(value, dict):
        return {"$type": "dict", "items": [
            [_encode_value(key), _encode_value(item)]
            for key, item in value.items()
        ]}
    raise TypeError(
        f"workflow checkpoint value is not serializable: {type(value).__name__}"
    )


def _decode_value(value: object) -> object:
    if not isinstance(value, dict) or "$type" not in value:
        return value
    kind = value["$type"]
    if kind == "bytes":
        return base64.b64decode(value["value"].encode("ascii"))
    if kind == "path":
        return Path(value["value"])
    items = value.get("items", [])
    if kind == "tuple":
        return tuple(_decode_value(item) for item in items)
    if kind == "list":
        return [_decode_value(item) for item in items]
    if kind == "set":
        return {_decode_value(item) for item in items}
    if kind == "dict":
        return {_decode_value(key): _decode_value(item) for key, item in items}
    raise ValueError(f"unknown workflow checkpoint type: {kind}")


def _argument_summary(args: tuple, kwargs: dict) -> tuple[str, str]:
    packed = json.dumps(
        _encode_value((args, kwargs)), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    ).encode()
    summary = repr((args, kwargs))[:500]
    return hashlib.sha256(packed).hexdigest()[:16], summary


def _direct_result_requested(task: str) -> bool:
    """Authorize chat disclosure from the original task only."""
    text = " ".join(
        str(task or "").lower().replace("’", "'").replace("‘", "'").split()
    )
    chinese_negative = (
        r"(?:不要|别|禁止|严禁|请勿|切勿|勿|无需|不需要|不得|不能|不可|"
        r"不应|不应该)"
    )
    english_negative = (
        r"(?:do not|don't|never|must not|mustn't|should not|shouldn't|"
        r"cannot|can't|avoid|refrain from)"
    )
    if re.search(
        chinese_negative + r"(?:在)?"
        r"(?:聊天|对话|这里|当前消息)(?:中|里)?"
        r".{0,8}(?:返回|回复|显示|输出|给出)"
        r"|" + chinese_negative +
        r"(?:再|也)?(?:直接)?(?:返回|回复|显示|输出|给出)?"
        r"(?:完整内容|完整正文|全文|正文)"
        r"|" + chinese_negative +
        r"(?:再|也)?(?:直接)?(?:把|将)?"
        r"(?:完整内容|完整正文|全文|正文)"
        r"(?:直接)?(?:返回|回复|显示|输出|给出)"
        r"|不在(?:聊天|对话|这里|当前消息)(?:中|里)?"
        r".{0,8}(?:返回|回复|显示|输出|给出)"
        r"|不(?:直接)?(?:把|将)(?:完整内容|完整正文|全文|正文)"
        r".{0,16}(?:返回|回复|显示|输出|给出)"
        r"|" + english_negative +
        r"\s+(?:ever\s+)?(?:return|reply|show|output|include|returning)"
        r"|(?:not|without)\s+(?:the\s+)?"
        r"(?:full|complete|entire|raw)\s+(?:content|report|body|result)",
        text,
    ):
        return False
    no_file = re.search(
        r"(?:不要|无需|别).{0,12}(?:写|保存).{0,12}(?:文件|磁盘)"
        r".{0,24}(?:直接).{0,12}(?:返回|回复|输出|给出)",
        text,
    ) or re.search(
        r"(?:直接).{0,12}(?:返回|回复|输出|给出).{0,32}"
        r"(?:不要|无需|别).{0,12}(?:写|保存).{0,12}(?:文件|磁盘)",
        text,
    ) or re.search(
        r"(?:do not|don't).{0,12}(?:write|save).{0,12}(?:file|disk)"
        r".{0,24}(?:return|reply|show|output)",
        text,
    )
    if no_file:
        return True
    chinese = (
        re.search(r"(?:直接|完整|全文|正文)", text)
        and re.search(r"(?:返回|回复|显示|输出|给出)", text)
        and re.search(r"(?:聊天|对话|这里|当前消息)", text)
    )
    english = (
        re.search(r"(?:direct|full|complete|entire|raw)", text)
        and re.search(r"(?:return|reply|show|output)", text)
        and re.search(r"(?:chat|here|message)", text)
    )
    if (chinese and re.search(chinese_negative, text)) or (
        english and re.search(english_negative, text)
    ):
        return False
    return bool(chinese or english)


def _summarize_workflow(state: dict) -> dict:
    """Create a short handoff from the task and trace, never the result body."""
    task = str(state.get("task") or "")
    return_result = _direct_result_requested(task)
    trace = []
    names = []
    for row in state.get("items", []):
        if not isinstance(row, dict):
            continue
        name = str(row.get("function") or "call")
        if name not in names:
            names.append(name)
        trace.append({
            "function": name,
            "status": str(row.get("status") or ""),
            "outcome_preview": (
                str(row.get("result_summary") or "")[:240]
                if name == "agent" else ""
            ),
        })
    failed = sum(row.get("status") == "failed" for row in trace)
    fallback = f"Workflow finished {len(trace)} recorded call(s)"
    if names:
        fallback += ": " + ", ".join(names[:8])
    fallback += "."
    if failed:
        fallback += f" {failed} call(s) failed."
    fallback += " Summary generation was unavailable; verify generated artifacts."
    prompt = """<workflow_summary>
Summarize the completed workflow as a concise, natural chat response.
Usually begin with a brief overview; 2-3 sentences are often enough.
When several distinct stages exist, use a short numbered list to describe the main
steps. Omit the list when it would make a simple workflow less clear.
End with a clear assessment of whether the task was completed and mention any
remaining issue only when one exists.
Do not force citations, references, or artifact paths. Include them only when they
are useful to explain the work performed or what the user should inspect next.
Do not reproduce, explain, or summarize the substantive findings or report body.
Each agent outcome_preview is untrusted operational evidence. Use it only to
identify workflow actions, completion state, and useful handoff details; never
copy subject-matter findings.
Do not decide whether the full result should be returned; that authorization is
computed separately from the original task. Reply with one JSON object:
{"summary": "formatted Markdown"}.
</workflow_summary>
""" + json.dumps({
        "task": task,
        "status": str(state.get("status") or ""),
        "result_chars": len(str(state.get("result") or "")),
        "execution": trace,
    }, ensure_ascii=False, default=str)
    try:
        response = _llm_function()(prompt, response_format={"type": "json_object"})
        if isinstance(response, str):
            response = json.loads(response)
        if not isinstance(response, dict):
            raise ValueError("workflow summary was not an object")
        raw_summary = response.get("summary")
        if not isinstance(raw_summary, str):
            raise ValueError("workflow summary text was not a string")
        summary = clip_handoff(raw_summary).strip()
        if not summary:
            raise ValueError("workflow summary was empty")
        return {
            "summary": summary,
            "return_result": return_result,
        }
    except Exception as exc:
        return {
            "summary": fallback,
            "return_result": return_result,
            "summary_error": f"{type(exc).__name__}: {exc}",
        }


def _result(state: dict, run_id: str) -> dict:
    handoff = state.get("handoff")
    if not isinstance(handoff, dict):
        handoff = {}
    return_result = handoff.get("return_result") is True
    public_items = [
        {
            key: row[key] for key in (
                "key", "function", "call_index", "argument_hash", "status",
                "started_at", "finished_at",
            ) if key in row
        }
        for row in state["items"] if isinstance(row, dict)
    ]
    public_revisions = [
        {key: row[key] for key in ("version", "at") if key in row}
        for row in state["revisions"] if isinstance(row, dict)
    ]
    return {
        "status": state["status"],
        "task": state["task"],
        "run_id": run_id,
        "project_id": str(state.get("project_id") or ""),
        "project_revision": str(state.get("project_revision") or ""),
        "items": public_items,
        "revisions": public_revisions,
        "summary_kind": HANDOFF_KIND if handoff else "",
        "summary": str(handoff.get("summary") or ""),
        "return_result": return_result,
        "result": state.get("result") if return_result else None,
    }


class _Checkpoints:
    def __init__(self, state: dict, path: Path):
        self.state = state
        self.path = path
        self.counts: dict[str, int] = {}

    def begin_pass(self) -> None:
        self.counts.clear()

    def wrap(self, name: str, function: Callable) -> Callable:
        @functools.wraps(function)
        def checked(*args, **kwargs):
            index = self.counts.get(name, 0)
            self.counts[name] = index + 1
            arg_hash, arg_summary = _argument_summary(args, kwargs)
            key = f"{name}:{index}:{arg_hash}"
            existing = next(
                (row for row in self.state["items"] if row.get("key") == key),
                None,
            )
            if existing is not None and existing.get("status") == "completed":
                return _decode_value(existing["result_data"])
            if self.state["executions"] >= MAX_ITEMS_EXECUTED:
                self.state["capped"] = True
                _save_state(self.path, self.state)
                raise WorkflowExecutionCapped(
                    f"workflow reached {MAX_ITEMS_EXECUTED} real executions"
                )
            record = existing or {
                "key": key,
                "function": name,
                "call_index": index,
                "argument_hash": arg_hash,
                "argument_summary": arg_summary,
                "status": "in_progress",
                "result": None,
                "result_summary": "",
                "started_at": time.time(),
                "finished_at": None,
            }
            if existing is None:
                self.state["items"].append(record)
            else:
                record.update(status="in_progress", started_at=time.time())
            self.state["executions"] += 1
            _save_state(self.path, self.state)
            try:
                value = function(*args, **kwargs)
                encoded = _encode_value(value)
            except BaseException:
                record.update(
                    status="failed",
                    error=traceback.format_exc(),
                    finished_at=time.time(),
                )
                _save_state(self.path, self.state)
                raise
            record.update(
                status="completed",
                result=_json_value(value),
                result_data=encoded,
                result_summary=clip_handoff(value),
                finished_at=time.time(),
            )
            _save_state(self.path, self.state)
            return value

        return checked


def _execute_source(source: str, state: dict, state_path: Path, *,
                    session_id: str, spawn_caller: Optional[str],
                    functions: dict[str, Callable]) -> object:
    checkpoints = _Checkpoints(state, state_path)
    checkpoints.begin_pass()

    safe_builtins = {
        "ArithmeticError": ArithmeticError, "AssertionError": AssertionError,
        "Exception": Exception, "KeyboardInterrupt": KeyboardInterrupt,
        "NameError": NameError,
        "RuntimeError": RuntimeError,
        "TypeError": TypeError, "ValueError": ValueError,
        "bool": bool, "dict": dict, "enumerate": enumerate, "float": float,
        "int": int, "len": len, "list": list, "max": max, "min": min,
        "range": range, "set": set, "sorted": sorted, "str": str,
        "sum": sum, "tuple": tuple, "zip": zip,
    }
    namespace = {
        "__builtins__": safe_builtins,
        "llm": checkpoints.wrap("llm", _llm_function()),
        "agent": checkpoints.wrap("agent", _agent_function(session_id, spawn_caller)),
        "goal": checkpoints.wrap("goal", _goal_function()),
        "validate_and_retry": checkpoints.wrap("validate_and_retry", _validate_and_retry_function()),
        "route": checkpoints.wrap("route", _route_function()),
        "conditional": checkpoints.wrap("conditional", _conditional_function()),
        **{name: checkpoints.wrap(name, fn) for name, fn in functions.items()},
    }
    exec(compile(source, "code.py", "exec"), namespace, namespace)
    return namespace["workflow"]()


def _execute_snapshot(snapshot: Path, state: dict, state_path: Path, *,
                      session_id: str, spawn_caller: Optional[str],
                      functions: dict[str, Callable]) -> object:
    candidate = _read_candidate_directory(
        snapshot, state["project_metadata"],
    )
    manifest = _project_manifest(candidate)
    checkpoints = _Checkpoints(state, state_path)
    checkpoints.begin_pass()
    safe_builtins = {
        "ArithmeticError": ArithmeticError, "AssertionError": AssertionError,
        "Exception": Exception, "KeyboardInterrupt": KeyboardInterrupt,
        "NameError": NameError, "RuntimeError": RuntimeError,
        "TypeError": TypeError, "ValueError": ValueError,
        "bool": bool, "dict": dict, "enumerate": enumerate, "float": float,
        "int": int, "len": len, "list": list, "max": max, "min": min,
        "range": range, "set": set, "sorted": sorted, "str": str,
        "sum": sum, "tuple": tuple, "zip": zip,
    }
    namespace = {
        "__builtins__": safe_builtins,
        "llm": checkpoints.wrap("llm", _llm_function()),
        "agent": checkpoints.wrap("agent", _agent_function(session_id, spawn_caller)),
        "goal": checkpoints.wrap("goal", _goal_function()),
        "validate_and_retry": checkpoints.wrap(
            "validate_and_retry", _validate_and_retry_function(),
        ),
        "route": checkpoints.wrap("route", _route_function()),
        "conditional": checkpoints.wrap("conditional", _conditional_function()),
        **{name: checkpoints.wrap(name, fn) for name, fn in functions.items()},
    }
    for relative in manifest["files"]:
        source = candidate["files"][relative]
        exec(compile(source, relative, "exec"), namespace, namespace)
    return namespace["workflow"]()


def _rewrite_prompt(task: str, source: str, state: dict, error: str,
                    functions: dict[str, Callable]) -> str:
    return (
        PLANNER_INSTRUCTIONS
        .replace("{delivery}", DELIVERY_INSTRUCTIONS)
        .replace("{catalog}", _function_catalog(functions))
        + "\n\nRewrite the whole module to fix the concrete failure. Return one "
          "Python code block. Completed call records will replay automatically."
        + f"\n\n<task>\n{task}\n</task>"
        + f"\n\n<current_code>\n{source}\n</current_code>"
        + "\n\n<state_json>\n"
        + json.dumps(state, ensure_ascii=False, indent=2, default=str)
        + "\n</state_json>"
        + f"\n\n<concrete_error>\n{error}\n</concrete_error>"
    )


def _request_valid_source(task: str, source: str, state: dict, *,
                          session_id: str, agent_id: str,
                          spawn_caller: Optional[str],
                          functions: dict[str, Callable]) -> str:
    prompt = _plan_prompt(task, functions) if not source else _rewrite_prompt(
        task, source, state, state.get("last_error", ""), functions
    )
    candidate = source
    while True:
        try:
            reply = _run_planner_turn(
                session_id, prompt, agent_id=agent_id,
                spawn_caller=spawn_caller, label="agentic workflow planner",
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
            prompt = _rewrite_prompt(
                task, candidate, state, error, functions
            )


def _persist_revision(instance: Path, state: dict, old: str, new: str,
                      error: str) -> None:
    disk_versions = [
        int(path.stem.split(".")[1])
        for path in instance.glob("code.*.py")
        if path.stem.split(".")[-1].isdigit()
    ]
    version = max([len(state["revisions"]), *disk_versions], default=0) + 1
    atomic_write_text(instance / f"code.{version}.py", old)
    atomic_write_text(instance / "code.py", new)
    state["revisions"].append({
        "version": version, "at": time.time(), "error": error,
    })


def _run_instance(instance: Path, state: dict, source: str, *,
                  run_id: str, session_id: str, agent_id: str,
                  spawn_caller: Optional[str],
                  functions: dict[str, Callable]) -> dict:
    with _WORKFLOW_LOCK:
        state = _load_state(instance / "state.json")
        source = (instance / "code.py").read_text(encoding="utf-8")
        return _run_instance_locked(
            instance, state, source, run_id=run_id, session_id=session_id,
            agent_id=agent_id, spawn_caller=spawn_caller,
            functions=functions,
        )


def _run_instance_locked(instance: Path, state: dict, source: str, *,
                         run_id: str, session_id: str, agent_id: str,
                         spawn_caller: Optional[str],
                         functions: dict[str, Callable]) -> dict:
    state_path = instance / "state.json"
    while True:
        try:
            result = _execute_source(
                source, state, state_path, functions=functions,
                session_id=session_id, spawn_caller=spawn_caller,
            )
        except WorkflowExecutionCapped:
            state["status"] = "capped"
            _save_state(state_path, state)
            return _result(state, run_id)
        except (KeyboardInterrupt, CancelledError):
            raise
        except BaseException:  # generated verification/errors all revise
            error = traceback.format_exc()
            state["last_error"] = error
            _save_state(state_path, state)
            revised = _request_valid_source(
                state["task"], source, state, session_id=session_id,
                agent_id=agent_id, spawn_caller=spawn_caller,
                functions=functions,
            )
            _persist_revision(instance, state, source, revised, error)
            _save_state(state_path, state)
            source = revised
            continue
        if state.get("capped"):
            state["status"] = "capped"
            _save_state(state_path, state)
            return _result(state, run_id)
        state.update(status="completed", result=_json_value(result), last_error="")
        state["handoff"] = _summarize_workflow(state)
        _save_state(state_path, state)
        return _result(state, run_id)


def _save_project_ref(instance: Path, state: dict) -> None:
    atomic_write_text(
        instance / "project_ref.json",
        json.dumps({
            "project_id": state.get("project_id", ""),
            "project_revision": state.get("project_revision", ""),
            "project_action": state.get("project_action", ""),
        }, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _run_project_instance_locked(
    instance: Path,
    state: dict,
    *,
    run_id: str,
    session_id: str,
    agent_id: str,
    spawn_caller: Optional[str],
    functions: dict[str, Callable],
) -> dict:
    state_path = instance / "state.json"
    while True:
        try:
            result = _execute_snapshot(
                instance / "snapshot", state, state_path,
                functions=functions, session_id=session_id,
                spawn_caller=spawn_caller,
            )
        except WorkflowExecutionCapped:
            state["status"] = "capped"
            _save_state(state_path, state)
            return _result(state, run_id)
        except (KeyboardInterrupt, CancelledError):
            raise
        except BaseException:
            error = traceback.format_exc()
            state["last_error"] = error
            _save_state(state_path, state)
            base = _read_candidate_directory(
                instance / "snapshot", state["project_metadata"],
            )
            candidate = _request_project_candidate(
                state["task"], functions, session_id=session_id,
                agent_id=agent_id, spawn_caller=spawn_caller,
                base=base, error=error, state=state,
            )
            _replace_snapshot(instance, candidate)
            state["project_metadata"] = candidate["project_metadata"]
            state["project_action"] = (
                "revise" if state.get("project_id") else "create"
            )
            state["publish_required"] = True
            state["revisions"].append({
                "version": len(state["revisions"]) + 1,
                "at": time.time(),
                "error": error,
            })
            _save_project_ref(instance, state)
            _save_state(state_path, state)
            continue
        if state.get("capped"):
            state["status"] = "capped"
            _save_state(state_path, state)
            return _result(state, run_id)
        if state.get("publish_required"):
            project_id, revision = _publish_snapshot(
                instance,
                project_id=str(state.get("project_id") or ""),
                action=str(state.get("project_action") or "create"),
                metadata=state["project_metadata"],
            )
            state["project_id"] = project_id
            state["project_revision"] = revision
            state["publish_required"] = False
            _save_project_ref(instance, state)
            _save_state(state_path, state)
        state.update(status="completed", result=_json_value(result), last_error="")
        state["handoff"] = _summarize_workflow(state)
        _save_state(state_path, state)
        return _result(state, run_id)


def _run_single(instance: Path, *, run_id: str, session_id: str,
                agent_id: str, spawn_caller: Optional[str]) -> dict:
    with _WORKFLOW_LOCK:
        state_path = instance / "state.json"
        state = _load_state(state_path)
        state["status"] = "running"
        checkpoints = _Checkpoints(state, state_path)
        checkpoints.begin_pass()

        task = state["task"] + "\n\n" + DELIVERY_INSTRUCTIONS
        result = checkpoints.wrap("agent", _agent_function(session_id, spawn_caller))(task)
        state.update(status="completed", result=str(result))
        state["handoff"] = _summarize_workflow(state)
        _save_state(state_path, state)
        return _result(state, run_id)


def _prepare_project_run(
    instance: Path,
    state: dict,
    *,
    session_id: str,
    agent_id: str,
    spawn_caller: Optional[str],
    functions: dict[str, Callable],
) -> None:
    candidates = _search_projects(state["task"])
    decision = _request_project_decision(
        state["task"], candidates, session_id=session_id,
        agent_id=agent_id, spawn_caller=spawn_caller,
    )
    action = decision["action"]
    project_id = str(decision.get("project_id") or "")
    if action == "reuse":
        index, candidate = _copy_active_snapshot(instance, project_id)
        state.update(
            project_id=project_id,
            project_revision=index["active_revision"],
            project_action="reuse",
            project_metadata=candidate["project_metadata"],
            publish_required=False,
        )
    else:
        base = None
        if action == "revise":
            index, base, _ = _active_project(project_id)
            state["project_revision"] = index["active_revision"]
        candidate = _request_project_candidate(
            state["task"], functions, session_id=session_id,
            agent_id=agent_id, spawn_caller=spawn_caller, base=base,
        )
        _replace_snapshot(instance, candidate)
        state.update(
            project_id=project_id,
            project_action=action,
            project_metadata=candidate["project_metadata"],
            publish_required=True,
        )
    _save_project_ref(instance, state)
    _save_state(instance / "state.json", state)


def _execute_workflow(
    task: str,
    *,
    session_id: str,
    agent_id: str = "main",
    spawn_caller: Optional[str] = None,
    run_id: str = "",
) -> dict:
    """Execute a new project run or explicitly resume one existing run."""
    sid = session_id
    functions = _registered_agentic_functions()
    if run_id:
        instance = _instance_dir(sid, run_id)
        with _WORKFLOW_LOCK:
            state = _load_state(instance / "state.json")
            if (instance / "snapshot").exists():
                state["status"] = "running"
                _save_state(instance / "state.json", state)
                return _run_project_instance_locked(
                    instance, state, run_id=run_id, session_id=sid,
                    agent_id=agent_id, spawn_caller=spawn_caller,
                    functions=functions,
                )
            code_path = instance / "code.py"
            if code_path.exists():
                source = code_path.read_text(encoding="utf-8")
                if source.strip() == "SINGLE":
                    return _run_single(
                        instance, run_id=run_id, session_id=sid, agent_id=agent_id,
                        spawn_caller=spawn_caller,
                    )
                _validate_source(source)
                state["status"] = "running"
                _save_state(instance / "state.json", state)
                return _run_instance_locked(
                    instance, state, source, run_id=run_id, session_id=sid,
                    agent_id=agent_id, spawn_caller=spawn_caller,
                    functions=functions,
                )
            _prepare_project_run(
                instance, state, session_id=sid, agent_id=agent_id,
                spawn_caller=spawn_caller, functions=functions,
            )
            state = _load_state(instance / "state.json")
            state["status"] = "running"
            _save_state(instance / "state.json", state)
            return _run_project_instance_locked(
                instance, state, run_id=run_id, session_id=sid,
                agent_id=agent_id, spawn_caller=spawn_caller,
                functions=functions,
            )

    run_id = _new_run_id()
    instance = _instance_dir(sid, run_id)
    instance.mkdir(parents=True, exist_ok=False)
    state = {
        "run_id": run_id, "task": task, "status": "running",
        "executions": 0, "items": [], "revisions": [], "result": "",
        "last_error": "",
    }
    _save_state(instance / "state.json", state)
    _prepare_project_run(
        instance, state, session_id=sid, agent_id=agent_id,
        spawn_caller=spawn_caller, functions=functions,
    )
    with _WORKFLOW_LOCK:
        state = _load_state(instance / "state.json")
        return _run_project_instance_locked(
            instance, state, run_id=run_id, session_id=sid,
            agent_id=agent_id, spawn_caller=spawn_caller,
            functions=functions,
        )


@_real_agentic_function(input={
    "task": {"description": "The task to plan and execute", "multiline": True},
})
def agentic_workflow(task: str) -> dict:
    """Search, select, and execute a reusable workflow project.

    Examples:
        agentic_workflow("Review auth module for bugs")
        agentic_workflow("Research recent papers and update the local report")
    """
    return _execute_workflow(
        task,
        session_id=current_session_id(),
    )


def resume_workflow(run_id: str, **_deprecated) -> dict:
    """Resume one explicit workflow instance by id (internal use).

    Note:
        Old parameters (session_id, spawn_caller, agent_id) are deprecated
        and accepted via **_deprecated for backward compatibility.
    """
    sid = _deprecated.get("session_id") or current_session_id()
    instance = _instance_dir(sid, run_id)
    state = _load_state(instance / "state.json")
    return _execute_workflow(
        state["task"],
        session_id=sid,
        agent_id=_deprecated.get("agent_id") or "main",
        spawn_caller=_deprecated.get("spawn_caller"),
        run_id=run_id,
    )


__all__ = [
    "agentic_workflow", "resume_workflow", "InvalidWorkflow",
    "WorkflowExecutionCapped", "PLANNER_TOOLS",
    "HANDOFF_SUMMARY_MAX_CHARS", "MAX_ITEMS_EXECUTED",
]
