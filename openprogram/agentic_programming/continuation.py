"""Durable, explicitly delimited function steps on the canonical execution store.

Orchestration outside ``step`` must be deterministic and free of side effects.
A step result is JSON data, never a pickled Python frame or executable object.
"""

from __future__ import annotations

import ast
from contextlib import closing, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
import hashlib
import importlib
import inspect
import json
import platform
import sys
from pathlib import Path
import textwrap
import time
import types

from openprogram.execution import AttemptStore, RuntimeControlService
from openprogram.execution.checkpoints import CheckpointFragment
from openprogram.execution.driver import DriverRegistry
from openprogram.execution.effects import (
    EffectClassification,
    EffectStatus,
    EffectStore,
)
from openprogram.execution.model import CommandKind, CommandStatus


class FunctionSuspended(BaseException):
    """An invocation has durable progress and stopped at an explicit boundary."""


class FunctionCompatibilityError(FunctionSuspended):
    """Saved execution cannot safely use the requested code or inputs."""


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _result_json(value):
    if value is None or type(value) in {bool, int, float, str}:
        return _json(value)
    if type(value) is list:
        for item in value:
            _result_json(item)
        return _json(value)
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _result_json(item)
        return _json(value)
    raise FunctionCompatibilityError(
        "Step results require JSON values with string object keys"
    )


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _module_identity(module):
    # A package initializer does not identify its transitive implementation.
    # User helpers are retained as source; opaque modules need a pinned runtime.
    import sysconfig

    filename = getattr(module, "__file__", None)
    standard_library = Path(sysconfig.get_path("stdlib")).resolve()
    if module.__name__.split(".")[0] not in sys.stdlib_module_names or (
        filename
        and (
            not Path(filename).resolve().is_relative_to(standard_library)
            or "site-packages" in Path(filename).parts
        )
    ):
        raise FunctionCompatibilityError(
            "Opaque non-standard-library dependencies cannot be retained; use source-defined Python helpers"
        )
    return hashlib.sha256(Path(filename).read_bytes()).hexdigest() if filename else None


def _validate_orchestration(fn):
    """Reject uncheckpointed calls; all external work must have a step receipt."""
    _validate_source(getattr(fn, "__durable_source__", None) or inspect.getsource(fn))
    import builtins

    closure = inspect.getclosurevars(fn)
    bindings = {**closure.globals, **closure.nonlocals}
    for name in {
        "step",
        "workflow",
        "parallel",
        "range",
        "enumerate",
        "zip",
        "len",
        "min",
        "max",
        "sum",
        "sorted",
        "str",
        "int",
        "float",
        "bool",
        "list",
        "dict",
        "tuple",
    }:
        expected = (
            globals()[name]
            if name in {"step", "workflow", "parallel"}
            else getattr(builtins, name)
        )
        if name in bindings and bindings[name] is not expected:
            raise FunctionCompatibilityError(
                "Durable API and builtin bindings cannot be replaced"
            )


