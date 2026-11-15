"""Task-local call state, cancellation hooks, schemas, and trace helpers."""

from __future__ import annotations

import functools
import inspect
import logging
import os
import time
from contextlib import nullcontext
from contextvars import ContextVar
from typing import Callable, Optional

_log = logging.getLogger(__name__)

# The cancellation primitive lives below the runtime (the host collects
# commands with it too). Agent method code and installed Program packages
# keep importing these two names from the call state they run in.
from openprogram.host.cancellation import CancelledError, check_cancelled  # noqa: E402,F401 -- public call-state names

# Runtime shared across the call chain via ContextVar.
# Entry-point functions auto-create a runtime; child functions inherit it.
_current_runtime: ContextVar = ContextVar('_current_runtime', default=None)

# ``_call_id`` (the running Agent method's DAG node id) and
# ``_forced_predecessor`` (a retry's explicit predecessor) live in
# ``openprogram.runtime.context.scope`` so Context can read them without
# importing the call core; these are the same ContextVar objects.
from openprogram.runtime.context.scope import _call_id, _forced_predecessor

# Pre-created top-level code node id for a run whose placeholder card the
# PARENT dispatch path already appended (before spawning the child), so the
# UI's head moves and the pending card exists within milliseconds of the WS
# action instead of waiting ~1s for the child's fresh-interpreter import.
# When set, the wrapper REUSES this id as its pending_id and the (idempotent)
# append becomes a no-op — the node already exists, so head/predecessor are
# not re-stamped, and the exit update keys on this same id. Unset → the
# wrapper mints its own uuid and appends as before.
_forced_node_id: ContextVar[Optional[str]] = ContextVar(
    '_forced_node_id', default=None,
)
_render_range_override: ContextVar[Optional[dict]] = ContextVar(
    '_render_range_override', default=None,
)

# Self-recursion safety net. The primary guard against an agentic
# function re-entering itself (paper_agent calling paper_agent →
# unbounded nesting) is the situational prompt injected in
# ``runtime._render_history_messages`` — the model is told it is
# already running inside the function and should not call it. This
# depth counter is only a backstop: if a runaway model ignores the
# guidance and re-enters the SAME function past the limit, we abort
# instead of burning tokens forever. Per-function-name depth, so
# distinct functions (paper_agent → agent → …) never collide.
_MAX_AGENTIC_RECURSION_DEPTH = 5
_recursion_depth: ContextVar[Optional[dict]] = ContextVar(
    '_recursion_depth', default=None,
)

# Parameter names that receive the runtime injection
_RUNTIME_PARAMS = {"runtime", "exec_runtime", "review_runtime"}


# Pre-invocation hooks — called at the top of every Agent method wrapper
# BEFORE the user function runs. Any hook can raise (typically CancelledError)
# to abort the call; the exception propagates to the caller unchanged.
_pre_invocation_hooks: list[Callable] = []


def add_pre_invocation_hook(hook: Callable) -> None:
    """Register a hook called at the top of every Agent method invocation.

    The hook takes no arguments. It may raise to abort the call (e.g. a
    webui stop button raising CancelledError).
    """
    if hook not in _pre_invocation_hooks:
        _pre_invocation_hooks.append(hook)


def remove_pre_invocation_hook(hook: Callable) -> None:
    """Unregister a previously added pre-invocation hook."""
    try:
        _pre_invocation_hooks.remove(hook)
    except ValueError:
        pass


def _run_pre_invocation_hooks() -> None:
    """Run all registered hooks. Exceptions (including CancelledError) propagate."""
    for hook in list(_pre_invocation_hooks):
        hook()


# Host integration point — the seam between the core and whatever is driving
# it. The webui registers the real provider at import (see
# conversation/run_control/); embedded use leaves the default, which is what
# keeps the core importable without the webui (an optional extra) installed.
# The cancellation checkpoint, the other such seam, lives with the
# cancellation primitive in ``openprogram.host.cancellation``.
#
# It is set rather than appended: exactly one host owns session routing at a
# time, and a second registration replacing the first is the intended
# behaviour.

def _no_session_id() -> str:
    """Default session-id provider: no frontend attached, so no route."""
    return ""


_session_id_provider: Callable[[], str] = _no_session_id


