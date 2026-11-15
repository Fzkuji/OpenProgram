"""Durable, explicitly delimited function steps, recorded in the function's own log.

Orchestration outside ``step`` must be deterministic and free of side effects.
A step result is JSON data, never a pickled Python frame or executable object.
"""

from __future__ import annotations

import ast
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
import hashlib
import importlib
import inspect
import json
import os
import platform
import sys
from pathlib import Path
import textwrap
import types

from openprogram.runtime.execution import AttemptStore, RuntimeControlService
from openprogram.runtime.execution.checkpoints import CheckpointFragment
from openprogram.runtime.execution.driver import DriverRegistry
from openprogram.runtime.execution.effects import EffectStatus
from openprogram.runtime.execution.function_log import DISPATCHED, FunctionLog, function_log_at
from openprogram.runtime.execution.model import CommandKind, CommandStatus


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
                or node.module != "openprogram.runtime.agent.durable_steps"
                or any(
                    alias.name not in {"step", "workflow", "parallel"} or alias.asname
                    for alias in node.names
                )
            ):
                raise FunctionCompatibilityError(
                    "Import action dependencies outside durable orchestration"
                )


def _source_callable(fn):
    """Return retained source without discarding a bound Agent receiver."""
    owner = getattr(fn, "__self__", None) or getattr(fn, "_agent_owner", None)
    source = getattr(fn, "_agent_method_source", None) or getattr(fn, "_fn", None) or fn
    source = inspect.unwrap(source)
    if inspect.ismethod(source):
        owner = source.__self__
        source = source.__func__
    if owner is not None and inspect.isfunction(source):
        return types.MethodType(source, owner)
    return source


def _receiver_state(owner):
    excluded = {"runtime", "context", "model", "effort", "instructions", "tools",
                "_options", "_method_tools"}
    state = {name: value for name, value in vars(owner).items()
             if name not in excluded and not callable(value)}
    configuration = {name: getattr(owner, name, None)
                     for name in ("model", "effort", "instructions", "tools")}
    configuration.update(getattr(owner, "_options", {}))
    configuration.pop("runtime", None)
    configuration.pop("context", None)
    _result_json(state)
    _result_json(configuration)
    return {"state": json.loads(_json(state)), "configuration": json.loads(_json(configuration))}