def _validate_source(source):
    tree = ast.parse(textwrap.dedent(source))
    root = tree.body[0]
    if not isinstance(root, ast.FunctionDef):
        raise FunctionCompatibilityError(
            "Only synchronous functions have this durable step contract"
        )
    root.decorator_list = []
    allowed_calls = {
        "step",
        "workflow",
        "parallel",
        "range",
        "enumerate",
        "zip",
        "len",
        "min",
        "max",
        "sum",
        "sorted",
        "str",
        "int",
        "float",
        "bool",
        "list",
        "dict",
        "tuple",
    }
    if any(
        arg.arg in allowed_calls
        for arg in (*root.args.posonlyargs, *root.args.args, *root.args.kwonlyargs)
    ):
        raise FunctionCompatibilityError(
            "Durable API and builtin names cannot be replaced by arguments"
        )
    for node in ast.walk(root):
        if isinstance(
            node,
            (
                ast.Global,
                ast.Nonlocal,
                ast.With,
                ast.AsyncWith,
                ast.Await,
                ast.Yield,
                ast.YieldFrom,
                ast.Try,
                ast.ClassDef,
                ast.Delete,
            ),
        ):
            raise FunctionCompatibilityError(
                "Durable orchestration must use explicit steps and JSON state"
            )
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Name) or node.func.id not in allowed_calls
        ):
            raise FunctionCompatibilityError(
                "Calls outside a durable step are not resumable"
            )
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if any(
                    isinstance(item, (ast.Attribute, ast.Subscript))
                    or isinstance(item, ast.Name)
                    and item.id in allowed_calls
                    for item in ast.walk(target)
                ):
                    raise FunctionCompatibilityError(
                        "External mutation and API replacement must not occur in orchestration"
                    )
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"sorted", "min", "max"}
            and any(keyword.arg == "key" for keyword in node.keywords)
        ):
            raise FunctionCompatibilityError(
                "Implicit callbacks must be executed inside a step"
            )
        if isinstance(node, (ast.For, ast.comprehension, ast.NamedExpr)) and any(
            isinstance(item, ast.Name) and item.id in allowed_calls
            for item in ast.walk(node.target)
        ):
            raise FunctionCompatibilityError(
                "Durable API and builtin names cannot be rebound"
            )
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if (
                not isinstance(node, ast.ImportFrom)
                or node.module != "openprogram.agentic_programming.continuation"
                or any(
                    alias.name not in {"step", "workflow", "parallel"} or alias.asname
                    for alias in node.names
                )
            ):
                raise FunctionCompatibilityError(
                    "Import action dependencies outside durable orchestration"
                )


