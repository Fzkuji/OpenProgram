"""``code``: Python that drives the targets, in the isolated GUI interpreter.

The interpreter is ``sandbox/runner.py``: a sandboxed child without
files, network or processes, stopped between calls. It reaches the host only
through ``sandbox/broker.py``, which records every primitive as an effect
of the running execution. Each call registers one broker resource per target
with the call's own approval scope (the declared app; the declared browser;
no upload from the built-in browser) and defines ``cu`` over those
resources, so ``await cu.app.click(...)`` is the action call of the same name.

One interpreter lives for one execution attempt of a conversation, so
variables carry across the code calls of a turn.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
import threading
from typing import Any, Callable

from openprogram.programs._host.execution import ToolReturn

from . import params
from .targets import app as app_target
from .targets import browser as browser_target
from .targets import vm as vm_target

MAX_TIMEOUT = 120

_log = logging.getLogger(__name__)

_GUIDE = (Path(__file__).parent / "guides" / "code.md").read_text(encoding="utf-8")

_PRELUDE = """
class _CuTarget:
    # A target's actions as methods; dir() lists them and help() shows one.

    def __init__(self, handle, docs):
        self._handle = handle
        self._docs = docs

    def __dir__(self):
        return sorted(self._docs)

    def __getattr__(self, action):
        if action.startswith("_"):
            raise AttributeError(action)

        async def call(**arguments):
            return (await ui.call(self._handle, action, arguments))["value"]
        call.__name__ = action
        call.__doc__ = self._docs.get(action)
        return call


class _Cu:
    pass


cu = _Cu()
for _name, (_handle, _docs) in {handles}.items():
    setattr(cu, _name, _CuTarget(_handle, _docs))
del _name, _handle, _docs
"""


def action_docs(guide_text: str, actions) -> dict[str, str]:
    """Each action's line of a target guide, as the docstring of its ``cu`` method."""
    import re
    fragments = [part.strip() for line in guide_text.splitlines() if line.startswith("- ")
                 for part in re.split(r";\s+|\.\s+(?=[a-z_]+[(:;]|[a-z_]+$)", line[2:])]
    docs = {}
    for action in actions:
        for fragment in fragments:
            if re.match(rf"{re.escape(action)}(\(|:|$|\s)", fragment):
                docs[action] = fragment
                break
        else:
            docs[action] = action
    return docs

_lock = threading.Lock()
# conversation -> (execution identity, broker, runner)
_runners: dict[str, tuple[tuple, Any, Any]] = {}


class CodeError(RuntimeError):
    pass


_EXAMPLES = {"app": 'await cu.app.click(app="Numbers", element_index=3)', "browser": "await cu.browser.read()",
             "vm": "await cu.vm.screenshot()"}


def available() -> bool:
    """Whether code can run here: the isolated interpreter needs the macOS sandbox."""
    import sys
    from openprogram.host import sandbox
    return sys.platform == "darwin" and not sandbox.unavailable_reason()


def examples(names) -> str:
    """One call per target as code writes it: the method of the target object."""
    return ", ".join(_EXAMPLES[name] for name in names if name in _EXAMPLES)


def guide() -> str:
    """The code guide, with examples for the targets available now."""
    names = [name for name, module in (("app", app_target), ("browser", browser_target), ("vm", vm_target))
             if module.available()]
    return _GUIDE.replace("{examples}", examples(names) or "none is available")


def _identity() -> tuple[str, str, int, str]:
    from openprogram.runtime.execution.run_control import get_current_execution_id
    from openprogram.runtime.agent.call_state import current_tool_call_id
    from openprogram.runtime.execution.control.shared import default_control_service

    execution_id = get_current_execution_id()
    invocation = current_tool_call_id()
    execution = default_control_service().executions.get_execution(execution_id) if execution_id else None
    if execution is None or not invocation:
        raise CodeError("code runs only inside a conversation turn")
    return execution_id, execution.current_attempt_id, execution.owner_lease.get("generation"), invocation