def set_session_id_provider(hook: "Callable[[], str] | None") -> None:
    """Install the provider for the host's current session id.

    Used to route questions back to a frontend; returning "" means nobody is
    there to answer, which is what ``Runtime.can_ask()`` reports. Passing None
    restores the no-frontend default.
    """
    global _session_id_provider
    _session_id_provider = hook or _no_session_id


_TERMINAL_EXIT_STATUSES = frozenset({
    "completed", "failed", "interrupted", "cancelled", "error",
})


def current_session_id() -> str:
    """The host's current session id, or "" when running headless."""
    try:
        return _session_id_provider() or ""
    except Exception:
        return ""


def current_call_id() -> str:
    """DAG node id of the Agent method currently executing, or ""."""
    try:
        return _call_id.get() or ""
    except LookupError:
        return ""


def current_tool_call_id() -> str:
    """Provider tool-call id currently executing in this context, if any."""
    try:
        from openprogram.programs._host.runtime import current_tool_call_id as _current

        return _current() or ""
    except Exception:
        return ""


def current_tool_call_occurrence_id() -> str:
    """Qualified provider tool-call occurrence currently executing."""
    try:
        from openprogram.programs._host.runtime import current_tool_call_occurrence_id as _current

        return _current() or ""
    except Exception:
        return ""


def tool_call_identity_consumed() -> bool:
    """Whether the current provider identity was consumed by a wrapper."""
    try:
        from openprogram.programs._host.runtime import tool_call_identity_consumed as _consumed

        return bool(_consumed())
    except Exception:
        return False


def tool_node_id(parent_id: str, tool_call_identity: str) -> str:
    """Return the stable DAG id for a qualified tool occurrence."""
    import hashlib

    digest = hashlib.sha256(
        f"{parent_id}\0{tool_call_identity}".encode("utf-8")
    ).hexdigest()[:20]
    return f"tool_{digest}"