def _snapshot(fn):
    """Capture reachable Python helpers; pin imported symbols by module content."""
    functions = {}
    function_ids = {}
    objects = {}

    def capture(value):
        if getattr(value, "resumable", False) and inspect.isfunction(
            getattr(value, "_fn", None)
        ):
            value = value._fn
        if (
            inspect.isfunction(value)
            and value.__module__ == __name__
            and value.__name__ in {"step", "workflow", "parallel"}
        ):
            return {"api": value.__name__, "version": 1}
        if inspect.isfunction(value):
            if id(value) in function_ids:
                return {"function": function_ids[id(value)]}
            identity = f"function-{len(functions)}"
            function_ids[id(value)] = identity
            functions[identity] = None
            tree = ast.parse(
                textwrap.dedent(
                    getattr(value, "__durable_source__", None)
                    or inspect.getsource(value)
                )
            )
            node = tree.body[0]
            if not isinstance(node, ast.FunctionDef):
                raise FunctionCompatibilityError(
                    "Only synchronous source-defined functions support durable steps"
                )
            if any(
                isinstance(item, (ast.Global, ast.Nonlocal)) for item in ast.walk(node)
            ):
                raise FunctionCompatibilityError(
                    "Mutable helper state must be explicit JSON step input and output"
                )
            for item in ast.walk(node):
                if isinstance(item, (ast.Import, ast.ImportFrom)) and not (
                    isinstance(item, ast.ImportFrom)
                    and item.module == "openprogram.agentic_programming.continuation"
                    and all(
                        alias.name in {"step", "workflow", "parallel"}
                        and alias.asname is None
                        for alias in item.names
                    )
                ):
                    raise FunctionCompatibilityError(
                        "Import step dependencies at module scope so their versions can be retained"
                    )
            node.decorator_list = []
            node.returns = None
            for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
                arg.annotation = None
            if node.args.vararg:
                node.args.vararg.annotation = None
            if node.args.kwarg:
                node.args.kwarg.annotation = None
            # Defaults are persisted values, not expressions evaluated during restore.
            node.args.defaults = []
            node.args.kw_defaults = [None] * len(node.args.kwonlyargs)
            closure = inspect.getclosurevars(value)
            bindings = {**closure.globals, **closure.nonlocals}
            import builtins

            if any(
                item is dynamic
                for item in (*bindings.values(), *closure.builtins.values())
                for dynamic in (
                    builtins.__import__,
                    builtins.eval,
                    builtins.exec,
                    builtins.compile,
                )
            ):
                raise FunctionCompatibilityError(
                    "Dynamic imports and generated code cannot retain dependency versions"
                )
            functions[identity] = {
                "source": ast.unparse(tree),
                "name": node.name,
                "module": value.__module__,
                "qualname": value.__qualname__,
                "bindings": {name: capture(item) for name, item in bindings.items()},
                "defaults": capture(value.__defaults__),
                "kwdefaults": (
                    {
                        "mapping": {
                            name: capture(item)
                            for name, item in value.__kwdefaults__.items()
                        }
                    }
                    if value.__kwdefaults__ is not None
                    else capture(None)
                ),
            }
            return {"function": identity}
        if isinstance(value, types.ModuleType):
            if value.__name__.split(".")[0] in {"importlib", "builtins"}:
                raise FunctionCompatibilityError(
                    "Dynamic import modules cannot retain dependency versions"
                )
            return {
                "module": value.__name__,
                "sha256": _module_identity(value),
                "path": getattr(value, "__file__", None),
            }
        if inspect.isclass(value) or inspect.isbuiltin(value):
            module = importlib.import_module(value.__module__)
            return {
                "symbol": value.__qualname__,
                "module": module.__name__,
                "sha256": _module_identity(module),
                "path": getattr(module, "__file__", None),
            }
        # Round-trip at admission rejects live handles and preserves caller-owned values.
        if isinstance(value, tuple):
            return {"tuple": [capture(item) for item in value]}
        if value is not None and type(value) not in {bool, int, float, str}:
            raise FunctionCompatibilityError(
                "Captured values must be immutable JSON scalars or tuples; pass state through step inputs and results"
            )
        _json(value)
        key = _digest(value)
        objects[key] = value
        return {"value": key}

    root = capture(fn)
    return {
        "root": root,
        "functions": functions,
        "values": objects,
        "schema": 1,
        "runtime": [sys.implementation.cache_tag, sys.platform, platform.machine()],
    }


def _restore(snapshot):
    if snapshot.get("runtime") != [
        sys.implementation.cache_tag,
        sys.platform,
        platform.machine(),
    ]:
        raise FunctionCompatibilityError("Retained Python runtime is unavailable")
    loaded = {}
    namespaces = {}
    for identity, record in snapshot["functions"].items():
        namespace = {"__builtins__": __builtins__, "__name__": record["module"]}
        exec(compile(record["source"], "<retained-function>", "exec"), namespace)
        loaded[identity] = namespace[record["name"]]
        loaded[identity].__durable_source__ = record["source"]
        loaded[identity].__qualname__ = record["qualname"]
        namespaces[identity] = namespace

    def restore(item):
        if "api" in item:
            if item.get("version") != 1 or item["api"] not in {
                "step",
                "workflow",
                "parallel",
            }:
                raise FunctionCompatibilityError(
                    "Unsupported continuation API contract"
                )
            return globals()[item["api"]]
        if "function" in item:
            return loaded[item["function"]]
        if "tuple" in item:
            return tuple(restore(value) for value in item["tuple"])
        if "mapping" in item:
            return {name: restore(value) for name, value in item["mapping"].items()}
        if "value" in item:
            return json.loads(_json(snapshot["values"][item["value"]]))
        try:
            # Verify the retained file before importing potentially changed code.
            if (
                item.get("path")
                and hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest()
                != item["sha256"]
            ):
                raise FunctionCompatibilityError(
                    f"Retained dependency is unavailable: {item['module']}"
                )
            module = importlib.import_module(item["module"])
            if _module_identity(module) != item["sha256"]:
                raise FunctionCompatibilityError(
                    f"Retained dependency is unavailable: {item['module']}"
                )
            result = module
            for part in item.get("symbol", "").split(".") if "symbol" in item else ():
                result = getattr(result, part)
            return result
        except (ImportError, OSError, AttributeError) as exc:
            raise FunctionCompatibilityError(
                f"Retained dependency is unavailable: {item['module']}"
            ) from exc

    for identity, record in snapshot["functions"].items():
        namespaces[identity].update(
            {name: restore(item) for name, item in record["bindings"].items()}
        )
        loaded[identity].__defaults__ = restore(record["defaults"])
        loaded[identity].__kwdefaults__ = restore(record["kwdefaults"])
    return restore(snapshot["root"])