def _runner(session: str):
    """This agent's interpreter for the running attempt, started on first use.

    Each agent of a conversation (the main turn and every agent it starts)
    has its own: one agent's code never replaces another's interpreter, whose
    variables and running operations would be lost with it.
    """
    from openprogram.programs.tools.computer_use.computer_use_agent.sandbox.broker import GuiBroker, SubprocessGuiSteps
    from openprogram.runtime.execution.worker_calls import run_table_calls
    from openprogram.programs.tools.computer_use.computer_use_agent.sandbox.runner import GuiPythonRunner
    from openprogram.runtime.execution.control.shared import default_control_service

    execution_id, attempt_id, generation, invocation = _identity()
    key = (execution_id, attempt_id, generation)
    slot = f"{session}\n{execution_id}"
    with _lock:
        held = _runners.get(slot)
        if held and held[0] == key and not held[2]._closed:
            held[1].bind_invocation(invocation)
            return held[1], held[2], slot
        if held:
            held[2].close()
        effects = SubprocessGuiSteps() if run_table_calls() is not None else default_control_service().effects
        broker = GuiBroker(effects=effects, execution_id=execution_id,
                           attempt_id=attempt_id, generation=generation, invocation_id=invocation)
        runner = GuiPythonRunner(broker=broker)
        runner.__enter__()
        _runners[slot] = (key, broker, runner)
        return broker, runner, slot


def close(slot: str) -> None:
    """Stop the interpreter ``_runner`` returned this slot for."""
    with _lock:
        held = _runners.pop(slot, None)
    if held:
        held[2].close()


def _adapter(target, action: str, call_args: dict, session: str, operator: str) -> Callable:
    def method(arguments: dict, observation, context) -> ToolReturn:
        context.check()
        name, step, problem = params.check_step(target.NAME, action, dict(arguments))
        if problem:
            return ToolReturn(text=f"Error: {problem}", is_error=True)
        if target is app_target and name == "get_app_state":
            # Code parses what it reads, so a read is the whole tree, not the change since the last one.
            step.setdefault("full", True)
            step.setdefault("include", "tree")
        # Application windows are read per agent (see tool.operator); the browser keeps the conversation's tabs.
        target_args = {**call_args, "_code_text_only": True} if target is app_target else call_args
        result = target.run([(name, step)], target_args, operator if target is app_target else session)
        # Code reads text and data; screenshots come back to a model through action calls.
        return ToolReturn(text=result.text, json_data=result.json_data, is_error=result.is_error)
    return method


def _reads(name: str) -> Callable[[str, dict], bool]:
    """Which of a target's actions only read, so a failed one applied nothing."""
    def reads(action: str, arguments: dict) -> bool:
        if name == "app":
            return action in app_target.READING_ACTIONS
        if name == "browser":
            return action in browser_target.READS
        return action == "screenshot"
    return reads


def _no_scripts() -> bool:
    """Whether page scripts stay out of this turn, by the permission decision's own rule."""
    from openprogram.runtime.agent.turn_request import get_turn_request
    from openprogram.runtime.permissions import identity
    request = get_turn_request()
    return request is not None and identity.unattended(request)


def _unrestricted(call_args: dict) -> bool:
    """Whether this turn is Bypass and the call may run: no grant to stay inside, whatever rule matched."""
    from openprogram.programs import get_agent_tool
    from openprogram.runtime.agent.turn_request import get_turn_request
    from openprogram.runtime.permissions.policy import permission_decision
    request, tool = get_turn_request(), get_agent_tool("computer_use_agent")
    return (request is not None and tool is not None and request.permission_mode == "bypass"
            and permission_decision(tool, request, call_args).outcome == "allow")


def _validator(name: str, call_args: dict, no_scripts: bool = False, unrestricted: bool = False) -> Callable:
    """Keep code inside what its call was granted: the app it names, the browser it names."""
    from openprogram.runtime.permissions import refusal
    declared = app_target.normalized_app(call_args.get("app"))
    browser = browser_target.user_browser(call_args) if browser_target.available() else ""

    def validate(action: str, arguments: dict, observation) -> None:
        if unrestricted:
            if name == "browser" and no_scripts and browser_target.evaluates([(action, arguments)]):
                raise refusal.page_script(action)
            return
        if name == "app":
            named = app_target.normalized_app(arguments.get("app"))
            if action != "list_apps" and named != declared:
                raise refusal.code_scope(action, f"this code call is approved for app {declared or '(none)'}; "
                                                 f"{action} names {named or 'no app'}")
        elif name == "browser":
            named = browser_target.user_browser({**call_args, **arguments})
            if named != browser:
                raise refusal.code_scope(action, f"this code call operates {browser or 'the built-in browser'}; "
                                                 f"{action} names {named or 'the built-in browser'}")
            if not browser and browser_target.approval([(action, arguments)], {}):
                # An upload from the built-in browser is approved alone, which code cannot wait for.
                raise refusal.code_scope(action, f"{action} needs its own approval; call it as an action")
            if no_scripts and browser_target.evaluates([(action, arguments)]):
                raise refusal.page_script(action)
    return validate


