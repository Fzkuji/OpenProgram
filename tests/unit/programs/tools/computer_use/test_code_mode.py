"""Code mode: cu is the call's target and nothing else, approved exactly as an action call.

The isolated interpreter is replaced by one that runs the code in this process
and reaches ``cu`` through the same register / validate / method calls the GUI
broker makes, so what is tested is code.py's binding, not the sandbox.
"""
from __future__ import annotations

import ast
import asyncio
from contextlib import redirect_stdout
import io
from types import SimpleNamespace
import time

import pytest

from openprogram.programs._host.execution import ToolReturn
from openprogram.programs.tools.computer_use.computer_use import code as code_mode
from openprogram.programs.tools.computer_use.computer_use import locks, tool
from openprogram.programs.tools.computer_use.computer_use.contract import Approval
from openprogram.programs.tools.computer_use.computer_use.locks import Busy, Locks
from openprogram.programs.tools.computer_use.computer_use.tool import computer_use

from tests.support.screen_targets import PIXELS, FakeProvider, app_approval, environment, install_machines, local, machine

MACHINE = locks.machine_key("local")


class _Broker:
    """The GUI broker's contract with code.py: one registered resource, each step validated, then run."""

    def __init__(self):
        self.resources: dict[str, tuple] = {}
        self.revoked: list[str] = []

    def register(self, *, target, methods, validate, reads=None):
        handle = f"h{len(self.resources) + 1}"
        self.resources[handle] = (target, methods, validate, reads)
        return handle

    def revoke_resource(self, handle):
        self.revoked.append(handle)
        self.resources.pop(handle, None)

    def settle(self):
        return 0

    async def call(self, handle, method, arguments):
        target, methods, validate, _ = self.resources[handle]
        if method not in methods:
            raise PermissionError("unknown GUI resource or denied method")
        validate(method, arguments, None)
        context = SimpleNamespace(check=lambda: None, deadline=time.monotonic() + 30)
        value = methods[method](arguments, None, context)
        if value.is_error:
            raise RuntimeError(value.text)
        return {"value": {"text": value.text, "json_data": value.json_data}}