def create_pending_call_node(
    *,
    pending_id: str,
    function_name: str,
    arguments: dict,
    expose: str,
    render_range=None,
    started_at=None,
    docstring: str = "",
    caller: Optional[str] = None,
    forced_predecessor: Optional[str] = None,
    retry_of: Optional[str] = None,
    store=None,
):
    """Build the placeholder code ``Call`` for an Agent method run.

    The node has ``output=None`` (the function hasn't returned yet) and
    ``metadata.status='running'``; the matching
    :func:`_update_function_call_exit` fills these in at exit.

    Shared by two writers so both stamp an IDENTICAL node under the same id:

      * the wrapper's :func:`_append_function_call_entry` (in-process /
        child), which reads ``caller`` / ``forced_predecessor`` off the
        ContextVars, and
      * the PARENT dispatch path (``run_program_call``), which
        pre-creates this card before spawning the child so head moves and
        the pending card lands on disk within milliseconds.

    ``caller`` / ``forced_predecessor`` default to the ambient ContextVars
    (``_call_id`` / ``_forced_predecessor``) so the wrapper needs no extra
    args; the parent path passes them explicitly. ``store`` (a
    SessionNodeWriter) is only needed to resolve the current head when this is
    a top-level call with no forced predecessor — pass the parent's shim, or
    let it default to the ambient ``_store``.

    Returns the constructed ``Call`` (unappended), or ``None`` when
    ``expose='hidden'``.
    """
    if expose == "hidden":
        return None

    from openprogram.state.store.session.nodes import Call, ROLE_CODE

    if store is None:
        from openprogram.state.store import _store
        store = _store.get()

    if caller is None:
        caller = _call_id.get() or ""
    if forced_predecessor is None:
        forced_predecessor = _forced_predecessor.get()
    if caller and store is not None:
        session_id = getattr(store, "session_id", None)
        if session_id:
            from openprogram.runtime.execution.run_control import admit_child_execution
            admit_child_execution(session_id, caller, store=getattr(store, "store", None))

    meta: dict = {
        "expose": expose,
        "status": "running",
    }
    from .call_scope import execution_task_id
    meta['task_id'] = execution_task_id()
    active_tool_call_id = current_tool_call_id()
    active_occurrence_id = current_tool_call_occurrence_id()
    if active_occurrence_id:
        # Lets recovery and stream projections identify the same node even
        # when the wrapper is entered after the provider event was emitted.
        meta["tool_call_id"] = active_tool_call_id
        meta["tool_call_occurrence_id"] = active_occurrence_id
    if retry_of:
        meta["retry_of"] = retry_of
    if render_range:
        meta["render_range"] = dict(render_range)
    # The function's docstring travels on the node so it renders into
    # the context of any LLM call that reads this code Call — restoring
    # the tree-Context behaviour where a function's documentation was
    # visible to the model running inside it.
    if docstring:
        meta["doc"] = docstring
    # Top-level manual function call (fn-form / Functions panel / retry) —
    # no enclosing Agent method on the stack, so ``caller`` is empty.
    # Without a conv predecessor the code node has no place in the
    # conversation chain and the DAG viewport renders it as a detached
    # root. Stamp the session's current head as ``predecessor``
    # (the conv-chain edge) so it attaches under the active branch's tip.
    if not caller:
        # A retry forks off a SPECIFIC node (the original run's
        # predecessor); a fresh run chains off the current head. Both land
        # as ``predecessor`` with an empty caller, so every
        # top-level run uses ONE edge type (predecessor) — internal
        # sub-calls remain the only nodes with a code-node caller.
        try:
            if forced_predecessor is not None:
                # "ROOT" is stamped EXPLICITLY (chat first turns carry
                # predecessor="ROOT" the same way): a retry of a root-level
                # run must record its fork point, or the branch walk's
                # legacy seq-stitching treats the sibling version as the
                # previous turn and renders both runs at once.
                if forced_predecessor:
                    meta["predecessor"] = forced_predecessor
            elif store is not None:
                pair = store.store._open(store.session_id)
                if pair is not None:
                    _session_log, _idx = pair
                    head = _idx.head_id
                    # Fresh session (no head yet) → anchor at ROOT
                    # explicitly, matching the chat convention, so the
                    # first run and any retry of it group as fork
                    # siblings instead of seq-stitched serial turns.
                    meta["predecessor"] = head or "ROOT"
        except Exception:
            # Without a predecessor the run renders as a detached root, but the
            # call itself still runs.
            _log.warning("predecessor not stamped for call %s", pending_id, exc_info=True)
    return Call(
        id=pending_id,
        created_at=started_at or time.time(),
        role=ROLE_CODE,
        name=function_name,
        predecessor=meta.pop("predecessor", None) or None,
        input=_sanitize_function_args(arguments or {}),
        output=None,
        # ``caller`` is the logical caller — the Agent method whose
        # body is the one invoking us. Empty string when this is a
        # top-level call (no enclosing Agent method on the stack).
        caller=caller,
        metadata=meta,
    )


def _append_function_call_entry(
    *,
    pending_id: str,
    function_name: str,
    arguments: dict,
    expose: str,
    render_range,
    started_at,
    docstring: str = "",
    structural: bool = False,
) -> None:
    """Append a placeholder code Call at Agent method entry.

    ``render_range`` is stamped into metadata so ``render_context``
    (which reads frame settings off the in-DAG code Call) can apply
    callers / subcalls limits without needing a separate in-memory frame.

    No-op when:
      - no ``_store`` is installed (standalone scripts / tests)
      - ``expose='hidden'`` (caller wants no trace in the DAG)

    When the PARENT dispatch path already pre-created this node (its id is
    ``pending_id``, threaded in via ``_forced_node_id``), the underlying
    ``store.append`` is idempotent on id — this call becomes a no-op and
    does not re-stamp head / predecessor.
    """
    if expose == "hidden":
        return

    from openprogram.state.store import _store
    store = _store.get()
    if store is None:
        return

    parent_id = _call_id.get() or ""
    admission = nullcontext()
    session_id = getattr(store, "session_id", None)
    if parent_id and session_id:
        from openprogram.runtime.execution.run_control import child_execution_admission
        admission = child_execution_admission(session_id, parent_id, store=getattr(store, "store", None))

    with admission:
        node = create_pending_call_node(
            pending_id=pending_id,
            function_name=function_name,
            arguments=arguments,
            expose=expose,
            render_range=render_range,
            started_at=started_at,
            docstring=docstring,
            store=store,
        )
        if node is None:
            return
        if structural:
            node.metadata["structural"] = True
        try:
            store.append(node)
        except Exception as exc:
            # DAG persistence failure must never break the user's function call.
            _log.warning(
                "DAG persistence failed phase=entry node_id=%s error_type=%s",
                pending_id,
                type(exc).__name__,
                exc_info=True,
            )