@dataclass
class _Execution:
    store: object
    attempt_id: str
    generation: int
    call_key: str
    policy: str
    execution_id: str
    checkpoint_root: bool = True
    frames: list = field(default_factory=list)
    ancestors: tuple = ()

    def owned(self, connection):
        attempts = AttemptStore(self.store)
        attempt = attempts._require(connection, self.attempt_id)
        attempts._validate_generation(attempt, self.generation)
        attempts._validate_lease(attempt, time.time())
        execution = self.store._require_execution(connection, self.execution_id)
        attempts._validate_owner(execution, attempt, execution.status_version)
        if attempt.status.value != "active":
            raise FunctionCompatibilityError("Function owner is no longer active")
        return execution

    def checkpoint(self, connection, execution, step_id):
        if not self.checkpoint_root:
            return
        from openprogram.execution.checkpoints import ExecutionCheckpointStore
        from openprogram.execution.restart import window_seconds

        ExecutionCheckpointStore(self.store)._publish_in_transaction(
            connection,
            execution_id=self.execution_id,
            expected_version=execution.status_version,
            revision_id=execution.revision_id,
            parent_checkpoint_id=execution.checkpoint_head_id,
            frontier=({"kind": "function.step.after", "step_id": step_id},),
            state_refs={
                "function": {
                    "version": 1,
                    "call_key": self.call_key,
                    "policy": self.policy,
                },
                "restart_window_seconds": window_seconds(),
            },
            completed_actions=(),
            effect_receipts=(),
            child_frontier={},
            pending_command_ids=(),
            created_by_attempt_id=self.attempt_id,
        )

    def save(self, kind, key, value):
        with self.store._transaction() as connection:
            execution = self.owned(connection)
            if (
                kind == "function.admitted"
                and connection.execute(
                    "SELECT 1 FROM execution_events WHERE execution_id = ? AND kind = 'function.admitted' "
                    "AND json_extract(payload_json, '$.call_key') = ? LIMIT 1",
                    (self.execution_id, key),
                ).fetchone()
            ):
                raise FunctionCompatibilityError(
                    "Function invocation was concurrently admitted"
                )
            ref = self.store._put_state_blob_in_transaction(
                connection,
                execution_id=self.execution_id,
                payload=_json(value),
                media_type="application/json",
                schema_version=1,
            )
            connection.execute(
                "INSERT OR IGNORE INTO execution_state_blob_refs VALUES (?, ?, ?, ?, ?, ?)",
                (self.execution_id, ref["ref"], kind, "function", key, time.time()),
            )
            self.store._append_event(
                connection,
                execution_id=self.execution_id,
                kind=kind,
                payload={
                    "call_key": key,
                    "ref": ref["ref"],
                    **(
                        {"reason": value["reason"], "policy": value["policy"]}
                        if kind == "function.incompatible"
                        else {}
                    ),
                },
                created_at=time.time(),
                execution_version=execution.status_version,
            )
            if kind in {"function.admitted", "function.completed"}:
                self.checkpoint(connection, execution, kind)
        return ref["ref"]

    def load(self, kind, key):
        # Load invocation metadata once per entry; individual steps use indexed effects.
        for event in reversed(self.store.list_events(self.execution_id)):
            if event.kind == kind and event.payload.get("call_key") == key:
                blob = self.store.get_state_blob(
                    self.execution_id, event.payload["ref"]
                )
                if blob is None:
                    raise FunctionCompatibilityError(
                        "Function continuation data is missing"
                    )
                return json.loads(blob["payload"])
        return None

    def boundary(self):
        from openprogram.agentic_programming.function import CancelledError

        execution = self.store.get_execution(self.execution_id)
        if execution.status.value in {"cancelling", "cancelled"}:
            raise CancelledError()
        commands = self.store.list_commands(
            self.execution_id,
            kinds=(CommandKind.PAUSE,),
            statuses=(CommandStatus.ACCEPTED, CommandStatus.APPLYING),
        )
        if commands:
            raise FunctionSuspended()