def _snapshot(fn):
    """Capture reachable Python helpers; pin imported symbols by module content."""
    functions = {}
    function_ids = {}
    objects = {}
    receivers = {}
    receiver_ids = {}

    def capture_receiver(owner, source):
        from openprogram.runtime.agent.agent_class import Agent
        if not isinstance(owner, Agent):
            raise FunctionCompatibilityError("Durable bound methods require an Agent receiver")
        if id(owner) not in receiver_ids:
            identity = f"receiver-{len(receivers)}"
            receiver_ids[id(owner)] = identity
            receivers[identity] = {**_receiver_state(owner), "methods": {}}
        identity = receiver_ids[id(owner)]
        tree = ast.parse(textwrap.dedent(getattr(source, "__durable_source__", None)
                                       or inspect.getsource(source)))
        parameter = next(iter(inspect.signature(source).parameters), "self")
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == parameter:
                method = getattr(owner, node.attr, None)
                if node.attr not in {"runtime", "context", "model", "effort", "instructions", "tools"} and not callable(method):
                    _result_json(method)
                    receivers[identity]["state"][node.attr] = json.loads(_json(method))
                if callable(method) and node.attr not in receivers[identity]["methods"]:
                    raw = _source_callable(method)
                    if inspect.ismethod(raw):
                        receivers[identity]["methods"][node.attr] = None
                        receivers[identity]["methods"][node.attr] = capture(raw.__func__)
                        capture_receiver(owner, raw.__func__)
        return identity

    def capture(value):
        if callable(value) and (getattr(value, "resumable", False) or inspect.ismethod(value)
                               or getattr(value, "_agent_owner", None) is not None
                               or getattr(value, "_is_agent_method", False)
                               or getattr(value, "_is_managed_function", False)):
            value = _source_callable(value)
        if inspect.ismethod(value):
            receiver = capture_receiver(value.__self__, value.__func__)
            return {"bound_method": capture(value.__func__), "receiver": receiver}
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
                    and item.module == "openprogram.runtime.agent.durable_steps"
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
        "receivers": receivers,
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
    restored_receivers = {}
    for identity, record in snapshot["functions"].items():
        namespace = {"__builtins__": __builtins__, "__name__": record["module"]}
        exec(compile(record["source"], "<retained-function>", "exec"), namespace)
        loaded[identity] = namespace[record["name"]]
        loaded[identity].__durable_source__ = record["source"]
        loaded[identity].__qualname__ = record["qualname"]
        namespaces[identity] = namespace

    def restore(item):
        if "bound_method" in item:
            identity = item["receiver"]
            if identity not in restored_receivers:
                from openprogram.runtime.agent.agent_class import Agent
                from openprogram.runtime.agent.call_state import _current_runtime
                from openprogram.runtime.context import Context
                record = snapshot["receivers"][identity]
                methods = {name: restore(source) for name, source in record["methods"].items()}
                retained_type = type("RetainedAgent", (Agent,), methods)
                receiver = object.__new__(retained_type)
                Agent.__init__(receiver, **record["configuration"],
                               runtime=_current_runtime.get(), context=Context.current())
                receiver.__dict__.update(json.loads(_json(record["state"])))
                restored_receivers[identity] = receiver
            return types.MethodType(restore(item["bound_method"]), restored_receivers[identity])
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


class _TableOwner:
    """The owner inside the process that holds the run tables."""

    def __init__(self, store, attempt_id, generation, execution_id, checkpoint_root):
        self.store = store
        self.attempt_id = attempt_id
        self.generation = generation
        self.execution_id = execution_id
        self.checkpoint_root = checkpoint_root

    def validate(self):
        with self.store._transaction() as connection:
            attempts = AttemptStore(self.store)
            attempt = attempts._require(connection, self.attempt_id)
            attempts._validate_generation(attempt, self.generation)
            attempts._validate_held(attempt)
            execution = self.store._require_execution(connection, self.execution_id)
            attempts._validate_owner(execution, attempt, execution.status_version)
            if attempt.status.value != "active":
                raise FunctionCompatibilityError("Function owner is no longer active")

    def stop_requested(self):
        execution = self.store.get_execution(self.execution_id)
        if execution.status.value in {"cancelling", "cancelled"}:
            return "cancel"
        pending = self.store.list_commands(
            self.execution_id,
            kinds=(CommandKind.PAUSE,),
            statuses=(CommandStatus.ACCEPTED, CommandStatus.APPLYING),
        )
        return "pause" if pending else None

    def admitted(self, call_key, policy):
        if self.checkpoint_root:
            publish_function_checkpoint(self.store, self.execution_id, self.attempt_id, call_key, policy)


class _ParentOwner:
    """The owner inside a function subprocess: its parent, the Worker."""

    def __init__(self, log, generation, parent_pid, on_admitted):
        self.log = log
        self.generation = generation
        self.parent_pid = parent_pid
        self.on_admitted = on_admitted

    def validate(self):
        if os.getppid() != self.parent_pid:
            raise FunctionSuspended("the process that owned this function has exited")

    def stop_requested(self):
        self.validate()
        requested = self.log.requested(self.generation)
        return "cancel" if "cancel" in requested else "pause" if "pause" in requested else None

    def admitted(self, call_key, policy):
        if self.on_admitted is not None:
            self.on_admitted(call_key, policy)