def _update_function_call_exit(
    *,
    pending_id: str,
    output,
    error,
    status: str,
    expose: str,
    started_at,
    ended_at,
) -> None:
    """Fill in output + status on the placeholder Call written at entry.

    Mirror of :func:`_append_function_call_entry` — same no-op rules.
    """
    if expose == "hidden":
        return

    from openprogram.state.store import _store
    store = _store.get()
    if store is None:
        return

    try:
        node = store.load().nodes.get(pending_id)
        current = (getattr(node, "metadata", None) or {}).get("status")
        if current in _TERMINAL_EXIT_STATUSES:
            return
    except Exception:
        # Unreadable status: fall through and write the exit as if non-terminal.
        _log.debug("could not read status of node %s", pending_id, exc_info=True)

    # ToolReturn and AgentToolResult encode failure without raising. Respect
    # that explicit protocol in both sync and async function DAG records.
    from openprogram.models.providers.types import AgentToolResult
    from openprogram.programs._host.execution import ToolReturn
    if status == "completed" and isinstance(output, (ToolReturn, AgentToolResult)) and output.is_error:
        status = "error"
        if isinstance(output, ToolReturn):
            error = output.text or str(output.json_data or "Tool returned an error")
        else:
            error = "".join(getattr(item, "text", "") for item in output.content) or "Tool returned an error"

    duration = None
    if started_at is not None and ended_at is not None:
        duration = float(ended_at) - float(started_at)

    if status == "error":
        result_payload = {"error": error or "unknown"}
    else:
        result_payload = output

    try:
        store.update(
            pending_id,
            output=result_payload,
            metadata={
                "duration_seconds": duration,
            },
        )
        from openprogram.runtime.execution.run_control import mark_execution_terminal
        bound_store = getattr(store, "store", None)
        if status == "error":
            mark_execution_terminal(pending_id, "error", store=bound_store)
        elif status == "cancelled":
            mark_execution_terminal(pending_id, "cancelled", store=bound_store)
        elif status in {"failed", "interrupted"}:
            mark_execution_terminal(pending_id, status, store=bound_store)
        else:
            mark_execution_terminal(pending_id, "completed", store=bound_store)
    except Exception as exc:
        _log.warning(
            "DAG persistence failed phase=exit node_id=%s error_type=%s",
            pending_id,
            type(exc).__name__,
            exc_info=True,
        )


def _sanitize_function_args(params: dict) -> dict:
    """Trim non-JSON-friendly param values so they fit a data_json blob.

    - Runtime injections become a type tag (we don't want to serialise
      a whole Runtime object into SQLite on every call).
    - Anything that JSON-doesn't-like is repr'd and truncated to 500 chars.
    """
    out: dict = {}
    for k, v in params.items():
        if k in ("self", "cls"):
            out[k] = f"<{type(v).__module__}.{type(v).__qualname__}>"
            continue
        if k in _RUNTIME_PARAMS:
            out[k] = f"<{type(v).__name__}>"
            continue
        try:
            import json as _json
            _json.dumps(v, default=str)
            out[k] = v
        except (TypeError, ValueError):
            out[k] = repr(v)[:500]
    return out




