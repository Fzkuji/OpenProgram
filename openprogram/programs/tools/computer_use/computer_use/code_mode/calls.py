"""``code``: Python that drives the call's target, in the isolated GUI interpreter.

The interpreter is ``sandbox/runner.py``: a sandboxed child without
files, network or processes, stopped between calls. It reaches the host only
through ``sandbox/broker.py``, which records every primitive as an effect
of the running execution.

``cu`` is the call's target and nothing else: each call registers one broker
resource, for its own provider, and defines ``cu`` over it, so
``await cu.click(element=3)`` is the action call of the same name on the
target the call names. A step may leave out the subject the call names (the
application, the tab) and may not name another. Code is approved exactly as
an action call with that target and subject is, and every step takes its own
locks. Work on two targets takes two calls.

One interpreter lives for one execution attempt of a conversation, so
variables carry across the code calls of a turn, whatever their targets.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
import threading
from typing import Any, Callable

from openprogram.runtime.agent.tool_execution import ToolReturn

from .. import errors
from ..actions import vocabulary
from ..contract import Approval, Call, Provider, refused
from ..routing.dispatch import run_steps

MAX_TIMEOUT = 120

_log = logging.getLogger(__name__)

_GUIDE = (Path(__file__).parent.parent / "guides" / "code.md").read_text(encoding="utf-8")

_PRELUDE = """
class _Cu:
    # The call's target: its actions as methods. dir() lists them and help() shows one.

    def __init__(self, handle, target, docs, names, own, others):
        self._handle, self._target, self._docs = handle, target, docs
        self._names, self._own, self._others = set(names), set(own), dict(others)

    def __dir__(self):
        return sorted(self._docs)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self._own:
            raise AttributeError(f"cu is already this call's target, {self._target}: write cu.<action>(...), "
                                 f"not cu.{name}.<action>(...).")
        if name in self._others:
            calls = " or ".join(f'computer_use({target}, code=...)' for target in self._others[name])
            raise AttributeError(f"cu is this call's target, {self._target}. {name} takes a call of its own: "
                                 f"{calls}.")
        if name not in self._names:
            raise AttributeError(f"UNKNOWN_ACTION: {self._target} has no action {name!r}. "
                                 f"Actions: {', '.join(sorted(self._docs))}.")
        handle = self._handle

        async def call(**arguments):
            return (await ui.call(handle, name, arguments))["value"]
        call.__name__ = name
        call.__doc__ = self._docs.get(name)
        return call


cu = _Cu(*{binding})
"""

_lock = threading.Lock()
# conversation -> (execution identity, broker, runner)
_runners: dict[str, tuple[tuple, Any, Any]] = {}


class CodeError(RuntimeError):
    pass


def available() -> bool:
    """Whether code can run here: the isolated interpreter needs the macOS sandbox."""
    import sys
    from openprogram.host import sandbox
    return sys.platform == "darwin" and not sandbox.unavailable_reason()


def guide() -> str:
    """The code guide."""
    return _GUIDE


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
    from openprogram.programs.tools.computer_use.computer_use.code_mode.sandbox.broker import (GuiBroker,
        SubprocessGuiSteps)
    from openprogram.runtime.execution.worker_calls import run_table_calls
    from openprogram.programs.tools.computer_use.computer_use.code_mode.sandbox.runner import GuiPythonRunner
    from openprogram.runtime.execution.control.shared import default_control_service

    execution_id, attempt_id, generation, invocation = _identity()
    key = (execution_id, attempt_id, generation)
    slot = run_slot(session, execution_id)
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


def run_slot(session: str, execution: str) -> str:
    """The slot of one run's interpreter: each run of a conversation has its own."""
    return f"{session}\n{execution}"


def close_conversation(session: str) -> None:
    """Stop every interpreter of a conversation that is going away."""
    with _lock:
        slots = [slot for slot in _runners if slot.split("\n", 1)[0] == session]
    for slot in slots:
        close(slot)


def _split(surface: vocabulary.Surface, subject: dict, step: dict) -> tuple[dict, dict]:
    """A step's own arguments, and the subject it acts on: the call's, with what the step itself names.

    A step may leave out what the call names, or repeat it, but not name
    another: the call was approved for its own subject only.
    """
    shared = surface.table.shared
    own = {key: step[key] for key in step if key in shared}
    for key, value in own.items():
        if key in subject and str(value).strip().lower() != str(subject[key]).strip().lower():
            raise errors.BadArgument(f"this code call operates {key}={subject[key]!r}; a step cannot name "
                                     f"{key}={value!r}. Work on it takes a call of its own.")
    return {key: value for key, value in step.items() if key not in shared}, {**subject, **own}