_current = ContextVar("durable_function_execution", default=None)


@contextmanager
def function_execution(
    store,
    *,
    attempt_id,
    generation,
    call_key,
    policy="keep_original",
    publish_pause=True,
    checkpoint_root=True,
):
    """Bind an exact canonical owner; caller identities must survive retries."""
    if policy not in {"keep_original", "use_latest"}:
        raise ValueError("Unknown function code change policy")
    attempt = AttemptStore(store).get(attempt_id)
    if attempt is None:
        raise FunctionCompatibilityError("Function attempt is missing")
    context = _Execution(
        store,
        attempt_id,
        generation,
        call_key,
        policy,
        attempt.execution_id,
        checkpoint_root=checkpoint_root,
    )
    with store._transaction() as connection:
        context.owned(connection)
    token = _current.set(context)
    try:
        yield
    except FunctionSuspended as exc:
        if isinstance(exc, FunctionCompatibilityError):
            import asyncio

            context.save(
                "function.incompatible",
                call_key,
                {"reason": str(exc), "policy": policy},
            )
            current = store.get_execution(context.execution_id)
            if current.status.value == "running":
                asyncio.run(
                    RuntimeControlService(
                        store, AttemptStore(store), DriverRegistry()
                    ).request_pause(
                        command_id="function-incompatible-" + attempt_id,
                        execution_id=context.execution_id,
                        expected_version=current.status_version,
                        actor={
                            "surface": "function",
                            "reason": "function_incompatible",
                        },
                    )
                )
        execution = store.get_execution(context.execution_id)
        commands = store.list_commands(
            context.execution_id,
            kinds=(CommandKind.PAUSE,),
            statuses=(CommandStatus.ACCEPTED, CommandStatus.APPLYING),
        )
        if commands and publish_pause:
            from openprogram.execution.restart import window_seconds

            RuntimeControlService(
                store, AttemptStore(store), DriverRegistry()
            ).arrive_safe_point(
                attempt_id=attempt_id,
                generation=generation,
                command_id=commands[0].command_id,
                expected_execution_version=execution.status_version,
                fragment=CheckpointFragment(
                    safe_point_kind="function.step.after",
                    frontier=({"kind": "function.step.after", "call_key": call_key},),
                    state_refs={
                        "function": {
                            "version": 1,
                            "call_key": call_key,
                            "policy": policy,
                        },
                        "restart_window_seconds": window_seconds(),
                    },
                ),
            )
        raise
    finally:
        _current.reset(token)


