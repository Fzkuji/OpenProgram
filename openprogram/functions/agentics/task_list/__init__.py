"""Execute planner-generated Agentic Programming workflows.

The planner returns ``SINGLE`` for one-agent work, otherwise a Python module
with ``def workflow():``.  Each invocation owns
``<session-repo>/workflows/<run_id>/`` with source history and automatic
call-boundary checkpoints; the todo board is not involved.
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
MAX_ITEMS_EXECUTED = 40
PLANNER_TOOLS = ("read", "grep", "glob", "list")

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

llm makes one model request without tools or a session branch. agent starts a
free-form agent; select its model and tool set through agent_id, which names an
agent profile. Define ordinary Python helper functions and use llm for one model
request or agent for autonomous work. Compose helpers and registered functions
with plain Python calls, if/for/try statements, and return values. Verification
belongs in the program and may raise an exception when it fails. There is no
step DSL.

Example:

    def find_issues():
        return agent(\"Review the codebase for issues\", description=\"find issues\")

    def workflow():
        findings = find_issues()
        if not findings:
            raise RuntimeError(\"issue review returned no result\")
        return findings

Available registered agentic functions (name, signature, first docstring
line):
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
        raise RuntimeError(result.error or "task list planning turn failed")
    return result.final_text or ""


def _agent_function() -> Callable:
    from openprogram.functions.tools.agent.agent.agent import _agent_impl

    return _agent_impl


def _llm_function() -> Callable:
    from openprogram.agentic_programming import llm

    return llm


def _registered_agentic_functions() -> dict[str, Callable]:
    """Resolve Python-callable entries defined by AGENTIC_MODULES."""
    from openprogram.functions._registry import AGENTIC_MODULES

    found: dict[str, Callable] = {}
    for module_name in AGENTIC_MODULES:
        try:
            module = importlib.import_module(
                f"openprogram.functions.agentics.{module_name}"
            )
        except Exception:
            continue
        for value in vars(module).values():
            inner = getattr(value, "_fn", None)
            if inner is not None and inner.__module__ == module.__name__:
                found[inner.__name__] = value
    return found


def _function_catalog(functions: dict[str, Callable]) -> str:
    rows = []
    for name, function in sorted(functions.items()):
        inner = getattr(function, "_fn", None) or function
        try:
            signature = inspect.signature(inner)
        except (TypeError, ValueError):
            signature = "(...)"
        doc = inspect.getdoc(inner) or ""
        rows.append(f"- {name}{signature}: {doc.splitlines()[0] if doc else ''}")
    return "\n".join(rows) or "(none)"


def _plan_prompt(task: str, functions: dict[str, Callable]) -> str:
    return (
        PLANNER_INSTRUCTIONS.replace("{catalog}", _function_catalog(functions))
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


def _result(state: dict, run_id: str) -> dict:
    return {
        "status": state["status"],
        "task": state["task"],
        "run_id": run_id,
        "items": state["items"],
        "revisions": state["revisions"],
        "summary": clip_handoff(state.get("result", "")),
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
        "agent": checkpoints.wrap("agent", _agent_function()),
        **{name: checkpoints.wrap(name, fn) for name, fn in functions.items()},
    }
    exec(compile(source, "code.py", "exec"), namespace, namespace)
    return namespace["workflow"]()


def _rewrite_prompt(task: str, source: str, state: dict, error: str,
                    functions: dict[str, Callable]) -> str:
    return (
        PLANNER_INSTRUCTIONS.replace("{catalog}", _function_catalog(functions))
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
                spawn_caller=spawn_caller, label="task list planner",
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

        summary = checkpoints.wrap("agent", _agent_function())(state["task"])
        state.update(status="completed", result=clip_handoff(summary))
        _save_state(state_path, state)
        return _result(state, run_id)


@_real_agentic_function(input={
    "task": {"description": "The task to plan and execute", "multiline": True},
    "run_id": {"description": "Existing isolated workflow run to resume"},
    "session_id": {"hidden": True},
    "spawn_caller": {"hidden": True},
    "agent_id": {"hidden": True},
})
def run_task_list(task: str, session_id: str = "", run_id: str = "",
                  spawn_caller: Optional[str] = None,
                  agent_id: str = "main") -> dict:
    """Plan and execute a new workflow, or explicitly resume ``run_id``."""
    sid = session_id or current_session_id()
    functions = _registered_agentic_functions()
    if run_id:
        instance = _instance_dir(sid, run_id)
        with _WORKFLOW_LOCK:
            state = _load_state(instance / "state.json")
            code_path = instance / "code.py"
            if not code_path.exists():
                source = _request_valid_source(
                    state["task"], "", state, session_id=sid,
                    agent_id=agent_id, spawn_caller=spawn_caller,
                    functions=functions,
                )
                atomic_write_text(code_path, source + "\n" if source == "SINGLE" else source)
            else:
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

    run_id = _new_run_id()
    instance = _instance_dir(sid, run_id)
    instance.mkdir(parents=True, exist_ok=False)
    state = {
        "run_id": run_id, "task": task, "status": "running",
        "executions": 0, "items": [], "revisions": [], "result": "",
        "last_error": "",
    }
    _save_state(instance / "state.json", state)
    source = _request_valid_source(
        task, "", state, session_id=sid, agent_id=agent_id,
        spawn_caller=spawn_caller, functions=functions,
    )
    if source == "SINGLE":
        atomic_write_text(instance / "code.py", "SINGLE\n")
        return _run_single(
            instance, run_id=run_id, session_id=sid, agent_id=agent_id,
            spawn_caller=spawn_caller,
        )
    atomic_write_text(instance / "code.py", source)
    return _run_instance(
        instance, state, source, run_id=run_id, session_id=sid,
        agent_id=agent_id, spawn_caller=spawn_caller,
        functions=functions,
    )


def resume_workflow(run_id: str, session_id: str = "", *,
                    spawn_caller: Optional[str] = None,
                    agent_id: str = "main") -> dict:
    """Resume one explicit workflow instance by id."""
    sid = session_id or current_session_id()
    instance = _instance_dir(sid, run_id)
    state = _load_state(instance / "state.json")
    return run_task_list(
        state["task"], session_id=sid, run_id=run_id,
        spawn_caller=spawn_caller, agent_id=agent_id,
    )


__all__ = [
    "run_task_list", "resume_workflow", "InvalidWorkflow",
    "WorkflowExecutionCapped", "PLANNER_TOOLS",
    "HANDOFF_SUMMARY_MAX_CHARS", "MAX_ITEMS_EXECUTED",
]