def _inject_runtime(sig, args, kwargs):
    """Auto-inject runtime into function call if needed.

    If the function has a runtime parameter and it's None:
      - If a runtime exists in the call chain (ContextVar), use it.
      - Otherwise, create a new one (this function is the entry point).

    Returns:
        (args, kwargs, runtime_token, owned_runtime)
        - runtime_token: ContextVar token to reset later (or None)
        - owned_runtime: the Runtime this call created (need to close it), else None
    """
    # bind_partial, NOT bind: a runtime parameter with no default (e.g.
    # `def f(pdf_path, runtime, …)`) is REQUIRED, so a full `sig.bind`
    # raises "missing a required argument: 'runtime'" here — before we
    # ever get a chance to inject it. bind_partial tolerates the gap so
    # the injection below (the positional-missing branch) can fill it;
    # the caller's own `sig.bind` (in the wrapper, after injection) still
    # enforces that every other required argument was supplied.
    bound = sig.bind_partial(*args, **kwargs)
    bound.apply_defaults()

    runtime_token = None
    owned_runtime = None

    # Which runtime params this function declares, and which still need a
    # value (either bound to None via default, or missing entirely). A
    # function can declare MORE THAN ONE (e.g. a `runtime`
    # + `review_runtime`) — every one of them must be filled, not just the
    # first. The old code `break`ed after one, so with _RUNTIME_PARAMS
    # being an unordered set, which param got filled was nondeterministic
    # → such a function intermittently saw `runtime=None` and raised.
    declared = [p for p in sig.parameters if p in _RUNTIME_PARAMS]
    needs = [
        p for p in declared
        if (p in bound.arguments and bound.arguments[p] is None)
        or (p not in bound.arguments)
    ]

    # Lazily resolve ONE runtime (from the call chain, else create one)
    # and share it across all the params that need it.
    def _resolve_rt():
        nonlocal runtime_token, owned_runtime
        rt = _current_runtime.get(None)
        if rt is None:
            from openprogram.runtime.agent.runtime.factory import create_runtime
            try:
                rt = create_runtime()
            except Exception:
                # No usable LLM provider. Not just RuntimeError: a provider
                # can resolve (CLI binary on PATH) yet fail construction with
                # AuthConfigError etc. when its credentials are absent — the
                # narrow catch made tests' pass/fail depend on the HOST's
                # login state. In production we re-raise the helpful "set up
                # a provider" guidance. Under pytest with no credentials
                # (CI), fall back to a placeholder runtime whose .exec raises
                # only IF the body actually calls the model — so the many
                # tests whose bodies never touch the LLM (cache / timeout
                # wrappers, dispatcher plumbing) stop crashing on a provider
                # lookup they don't need. See tests/conftest.py.
                import os as _os
                if not _os.environ.get("PYTEST_CURRENT_TEST"):
                    raise
                from openprogram.runtime.agent.runtime import Runtime

                def _no_provider_call(content, model="test", response_format=None):
                    raise RuntimeError(
                        "No LLM provider configured (test placeholder runtime). "
                        "This test body called the model without providing a "
                        "runtime; pass one explicitly or mock the LLM."
                    )

                rt = Runtime(call=_no_provider_call, model="test")
            runtime_token = _current_runtime.set(rt)
            owned_runtime = rt
        return rt

    if needs:
        rt = _resolve_rt()
        for p in needs:
            bound.arguments[p] = rt

    # A runtime was passed in explicitly (not None) and nothing is in the
    # call chain yet → publish it so nested calls inherit the same one.
    if runtime_token is None:
        for p in declared:
            if bound.arguments.get(p) is not None:
                if _current_runtime.get(None) is None:
                    runtime_token = _current_runtime.set(bound.arguments[p])
                break

    return bound.args, bound.kwargs, runtime_token, owned_runtime


def _apply_system(system, bound_args):
    """Bind function instructions in this task without mutating a Runtime."""
    if system is None:
        return None
    from .runtime.shared import _current_instructions
    return _current_instructions.set(system)


def _restore_system(token):
    if token is not None:
        from .runtime.shared import _current_instructions
        _current_instructions.reset(token)


def _close_owned_runtime(owned_runtime) -> None:
    if owned_runtime is None or not hasattr(owned_runtime, "close"):
        return
    import sys
    active_error = sys.exception()
    try:
        owned_runtime.close()
    except Exception as exc:
        if active_error is None:
            raise
        _log.warning(
            "owned runtime close failed error_type=%s",
            type(exc).__name__,
            exc_info=True,
        )


def _build_agent_tool_spec(fn, input_meta):
    from .agent_method import _build_agent_tool_spec as build
    return build(fn, input_meta)


def traced(fn):
    """Lightweight decorator that records function execution into the DAG.

    Record explicit arguments, results, and terminal state in the existing DAG.
    Recording does not create a Runtime or a store.

    Usage:
        @traced
        def search_papers(query):
            ...
    """
    if inspect.isgeneratorfunction(fn) or inspect.isasyncgenfunction(fn):
        return fn
    from .call_scope import CallScope
    sig = inspect.signature(fn)

    def scope(args, kwargs):
        try:
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            arguments = {k: v for k, v in bound.arguments.items()
                         if k not in ("self", "cls", "runtime", "callback")}
        except TypeError:
            arguments = {}
        return CallScope(fn.__name__, docstring=inspect.getdoc(fn) or '',
                         arguments=arguments, capture_io=True, expose="io")

    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            with scope(args, kwargs) as call:
                call.output = await fn(*args, **kwargs)
                return call.output
    else:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with scope(args, kwargs) as call:
                call.output = fn(*args, **kwargs)
                return call.output
    wrapper._is_traced = True
    return wrapper