def invoke(fn, name, args, kwargs):
    context = _current.get()
    if context is None:
        return fn(*args, **kwargs)
    if context.frames:
        raise FunctionCompatibilityError(
            "Nested durable functions require an explicit step boundary"
        )
    key = context.call_key
    arguments = _digest({"args": args, "kwargs": kwargs})
    saved = context.load("function.admitted", key)
    if saved is None:
        _validate_orchestration(fn)
        snapshot = _snapshot(fn)
        context.save(
            "function.admitted",
            key,
            {
                "name": name,
                "arguments": arguments,
                "code": snapshot,
                "policy": context.policy,
            },
        )
    else:
        if saved["name"] != name or saved["arguments"] != arguments:
            raise FunctionCompatibilityError("Function identity or input changed")
        result = context.load("function.completed", key)
        if result is not None:
            context.boundary()
            return result["result"]
        active_version = context.load("function.version_active", key)
        if context.policy == "keep_original":
            snapshot = (
                active_version["code"] if active_version is not None else saved["code"]
            )
            fn = _restore(snapshot)
        else:
            _validate_orchestration(fn)
            snapshot = _snapshot(fn)
    saved_nodes = [
        dict(event.payload["node"])
        for event in context.store.list_events(context.execution_id)
        if event.kind == "function.node" and event.payload.get("call_key") == key
    ]
    frame = {
        "key": key,
        "counts": {},
        "saved": saved_nodes,
        "cursor": 0,
        "code": snapshot,
        "code_hash": _digest(snapshot),
        "version_saved": False,
    }
    context.frames.append(frame)
    try:
        context.boundary()
        result = fn(*args, **kwargs)
        if frame["cursor"] < len(frame["saved"]):
            raise FunctionCompatibilityError(
                "New code removed a previously executed step"
            )
        _result_json(result)
        _activate_version(context, frame)
        context.save("function.completed", key, {"result": result})
        return result
    finally:
        context.frames.pop()


def _activate_version(context, frame):
    if frame["version_saved"]:
        return
    for ancestor_context, ancestor_frame in context.ancestors:
        _activate_version(ancestor_context, ancestor_frame)
    context.save(
        "function.version_active",
        frame["key"],
        {
            "code": frame["code"],
            "code_hash": frame["code_hash"],
            "policy": context.policy,
        },
    )
    frame["version_saved"] = True


def _node(context, kind, name):
    frame = context.frames[-1]
    counter = (kind, name)
    occurrence = frame["counts"].get(counter, 0)
    frame["counts"][counter] = occurrence + 1
    identity = {
        "call": frame["key"],
        "kind": kind,
        "step": name,
        "occurrence": occurrence,
    }
    if frame["cursor"] < len(frame["saved"]):
        if frame["saved"][frame["cursor"]] != identity:
            raise FunctionCompatibilityError(
                f"New code changed the recorded step order: {name}"
            )
    else:
        context.boundary()
        _activate_version(context, frame)
        with context.store._transaction() as connection:
            execution = context.owned(connection)
            context.store._append_event(
                connection,
                execution_id=context.execution_id,
                kind="function.node",
                payload={"call_key": frame["key"], "node": identity},
                created_at=time.time(),
                execution_version=execution.status_version,
            )
    frame["cursor"] += 1
    return identity


def workflow(name, fn, *args, **kwargs):
    """A nested durable orchestration with its own stable step namespace."""
    context = _current.get()
    if context is None:
        return fn(*args, **kwargs)
    if not isinstance(name, str) or not name or not context.frames:
        raise FunctionCompatibilityError("A nested workflow needs a stable name")
    identity = _node(context, "workflow", name)
    child = replace(
        context,
        call_key=context.call_key + "/" + _digest(identity),
        frames=[],
        checkpoint_root=False,
        ancestors=(*context.ancestors, (context, context.frames[-1])),
    )
    token = _current.set(child)
    try:
        return invoke(getattr(fn, "_fn", fn), name, args, kwargs)
    finally:
        _current.reset(token)