def publish_function_checkpoint(store, execution_id, attempt_id, call_key, policy):
    """Record where a restarted Worker finds this call's progress."""
    from openprogram.runtime.execution.checkpoints import ExecutionCheckpointStore
    from openprogram.runtime.execution.restart import window_seconds

    with store._transaction() as connection:
        execution = store._require_execution(connection, execution_id)
        ExecutionCheckpointStore(store)._publish_in_transaction(
            connection,
            execution_id=execution_id,
            expected_version=execution.status_version,
            revision_id=execution.revision_id,
            parent_checkpoint_id=execution.checkpoint_head_id,
            frontier=({"kind": "function.step.after", "step_id": "function.admitted"},),
            state_refs={
                "function": {"version": 1, "call_key": call_key, "policy": policy},
                "restart_window_seconds": window_seconds(),
            },
            completed_actions=(),
            effect_receipts=(),
            child_frontier={},
            pending_command_ids=(),
            created_by_attempt_id=attempt_id,
        )


@dataclass
class _Execution:
    log: FunctionLog
    owner: object
    attempt_id: str
    generation: int
    call_key: str
    policy: str
    execution_id: str
    frames: list = field(default_factory=list)
    ancestors: tuple = ()

    def save(self, kind, key, value):
        self.owner.validate()
        if kind == "function.admitted" and self.log.saved(kind, key) is not None:
            raise FunctionCompatibilityError("Function invocation was concurrently admitted")
        self.log.save(kind, key, value)
        if kind == "function.admitted" and key == self.call_key and not self.ancestors:
            self.owner.admitted(key, self.policy)

    def load(self, kind, key):
        return self.log.saved(kind, key)

    def boundary(self):
        from openprogram.host.cancellation import CancelledError

        stop = self.owner.stop_requested()
        if stop == "cancel":
            raise CancelledError()
        if stop == "pause":
            raise FunctionSuspended()


_current = ContextVar("durable_function_execution", default=None)


@contextmanager
def _bound(context):
    token = _current.set(context)
    try:
        yield
    finally:
        _current.reset(token)


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
    owner = _TableOwner(store, attempt_id, generation, attempt.execution_id, checkpoint_root)
    owner.validate()
    context = _Execution(
        store.function_log(attempt.execution_id), owner, attempt_id, generation,
        call_key, policy, attempt.execution_id,
    )
    try:
        with _bound(context):
            yield
    except FunctionSuspended as exc:
        if isinstance(exc, FunctionCompatibilityError):
            context.log.save("function.incompatible", call_key, {"reason": str(exc), "policy": policy})
            request_incompatible_pause(store, context.execution_id, attempt_id)
        if publish_pause:
            _publish_pause(store, context, attempt_id, generation, call_key, policy)
        raise


def request_incompatible_pause(store, execution_id, attempt_id, service=None):
    """Stop an execution whose saved progress cannot continue with this code."""
    import asyncio

    current = store.get_execution(execution_id)
    if current.status.value != "running":
        return
    control = service or RuntimeControlService(store, AttemptStore(store), DriverRegistry())
    asyncio.run(control.request_pause(
        command_id="function-incompatible-" + attempt_id,
        execution_id=execution_id,
        expected_version=current.status_version,
        actor={"surface": "function", "reason": "function_incompatible"},
    ))


def _publish_pause(store, context, attempt_id, generation, call_key, policy):
    execution = store.get_execution(context.execution_id)
    commands = store.list_commands(
        context.execution_id,
        kinds=(CommandKind.PAUSE,),
        statuses=(CommandStatus.ACCEPTED, CommandStatus.APPLYING),
    )
    if not commands:
        return
    from openprogram.runtime.execution.restart import window_seconds

    RuntimeControlService(store, AttemptStore(store), DriverRegistry()).arrive_safe_point(
        attempt_id=attempt_id,
        generation=generation,
        command_id=commands[0].command_id,
        expected_execution_version=execution.status_version,
        fragment=CheckpointFragment(
            safe_point_kind="function.step.after",
            frontier=({"kind": "function.step.after", "call_key": call_key},),
            state_refs={
                "function": {"version": 1, "call_key": call_key, "policy": policy},
                "restart_window_seconds": window_seconds(),
            },
        ),
    )