def _is_agent_method(obj) -> bool:
    """Return whether a callable is a configured Agent method."""
    return getattr(obj, '_is_agent_method', False)


def _calls_agent_methods(func, mod) -> bool:
    """Check if a function calls any Agent method.

    Inspects the function's bytecode references (co_names) and checks
    whether any referenced name in the module is an Agent method.
    This identifies orchestrator functions that should be traced.
    """
    # Unwrap decorated functions to get the original code
    original = getattr(func, '__wrapped__', func)
    try:
        code_names = set(original.__code__.co_names)
    except AttributeError:
        return False
    for ref_name in code_names:
        ref_obj = getattr(mod, ref_name, None)
        if ref_obj is not None and _is_agent_method(ref_obj):
            return True
    return False


def auto_trace_module(mod, exclude=None, trace_pkg=None):
    """Auto-apply @traced to orchestrator functions in a module.

    Only traces functions that call Agent method (orchestrators).
    Leaf functions (pure utilities like compute_iou) are skipped.

    Skips functions that are already Agent method or @traced,
    private functions (starting with _), and third-party imports.

    Args:
        mod: The module object to patch.
        exclude: Optional set of function names to skip.
        trace_pkg: Package directory path. Functions from files within this
                   directory are considered even if imported. If None, uses
                   the directory of mod.__file__.
    """
    exclude = exclude or set()
    mod_file = getattr(mod, '__file__', None)
    if not mod_file:
        return
    if trace_pkg is None:
        trace_pkg = os.path.dirname(os.path.abspath(mod_file))

    for name in list(dir(mod)):
        if name.startswith('_') or name in exclude:
            continue
        obj = getattr(mod, name)
        if not callable(obj) or not inspect.isfunction(obj):
            continue
        # Skip already decorated
        if getattr(obj, '_is_agent_method', False) or getattr(obj, '_is_traced', False):
            continue
        # Only trace functions defined within the package
        try:
            fn_file = os.path.abspath(inspect.getfile(obj))
        except (TypeError, OSError):
            continue
        if not fn_file.startswith(trace_pkg):
            continue
        # Only trace orchestrators (functions that call Agent method)
        if _calls_agent_methods(obj, mod):
            setattr(mod, name, traced(obj))


def auto_trace_package(pkg_dir, pkg_name=None):
    """Recursively auto-trace all .py files in a package directory.

    Walks the directory tree, imports each module, and applies @traced
    to all user-defined functions. This ensures that lazy imports
    within the package get traced versions.

    Args:
        pkg_dir: Absolute path to the package root directory.
        pkg_name: Dotted package name prefix (e.g. "some_harness").
                  If None, uses the directory basename.
    """
    import importlib.util as _imputil
    import sys as _sys

    pkg_dir = os.path.abspath(pkg_dir)
    if pkg_name is None:
        pkg_name = os.path.basename(pkg_dir)

    for root, dirs, files in os.walk(pkg_dir):
        dirs[:] = [d for d in dirs if not d.startswith(("_", ".", "test"))]
        for f in sorted(files):
            if not f.endswith(".py") or f.startswith("_"):
                continue
            filepath = os.path.join(root, f)
            # Build module name relative to pkg_dir
            rel = os.path.relpath(filepath, os.path.dirname(pkg_dir))
            mod_name = rel.replace(os.sep, ".")[:-3]  # strip .py
            if mod_name in _sys.modules:
                mod = _sys.modules[mod_name]
            else:
                try:
                    spec = _imputil.spec_from_file_location(mod_name, filepath)
                    if spec is None:
                        continue
                    mod = _imputil.module_from_spec(spec)
                    _sys.modules[mod_name] = mod
                    spec.loader.exec_module(mod)
                except Exception:
                    continue
            auto_trace_module(mod, trace_pkg=pkg_dir)