def parallel(branches):
    """Run named workflows concurrently; wait for every branch before suspending.

    Each value is ``(function, positional_args, keyword_args)``. Branch names
    and admission order are stable; completion order does not define identity.
    """
    from concurrent.futures import ThreadPoolExecutor

    context = _current.get()
    prepared = []
    for name, (fn, args, kwargs) in sorted(branches.items()):
        if context is None:
            prepared.append((name, fn, args, kwargs, None))
        else:
            identity = _node(context, "workflow", name)
            prepared.append(
                (
                    name,
                    getattr(fn, "_fn", fn),
                    args,
                    kwargs,
                    replace(
                        context,
                        call_key=context.call_key + "/" + _digest(identity),
                        frames=[],
                        checkpoint_root=False,
                        ancestors=(*context.ancestors, (context, context.frames[-1])),
                    ),
                )
            )

    def run(item):
        name, fn, args, kwargs, child = item
        token = _current.set(child)
        try:
            return (
                invoke(fn, name, args, kwargs)
                if child is not None
                else fn(*args, **kwargs)
            )
        finally:
            _current.reset(token)

    with ThreadPoolExecutor(max_workers=min(8, max(1, len(prepared)))) as pool:
        futures = [(item[0], pool.submit(run, item)) for item in prepared]
        results = {}
        failure = None
        for name, future in futures:
            try:
                results[name] = future.result()
            except BaseException as exc:
                if failure is None or not isinstance(exc, FunctionSuspended):
                    failure = exc
        if failure is not None:
            raise failure
        return results


def step(name, fn, *args, **kwargs):
    """Execute one action once, or recover its committed JSON result."""
    context = _current.get()
    if context is None:
        return fn(*args, **kwargs)
    if not context.frames or not isinstance(name, str) or not name:
        raise FunctionCompatibilityError(
            "A durable step needs a function and a stable nonempty name"
        )
    identity = _node(context, "step", name)
    action_id = _digest(identity)
    effect_id = "function-step-" + _digest(
        {"execution": context.execution_id, "attempt": context.attempt_id, **identity}
    )
    effects = EffectStore(context.store)
    with closing(context.store._connect()) as connection:
        row = connection.execute(
            "SELECT * FROM effects WHERE execution_id = ? AND action_id = ? "
            "AND status IN ('committed', 'dispatched', 'uncertain') ORDER BY created_at DESC LIMIT 1",
            (context.execution_id, action_id),
        ).fetchone()
    effect = effects._record(row) if row else None
    input_hash = _digest({"args": args, "kwargs": kwargs})
    if effect is not None:
        if effect.metadata["input_hash"] != input_hash:
            raise FunctionCompatibilityError(f"Completed step inputs changed: {name}")
        if effect.status is not EffectStatus.COMMITTED:
            raise FunctionCompatibilityError(
                f"Step outcome needs reconciliation: {name}"
            )
        blob = context.store.get_state_blob(
            context.execution_id, effect.receipt["result_ref"]
        )
        if blob is None:
            raise FunctionCompatibilityError(f"Step result is missing: {name}")
        return json.loads(blob["payload"])
    context.boundary()
    effects.register(
        effect_id=effect_id,
        execution_id=context.execution_id,
        attempt_id=context.attempt_id,
        action_id=action_id,
        classification=EffectClassification.UNKNOWN,
        idempotency_key=None,
        metadata={
            "function_step": identity,
            "input_hash": input_hash,
            "code_hash": context.frames[-1]["code_hash"],
        },
    )
    effects.mark_dispatched(effect_id, expected_status=EffectStatus.PLANNED)
    result = fn(*args, **kwargs)
    # Persist result and effect receipt atomically. A crash before this transaction
    # remains unresolved rather than reissuing a possibly completed external write.
    with context.store._transaction() as connection:
        execution = context.owned(connection)
        current = effects._require(connection, effect_id)
        if current.status is not EffectStatus.DISPATCHED:
            raise FunctionCompatibilityError("Step was concurrently reconciled")
        ref = context.store._put_state_blob_in_transaction(
            connection,
            execution_id=context.execution_id,
            payload=_result_json(result),
            media_type="application/json",
            schema_version=1,
        )
        now = time.time()
        connection.execute(
            "INSERT OR IGNORE INTO execution_state_blob_refs VALUES (?, ?, ?, ?, ?, ?)",
            (
                context.execution_id,
                ref["ref"],
                "result",
                "function_step",
                effect_id,
                now,
            ),
        )
        connection.execute(
            "UPDATE effects SET status = ?, receipt_json = ?, updated_at = ?, resolved_at = ? WHERE effect_id = ?",
            ("committed", _json({"result_ref": ref["ref"]}), now, now, effect_id),
        )
        effects._append_event(
            connection,
            execution.status_version,
            effects._require(connection, effect_id),
            now,
        )
        context.checkpoint(connection, execution, effect_id)
    context.boundary()
    return result