@contextmanager
def subprocess_function_execution(
    log_path, *, execution_id, attempt_id, generation, call_key, policy, on_admitted=None,
):
    """Bind a function subprocess to its own log; its parent keeps the run state."""
    log = function_log_at(log_path)
    context = _Execution(
        log, _ParentOwner(log, generation, os.getppid(), on_admitted),
        attempt_id, generation, call_key, policy, execution_id,
    )
    try:
        with _bound(context):
            yield
    except FunctionCompatibilityError as exc:
        log.save("function.incompatible", call_key, {"reason": str(exc), "policy": policy})
        raise


def invoke(fn, name, args, kwargs):
    revision = getattr(fn, "_workflow_revision", None)
    if revision is not None:
        # A workflow entry is identified by its package revision, not by a
        # source snapshot: it runs the loaded package code, roles included.
        return _invoke(fn, name, args, kwargs, revision=revision())
    fn = _source_callable(fn)
    if inspect.isfunction(fn) and args:
        from openprogram.runtime.agent.agent_class import Agent
        if isinstance(args[0], Agent):
            fn = types.MethodType(fn, args[0])
            args = args[1:]
    if inspect.ismethod(fn):
        bound = inspect.signature(fn).bind(*args, **kwargs)
        values = dict(bound.arguments)
        runtime = values.pop("runtime", None)
        return _invoke(fn, name, (), values, runtime=runtime)
    return _invoke(fn, name, args, kwargs)