def _adapter(provider: Provider, surface: vocabulary.Surface, method: str, subject: dict, session: str,
             operator: str) -> Callable:
    """One method of ``cu``: the action call of the same name, as one step with its own locks."""
    def run(arguments: dict, observation, context) -> ToolReturn:
        try:
            action, step = vocabulary.check_step(surface, method, dict(arguments))
            step, step_subject = _split(surface, subject, step)
        except errors.CallError as exc:
            return refused(exc)
        if action == "get_state" and "tree" in provider.capabilities:
            # Code parses what it reads, so a read is the whole tree, not the change since the last one.
            step.setdefault("full", True)
            step.setdefault("include", "tree")
        call = Call(conversation=session, agent=operator, deadline=context.deadline, check_cancelled=context.check,
                    images=False)
        result = run_steps(provider, step_subject, [(action, step)], call)
        # Code reads text and data; screenshots come back to a model through action calls.
        return ToolReturn(text=result.text, json_data=result.json_data, is_error=result.is_error)
    return run


def _no_scripts() -> bool:
    """Whether page scripts stay out of this turn, by the permission decision's own rule: a turn nobody
    attends, or one that works from text the owner did not write."""
    from openprogram.runtime.agent.turn_request import get_turn_request
    from openprogram.runtime.permissions import identity
    request = get_turn_request()
    return request is not None and (identity.unattended(request) or identity.outside_text(request))


# How a step of code is judged in Bypass: (action, its subject, its arguments) -> the decision's outcome.
Judge = Callable[[str, dict, dict], str]


def _computer_use_tool():
    """The registered ``computer_use`` tool, which the permission decision reads."""
    from openprogram.programs._host import runtime as registry
    return registry.get("computer_use")


def _unrestricted(tool_args: dict) -> bool:
    """Whether this turn is Bypass and the call may run: no grant to stay inside, whatever rule matched.

    ``tool_args`` is the whole call (device, type, app, args, code), as the permission decision reads it.
    """
    from openprogram.runtime.agent.turn_request import get_turn_request
    from openprogram.runtime.permissions.policy import permission_decision
    request, tool = get_turn_request(), _computer_use_tool()
    return (request is not None and tool is not None and request.permission_mode == "bypass"
            and permission_decision(tool, request, tool_args).outcome == "allow")


def _as_action_calls(provider: Provider) -> Judge:
    """The decision each step of an unrestricted call gets as an action call of its own, so the owner's deny
    and ask rules still hold for the subject a step names. Read on the call's thread."""
    from openprogram.runtime.agent.turn_request import get_turn_request
    from openprogram.runtime.permissions.policy import permission_decision
    request, tool = get_turn_request(), _computer_use_tool()

    def judge(action: str, step_subject: dict, step: dict) -> str:
        return permission_decision(tool, request, {**provider.addressed(), "action": action,
                                                   "args": {**step_subject, **step}}).outcome
    return judge


def _validator(provider: Provider, surface: vocabulary.Surface, subject: dict, granted: Approval | None,
               no_scripts: bool = False, judge: Judge | None = None) -> Callable:
    """Keep code inside what its call was granted.

    ``granted`` is what the call itself was approved for. A step that would
    ask the user something else (another application, an upload that is
    approved alone) is refused: code cannot wait for an answer. The step is
    judged in our names, after the names of other tools are rewritten. In
    Bypass (``judge``) there is no grant to stay inside, and a step runs
    exactly when the same step as an action call would be allowed.
    """
    from openprogram.runtime.permissions import refusal

    def validate(method: str, arguments: dict, observation) -> None:
        try:
            action, step = vocabulary.check_step(surface, method, dict(arguments))
            step, step_subject = _split(surface, subject, step)
        except errors.CallError:
            # Nothing runs: the adapter answers with what is wrong.
            return
        if judge is not None:
            if judge(action, step_subject, step) != "allow":
                raise refusal.code_scope(action, f"{action} is not allowed here as an action call; the owner's "
                                                 "rules refuse it or ask about it")
        else:
            needed = provider.approval(step_subject, [(action, step)])
            if needed is not None and needed.shown:
                held = granted.rule if granted is not None and granted.shown else None
                if needed.rule is None:
                    raise refusal.code_scope(action, f"{action} needs its own approval; call it as an action")
                if held is None:
                    raise refusal.code_scope(action, f"this code call was not approved for {needed.rule}; name it "
                                                     "in the call's args, or call it as an action")
                if needed.rule != held:
                    raise refusal.code_scope(action, f"this code call is approved for {held}; "
                                                     f"{action} asks for {needed.rule}")
        if no_scripts and provider.runs_page_script([(action, step)]):
            raise refusal.page_script(action)
    return validate