class _Runner:
    """Runs code with top-level await in one namespace that lives across calls, as the interpreter does."""

    def __init__(self, broker: _Broker, **names):
        self.namespace = {"ui": broker, **names}

    def execute(self, code: str, timeout: float):
        out, error = io.StringIO(), None
        try:
            with redirect_stdout(out):
                compiled = compile(code, "<code>", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
                pending = eval(compiled, self.namespace)
                if pending is not None:
                    asyncio.run(pending)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        return SimpleNamespace(stdout=out.getvalue(), stderr="", truncated=False, error=error)


@pytest.fixture
def interpreter(monkeypatch, tmp_path):
    broker = _Broker()
    runner = _Runner(broker, free=lambda: _free(MACHINE))
    monkeypatch.setattr(code_mode, "_runner", lambda session: (broker, runner, "slot"))
    monkeypatch.setattr(code_mode, "_no_scripts", lambda: False)
    monkeypatch.setattr(code_mode, "_unrestricted", lambda tool_args: False)
    monkeypatch.setattr(tool, "_code_available", lambda: True)
    monkeypatch.setattr(locks, "_root", lambda: tmp_path / "locks")
    environment(monkeypatch, local())
    return SimpleNamespace(broker=broker, runner=runner)


def _free(key: str) -> bool:
    try:
        with locks.held(Locks(exclusive=(key,)), seconds=0.1):
            return True
    except Busy:
        return False


@pytest.fixture
def targets(monkeypatch):
    app = FakeProvider("window", extras=("list_apps",), approval=app_approval,
                       locks=lambda subject, steps: Locks(exclusive=(MACHINE,)))
    browser = FakeProvider("browser", extras=("list_tabs", "upload_file", "evaluate"),
                           authority="browser.control", page_scripts=("evaluate",),
                           approval=lambda subject, steps: (Approval("upload a file") if any(
                               name == "upload_file" for name, _ in steps) else None))
    lab = FakeProvider("screen", "lab", capabilities=PIXELS)
    install_machines(monkeypatch, [local(window="fake", browser="fake"), machine("lab", screen="fake")],
            {"local": [app, browser], "lab": [lab]})
    return SimpleNamespace(app=app, browser=browser, lab=lab)


def _run(type: str, code: str, device: str = "", app: str = "", **args) -> ToolReturn:
    return computer_use(type=type, device=device, app=app, args=args, code=code)


def test_cu_is_the_calls_target(interpreter, targets):
    result = _run("window", "print((await cu.click(element=3))['text'])", app="Numbers")
    assert result.text == "Done: click" and not result.is_error
    assert targets.app.calls == [("act", {"app": "Numbers"}, [("click", {"element": 3})])]
    assert targets.browser.calls == [] and targets.lab.calls == []
    # One resource was registered, for this target only, and given back when the call ended.
    assert interpreter.broker.revoked == ["h1"] and interpreter.broker.resources == {}


def test_cu_takes_carried_over_names(interpreter, targets):
    _run("window", "await cu.left_click(element_index=3)\nawait cu.hotkey(keys=['cmd', 'a'])", app="Numbers")
    assert [call[2] for call in targets.app.calls] == [[("click", {"element": 3})],
                                                         [("press_key", {"key": "super+a"})]]


def test_cu_of_its_own_family_says_cu_is_already_the_target(interpreter, targets):
    result = _run("window", "await cu.app.click(element=1)", app="Numbers")
    assert result.is_error
    assert "cu is already this call's target, type=\"window\": write cu.<action>(...), not cu.app.<action>(...)" in result.text
    assert targets.app.calls == []
    result = _run("screen", "await cu.screen.click(x=1, y=2)", device="lab")
    assert result.is_error and "cu is already this call's target, device=\"lab\", type=\"screen\"" in result.text
    assert targets.lab.calls == []


def test_another_surface_is_named_by_the_targets_listed_under_it(interpreter, targets):
    # Only the lab's screen is listed: the hint never sends the model to this computer's screen.
    result = _run("window", "await cu.screen.click(x=1, y=2)", app="Numbers")
    assert result.is_error and 'screen takes a call of its own: computer_use(device="lab", type="screen", code=...)' \
        in result.text
    # A surface with no listed target is no hint at all.
    result = _run("window", "await cu.mobile.click(x=1, y=2)", app="Numbers")
    assert result.is_error and "UNKNOWN_ACTION: type=\"window\" has no action 'mobile'" in result.text


def test_another_surface_raises_with_the_call_to_make(interpreter, targets):
    result = _run("window", "await cu.browser.click(element='e1')", app="Numbers")
    assert result.is_error
    assert "cu is this call's target, type=\"window\". browser takes a call of its own" in result.text
    assert 'computer_use(type="browser", code=...)' in result.text
    assert targets.browser.calls == []


def test_an_action_the_target_lacks_is_unknown(interpreter, targets):
    result = _run("window", "await cu.navigate(url='https://example.com')", app="Numbers")
    assert result.is_error and "UNKNOWN_ACTION: type=\"window\" has no action 'navigate'" in result.text


def test_the_subject_the_call_names_holds_for_every_step(interpreter, targets):
    _run("window", "await cu.click(element=1)\nawait cu.click(app='numbers', element=2)", app="Numbers")
    assert [call[1] for call in targets.app.calls] == [{"app": "Numbers"}, {"app": "numbers"}]
    result = _run("window", "await cu.click(app='Finder', element=2)", app="Numbers")
    assert result.is_error and "BAD_ARGUMENT: this code call operates app='Numbers'" in result.text
    assert all(call[1]["app"].lower() == "numbers" for call in targets.app.calls)


def test_a_step_outside_what_was_approved_is_refused_with_the_rule_keys(interpreter, targets):
    # The call named no application, so it was approved for nothing an application asks.
    result = _run("window", "await cu.click(app='Finder', element=2)")
    assert result.is_error and "window finder" in result.text and "not approved" in result.text
    assert targets.app.calls == []


def test_a_step_that_asks_for_another_rule_names_both(monkeypatch, interpreter):
    def approval(subject, steps):
        if any(name == "drag" for name, _ in steps):
            return Approval("operate the whole screen", "screen")
        return app_approval(subject, steps)

    app = FakeProvider("window", approval=approval)
    install_machines(monkeypatch, [local(window="fake")], {"local": [app]})
    result = _run("window", "await cu.drag(x=1, y=1, to_x=2, to_y=2)", app="Numbers")
    assert result.is_error
    assert "this code call is approved for window numbers; drag asks for screen" in result.text
    assert app.calls == []


def test_a_step_that_needs_its_own_approval_is_refused(interpreter, targets):
    result = _run("browser", "await cu.upload_file(element='e1', path='/tmp/a.pdf')")
    assert result.is_error and "upload_file needs its own approval" in result.text
    assert targets.browser.calls == []


def test_page_scripts_are_refused_where_nobody_attends(monkeypatch, interpreter, targets):
    monkeypatch.setattr(code_mode, "_no_scripts", lambda: True)
    result = _run("browser", "await cu.evaluate(script='document.title')")
    assert result.is_error and "page scripts run only in work the owner started" in result.text
    assert targets.browser.calls == []
    monkeypatch.setattr(code_mode, "_no_scripts", lambda: False)
    assert not _run("browser", "await cu.evaluate(script='document.title')").is_error


def test_page_scripts_stay_out_of_turns_working_from_text_the_owner_did_not_write(monkeypatch):
    from openprogram.runtime.agent import turn_request
    from openprogram.runtime.permissions import identity

    monkeypatch.setattr(turn_request, "get_turn_request", lambda: SimpleNamespace())
    monkeypatch.setattr(identity, "unattended", lambda request: False)
    monkeypatch.setattr(identity, "outside_text", lambda request: True)
    assert code_mode._no_scripts()
    monkeypatch.setattr(identity, "outside_text", lambda request: False)
    assert not code_mode._no_scripts()


def test_in_bypass_each_step_is_judged_as_the_action_call_it_is(monkeypatch, interpreter, targets):
    judged = []

    def judge(action, subject, step):
        judged.append((action, subject, step))
        return "deny" if str(subject.get("app", "")).lower() == "terminal" else "allow"

    monkeypatch.setattr(code_mode, "_unrestricted", lambda tool_args: True)
    monkeypatch.setattr(code_mode, "_as_action_calls", lambda provider: judge)
    # The call names no application, so its own rule key is none; the step's own is.
    result = _run("window", "await cu.click(app='Terminal', element=1)")
    assert result.is_error and "the owner's rules refuse it or ask about it" in result.text
    assert targets.app.calls == []
    result = _run("window", "await cu.click(app='Numbers', element=1)")
    assert not result.is_error and targets.app.calls == [("act", {"app": "Numbers"}, [("click", {"element": 1})])]
    assert ("click", {"app": "Terminal"}, {"element": 1}) in judged


def test_a_step_is_judged_with_the_whole_action_call_it_would_be(monkeypatch):
    from openprogram.runtime.agent import turn_request
    from openprogram.runtime.permissions import policy
    import openprogram.programs as programs

    asked = []
    monkeypatch.setattr(turn_request, "get_turn_request", lambda: "request")
    monkeypatch.setattr(programs, "get_agent_tool", lambda name: "tool")
    monkeypatch.setattr(policy, "permission_decision",
                        lambda tool, request, args: asked.append((tool, request, args)) or SimpleNamespace(
                            outcome="deny"))
    judge = code_mode._as_action_calls(FakeProvider("window"))
    assert judge("click", {"app": "Terminal"}, {"element": 1}) == "deny"
    assert asked == [("tool", "request", {"type": "window", "action": "click",
                                          "args": {"app": "Terminal", "element": 1}})]


def test_states_are_text_only_and_a_tree_is_read_whole(interpreter, targets):
    result = _run("window", "state = await cu.get_state()\nprint(state['text'])", app="Numbers")
    assert result.text == "state of window"
    assert targets.app.calls == [("get_state", {"app": "Numbers"}, {"full": True, "include": "tree"}, False)]
    _run("screen", "await cu.screenshot()", device="lab")
    # A target without a tree is not asked for one; it still sends no picture to code.
    assert targets.lab.calls == [("get_state", {}, {"include": "screenshot"}, False)]


def test_no_lock_is_held_between_two_steps(interpreter, monkeypatch):
    held_during = []

    def act(subject, steps, call):
        held_during.append(not _free(MACHINE))
        return ToolReturn(text="Done")

    app = FakeProvider("window", approval=app_approval, act=act,
                       locks=lambda subject, steps: Locks(exclusive=(MACHINE,)))
    install_machines(monkeypatch, [local(window="fake")], {"local": [app]})
    result = _run("window", "await cu.click(element=1)\nprint(free())\nawait cu.click(element=2)\nprint(free())",
                  app="Numbers")
    assert result.text == "True\nTrue"
    assert held_during == [True, True]


def test_variables_carry_over_between_calls_on_two_targets(interpreter, targets):
    _run("window", "seen = (await cu.get_state())['text']", app="Numbers")
    result = _run("screen", "print(seen)\nawait cu.click(x=1, y=2)", device="lab")
    assert result.text == '[device="lab", type="screen"]\nstate of window'
    assert targets.lab.calls[-1] == ("act", {}, [("click", {"x": 1, "y": 2})])


def test_code_with_an_action_is_refused(interpreter, targets):
    result = computer_use(type="window", action="click", args={"element": 1}, code="pass")
    assert result.is_error and result.text.startswith("BAD_ARGUMENT: send code, or action / actions, not both.")
    assert targets.app.calls == []


def test_code_is_approved_as_the_action_call_with_its_subject(targets):
    asked = tool.approval({"type": "window", "app": "Numbers", "code": "await cu.click(element=1)"})
    assert asked == Approval("computer_use controls Numbers", "window numbers")
    assert tool.rule_key({"type": "window", "app": "Numbers", "code": "pass"}) == \
        tool.rule_key({"type": "window", "app": "Numbers"}) == \
        tool.rule_key({"type": "window", "args": {"app": "Numbers"}, "code": "pass"})