def default_policy(store=None, execution_id=None):
    if store is not None and execution_id:
        commands = store.list_commands(
            execution_id,
            kinds=(CommandKind.CONTINUE,),
            statuses=(CommandStatus.APPLYING, CommandStatus.APPLIED),
        )
        for command in sorted(
            commands, key=lambda item: item.expected_version, reverse=True
        ):
            if command.payload.get("code_change_policy") in {
                "keep_original",
                "use_latest",
            }:
                return command.payload["code_change_policy"]
        for event in store.list_events(execution_id):
            if event.kind == "function.admitted":
                blob = store.get_state_blob(execution_id, event.payload["ref"])
                if blob:
                    return json.loads(blob["payload"]).get("policy", "keep_original")
    from openprogram.setup import _read_config

    settings = _read_config().get("execution", {})
    value = (
        settings.get("code_change_policy", "keep_original")
        if isinstance(settings, dict)
        else "keep_original"
    )
    return value if value in {"keep_original", "use_latest"} else "keep_original"


def suspension_evidence(store, connection, execution_id, call_key):
    """Prove an aggregate tool invocation contains only settled durable steps."""
    admitted = connection.execute(
        "SELECT 1 FROM execution_events WHERE execution_id = ? AND kind = 'function.admitted' "
        "AND json_extract(payload_json, '$.call_key') = ? LIMIT 1",
        (execution_id, call_key),
    ).fetchone()
    if admitted is None:
        return False
    uncertain = connection.execute(
        "SELECT 1 FROM effects WHERE execution_id = ? AND status IN ('dispatched', 'uncertain') "
        "AND (json_extract(metadata_json, '$.function_step.call') = ? OR "
        "substr(json_extract(metadata_json, '$.function_step.call'), 1, ?) = ?) LIMIT 1",
        (execution_id, call_key, len(call_key) + 1, call_key + "/"),
    ).fetchone()
    return uncertain is None


def retained_function_names(store, execution_id):
    from openprogram.programs._runtime import get

    names = set()
    for event in store.list_events(execution_id):
        if event.kind != "function.admitted":
            continue
        blob = store.get_state_blob(execution_id, event.payload["ref"])
        if blob is None:
            continue
        saved = json.loads(blob["payload"])
        name = saved.get("name")
        tool = get(name) if isinstance(name, str) else None
        if tool is not None and getattr(tool, "_resumable", False):
            names.add(name)
    return names


def source_capability(source, name):
    """Read-only capability discovery; never imports an untrusted program."""
    try:
        tree = ast.parse(source)
        node = next(
            (
                item
                for item in tree.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                and item.name == name
            ),
            None,
        )
        if node is None or not any(
            isinstance(decorator, ast.Call)
            and any(
                keyword.arg == "resumable"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is True
                for keyword in decorator.keywords
            )
            for decorator in node.decorator_list
        ):
            return {"supported": False, "reason": "No durable step contract"}
        _validate_source(ast.unparse(node))
        return {"supported": True, "boundary": "function.step.after", "state": "json"}
    except (FunctionCompatibilityError, ValueError, SyntaxError) as exc:
        return {"supported": False, "reason": str(exc)}


def current_function_node_id():
    context = _current.get()
    if context is None or context.frames:
        return None
    return (
        "function_"
        + _digest({"execution": context.execution_id, "call": context.call_key})[:24]
    )