def _reads(family: str) -> Callable[[str, dict], bool]:
    """Which methods only read, so a failed one applied nothing."""
    def reads(method: str, arguments: dict) -> bool:
        return vocabulary.canonical(family, method)[0] in vocabulary.READS
    return reads


def _docs(surface: vocabulary.Surface, guide_text: str) -> dict[str, str]:
    """Each action's line of the family guide, else its signature, as the docstring of its ``cu`` method."""
    import re
    fragments = [part.strip() for line in guide_text.splitlines() if line.startswith("- ")
                 for part in re.split(r";\s+|\.\s+(?=[a-z_]+[(:;]|[a-z_]+$)", line[2:])]
    docs = {}
    for action in surface.actions():
        docs[action] = next((fragment for fragment in fragments
                             if re.match(rf"{re.escape(action)}(\(|:|$|\s)", fragment)), surface.signature(action))
    return docs


# Other names a model writes after ``cu.`` for a type.
_ALIASES = {"window": ("app",), "browser": ("built_in_browser",)}


def _others(provider: Provider, session: str) -> dict[str, list[str]]:
    """Names a model might write after ``cu.`` for another type, each with how a call names the targets of that
    type listed now: ``screen`` may be ``device="lab", type="screen"`` only, never this computer's screen when
    that is not listed."""
    from ..routing import registry
    own = {provider.family, *_ALIASES.get(provider.family, ())}
    found: dict[str, list[str]] = {}
    for other in registry.providers(session).values():
        if other.family in own:
            # Another browser of the same type is named by its app: cu.chrome on OpenProgram's own browser.
            if other.target != provider.target and other.app and other.app not in own:
                found.setdefault(other.app, []).append(other.address())
            continue
        for name in (other.family, *_ALIASES.get(other.family, ())):
            found.setdefault(name, []).append(other.address())
    return {name: sorted(targets) for name, targets in sorted(found.items())}


def run(code: str, provider: Provider, subject: dict, session: str, *, timeout: float = 60, operator: str = "",
        tool_args: dict | None = None) -> ToolReturn:
    """Run ``code`` with ``cu`` bound to ``provider``, within ``subject``."""
    from openprogram.programs.tools.computer_use.computer_use.code_mode.sandbox.runner import GuiRunnerError
    from openprogram.host.sandbox import SandboxUnavailable

    timeout = max(1.0, min(float(timeout or 60), MAX_TIMEOUT))
    try:
        broker, runner, slot = _runner(session)
    except (CodeError, SandboxUnavailable, PermissionError) as exc:
        return ToolReturn(text=f"Error: code is unavailable here: {exc}", is_error=True)
    surface = provider.surface()
    handle = None
    stopped, result, unreported = None, None, 0
    # Read here, on the thread of the call: the broker checks each step on its own.
    no_scripts = _no_scripts()
    unrestricted = _unrestricted(tool_args or {**provider.addressed(), "args": subject, "code": code})
    judge = _as_action_calls(provider) if unrestricted else None
    granted = provider.approval(subject, provider.opening(subject))
    try:
        from ..type_guides import _family_text
        names = [*surface.actions(), *vocabulary.carried(provider.family)]
        methods = {name: _adapter(provider, surface, name, subject, session, operator or session) for name in names}
        handle = broker.register(target=provider.target, methods=methods,
                                 validate=_validator(provider, surface, subject, granted, no_scripts, judge),
                                 reads=_reads(provider.family))
        own = sorted({provider.family, *_ALIASES.get(provider.family, ())})
        binding = [handle, provider.address(), _docs(surface, _family_text(provider.family)), names, own,
                   _others(provider, session)]
        prelude = runner.execute(_PRELUDE.replace("{binding}", repr(json.loads(json.dumps(binding)))), timeout=10)
        if prelude.error:
            raise GuiRunnerError(prelude.error)
        result = runner.execute(code, timeout=timeout)
    except (GuiRunnerError, ValueError, PermissionError) as exc:
        close(slot)
        stopped = exc
    finally:
        if handle is not None:
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


__all__ = ["MAX_TIMEOUT", "close", "close_conversation", "guide", "run", "run_slot"]