def _resources(call_args: dict) -> dict[str, tuple[Any, tuple[str, ...]]]:
    targets: dict[str, tuple[Any, tuple[str, ...]]] = {}
    if browser_target.available():
        targets["browser"] = (browser_target, browser_target.actions())
    if app_target.normalized_app(call_args.get("app")) and app_target.available():
        targets["app"] = (app_target, app_target.ACTIONS)
    if vm_target.available():
        targets["vm"] = (vm_target, vm_target.ACTIONS)
    return targets


def run(code: str, call_args: dict, session: str, *, timeout: float = 60, operator: str = "") -> ToolReturn:
    from openprogram.programs.tools.computer_use.computer_use_agent.sandbox.runner import GuiRunnerError
    from openprogram.host.sandbox import SandboxUnavailable

    timeout = max(1.0, min(float(timeout or 60), MAX_TIMEOUT))
    try:
        broker, runner, slot = _runner(session)
    except (CodeError, SandboxUnavailable, PermissionError) as exc:
        return ToolReturn(text=f"Error: code is unavailable here: {exc}", is_error=True)
    handles, docs = {}, {}
    stopped, result, unreported = None, None, 0
    # Read here, on the thread of the call: the broker checks each step on its own.
    no_scripts = _no_scripts()
    unrestricted = _unrestricted(call_args)
    try:
        for name, (target, actions) in _resources(call_args).items():
            methods = {action: _adapter(target, action, call_args, session, operator or session)
                       for action in actions}
            handles[name] = broker.register(target=name, methods=methods,
                                            validate=_validator(name, call_args, no_scripts, unrestricted), reads=_reads(name))
            docs[name] = action_docs(target.guide(), actions)
        bound = {name: [handle, docs[name]] for name, handle in handles.items()}
        prelude = runner.execute(_PRELUDE.replace("{handles}", json.dumps(bound)), timeout=10)
        if prelude.error:
            raise GuiRunnerError(prelude.error)
        result = runner.execute(code, timeout=timeout)
    except (GuiRunnerError, ValueError, PermissionError) as exc:
        close(slot)
        stopped = exc
    finally:
        for handle in handles.values():
            broker.revoke_resource(handle)
        # Steps this call leaves without a result are reported by the call itself.
        try:
            unreported = broker.settle()
        except Exception:
            _log.warning("could not settle the steps of a code call", exc_info=True)
    if stopped is not None:
        return ToolReturn(text=_stopped_text(stopped, timeout, unreported), is_error=True)
    parts = []
    if result.stdout:
        parts.append(result.stdout.rstrip("\n"))
    if result.stderr:
        parts.append("stderr:\n" + result.stderr.rstrip("\n"))
    if result.truncated:
        parts.append("(printed text past 256 KB was dropped; print less)")
    if result.error:
        parts.append(f"Error: {result.error}")
    if unreported:
        parts.append(_UNREPORTED)
    return ToolReturn(text="\n".join(parts) or "Done (nothing printed).", is_error=result.error is not None)


_UNREPORTED = ("A step failed after it started, so it may or may not have been applied: read the current state "
               "before repeating it.")
_REASONS = {
    "execution deadline exceeded": "it ran longer than its {timeout:g} seconds (timeout takes up to 120)",
    "GUI operation limit exceeded": "it made more than 100 actions in one call",
    "output transport limit exceeded": "it printed more than 4 MB",
    "execution cancelled": "it was cancelled",
}


def _stopped_text(exc: Exception, timeout: float, unreported: int) -> str:
    """What the model reads when the interpreter had to stop: why, what is unknown, what is lost."""
    known = _REASONS.get(str(exc))
    reason = known.format(timeout=timeout) if known else str(exc)
    lines = [f"Error: the code was stopped: {reason}. The steps it had already made stay made."]
    if unreported:
        lines.append("The step that was running may or may not have been applied: read the current state before "
                     "repeating it.")
    lines.append("The interpreter starts again on the next code call, without the variables of this one.")
    text = " ".join(lines)
    partial = getattr(exc, "stdout", "").strip()
    if partial:
        text += "\n\nOutput collected before stopping:\n" + partial
    return text


__all__ = ["MAX_TIMEOUT", "close", "examples", "guide", "run"]