def _invoke(fn, name, args, kwargs, *, runtime=None, revision=None):
    context = _current.get()
    if context is None:
        return fn(*args, **kwargs, **({"runtime": runtime} if runtime is not None else {}))
    if context.frames:
        raise FunctionCompatibilityError(
            "Nested durable functions require an explicit step boundary"
        )
    key = context.call_key
    arguments = _digest({"args": args, "kwargs": kwargs,
                         **({"receiver": _receiver_state(fn.__self__)} if inspect.ismethod(fn) else {})})
    saved = context.load("function.admitted", key)
    if saved is None:
        if revision is not None:
            snapshot = {"workflow": revision}
        else:
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
        if revision is not None:
            retained = active_version["code"] if active_version is not None else saved["code"]
            snapshot = {"workflow": revision}
            if retained != snapshot and context.policy == "keep_original":
                raise FunctionCompatibilityError(
                    "Workflow code changed since this run started; continue with the latest code"
                )
        elif context.policy == "keep_original":
            snapshot = (
                active_version["code"] if active_version is not None else saved["code"]
            )
            fn = _restore(snapshot)
        else:
            _validate_orchestration(fn)
            snapshot = _snapshot(fn)
    saved_nodes = context.log.nodes(key)
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
        result = fn(*args, **kwargs, **({"runtime": runtime} if runtime is not None else {}))
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
        context.owner.validate()
        context.log.add_node(frame["key"], identity)
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
        ancestors=(*context.ancestors, (context, context.frames[-1])),
    )
    token = _current.set(child)
    try:
        return invoke(_source_callable(fn), name, args, kwargs)
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
                    _source_callable(fn),
                    args,
                    kwargs,
                    replace(
                        context,
                        call_key=context.call_key + "/" + _digest(identity),
                        frames=[],
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
    return _step(name, fn, args, kwargs)


def _step(name, fn, args, kwargs):
    context = _current.get()
    if context is None:
        return fn(*args, **kwargs)
    if not context.frames or not isinstance(name, str) or not name:
        raise FunctionCompatibilityError(
            "A durable step needs a function and a stable nonempty name"
        )
    identity = _node(context, "step", name)
    action_id = _digest(identity)
    input_hash = _digest({"args": args, "kwargs": kwargs})
    recorded = context.log.step(action_id)
    if recorded is not None and recorded.status in {
        DISPATCHED, EffectStatus.COMMITTED.value, EffectStatus.UNCERTAIN.value,
    }:
        if recorded.input_hash != input_hash:
            raise FunctionCompatibilityError(f"Completed step inputs changed: {name}")
        if recorded.status != EffectStatus.COMMITTED.value:
            raise FunctionCompatibilityError(f"Step outcome needs reconciliation: {name}")
        if recorded.result is None:
            raise FunctionCompatibilityError(f"Step result is missing: {name}")
        return json.loads(recorded.result)
    context.boundary()
    context.owner.validate()
    context.log.start_step(
        action_id=action_id, identity=identity, input_hash=input_hash,
        code_hash=context.frames[-1]["code_hash"], attempt_id=context.attempt_id,
    )
    result = fn(*args, **kwargs)
    # A crash before this line leaves the step's outcome unknown rather than
    # reissuing a possibly completed external write.
    payload = _result_json(result)
    context.owner.validate()
    current = context.log.step(action_id)
    if current is None or current.status != DISPATCHED:
        raise FunctionCompatibilityError("Step was concurrently reconciled")
    context.log.commit_step(action_id, payload)
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
        log = store.function_log(execution_id)
        for saved in log.admitted() if log is not None else ():
            return saved.get("policy", "keep_original")
    from openprogram.state.config import read_config

    settings = read_config().get("execution", {})
    value = (
        settings.get("code_change_policy", "keep_original")
        if isinstance(settings, dict)
        else "keep_original"
    )
    return value if value in {"keep_original", "use_latest"} else "keep_original"


def retained_function_names(store, execution_id):
    from openprogram.programs._host.runtime import get

    log = store.function_log(execution_id)
    names = set()
    for saved in log.admitted() if log is not None else ():
        name = saved.get("name")
        tool = get(name) if isinstance(name, str) else None
        if tool is not None and getattr(tool, "_resumable", False):
            names.add(name)
    return names


def source_capability(source, name):
    """Inspect explicit Agent method options without importing source."""
    try:
        tree = ast.parse(source)
        for declaration in tree.body:
            if not isinstance(declaration, ast.ClassDef):
                continue
            options = {}
            for assignment in declaration.body:
                if isinstance(assignment, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "method_options" for target in assignment.targets):
                    options = _literal_method_options(assignment.value)
            for method in declaration.body:
                if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                configuration = options.get(method.name, {})
                identity = configuration.get("name", method.name)
                if name not in {method.name, identity, f"{declaration.name}.{method.name}"} and not name.endswith(f".{declaration.name}.{method.name}"):
                    continue
                if configuration.get("resumable") is not True:
                    return {"supported": False, "reason": "No durable step contract"}
                _validate_source(ast.unparse(method))
                return {"supported": True, "boundary": "function.step.after", "state": "json"}
        return {"supported": False, "reason": "No durable Agent method contract"}
    except (FunctionCompatibilityError, ValueError, SyntaxError) as exc:
        return {"supported": False, "reason": str(exc)}


def _literal_method_options(node):
    """Read literal metadata while ignoring callable configuration fields."""
    if not isinstance(node, ast.Dict):
        return {}
    result = {}
    for key, value in zip(node.keys, node.values):
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str) or not isinstance(value, ast.Dict):
            continue
        options = {}
        for option, setting in zip(value.keys, value.values):
            if isinstance(option, ast.Constant) and option.value in {"name", "resumable"} and isinstance(setting, ast.Constant):
                options[option.value] = setting.value
        result[key.value] = options
    return result


def current_function_node_id():
    context = _current.get()
    if context is None or context.frames:
        return None
    return (
        "function_"
        + _digest({"execution": context.execution_id, "call": context.call_key})[:24]
    )
