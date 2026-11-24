"""computer_use code: the isolated interpreter reaches the call's target only through the broker."""
import sys

import pytest

from openprogram.programs.tools.computer_use.computer_use.sandbox.broker import GuiBroker
from openprogram.programs.tools.computer_use.computer_use.sandbox.runner import GuiPythonRunner
from openprogram.runtime.execution.attempts import AttemptStore
from openprogram.runtime.execution.recovery.effects import EffectStore
from openprogram.runtime.execution.model import CapabilitySet
from openprogram.runtime.execution.store import ExecutionStore
from openprogram.programs._host.execution import ToolReturn
from openprogram.programs.tools.computer_use.computer_use import code
from openprogram.programs.tools.computer_use.computer_use.contract import Approval
from tests.support.screen_targets import FakeProvider, app_approval, install

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="strict macOS sandbox")

_BROWSER_EXTRAS = ("list_tabs", "create_tab", "navigate", "upload_file", "evaluate")


def _upload_asks(subject, steps):
    """As the built-in browser asks: nothing, except an upload, which is approved alone each time."""
    if any(action == "upload_file" for action, _ in steps):
        return Approval("Upload the selected local file to the website")
    return None


class _Target(FakeProvider):
    """A target whose answers a test can replace; it records every step that reached it."""

    def __init__(self, *args, answers, **kwargs):
        super().__init__(*args, act=self._answer, **kwargs)
        self.answers = answers

    def _answer(self, subject, steps, call):
        if "act" in self.answers:
            return self.answers["act"](subject, steps, call)
        return ToolReturn(text=f"{self.target} {steps[0][0]} ok", images=[b"not a png"])

    def get_state(self, subject, options, call):
        self.calls.append(("get_state", dict(subject), dict(options), call.images))
        return ToolReturn(text=self.answers.get("state", f"{self.target} get_state ok"))


@pytest.fixture
def interpreter(tmp_path, monkeypatch):
    executions = ExecutionStore(tmp_path / "effects.db")
    revision = executions.create_revision(manifest={"entrypoint": "computer-use-code"})
    execution = executions.create_execution(execution_id="exec", run_id="run", session_id="session",
                                            revision_id=revision.revision_id, capabilities=CapabilitySet())
    attempts = AttemptStore(executions)
    leased, reserved = attempts.lease("exec", expected_version=execution.status_version,
                                      owner_id="worker", attempt_id="attempt")
    active, _ = attempts.activate("attempt", generation=leased.generation,
                                  expected_execution_version=reserved.status_version)
    effects = EffectStore(executions)
    broker = GuiBroker(effects=effects, execution_id="exec", attempt_id="attempt",
                       generation=active.generation, invocation_id="call-1")
    runner = GuiPythonRunner(broker=broker).__enter__()
    monkeypatch.setattr(code, "_runner", lambda session: (broker, runner, session))
    answers = {}
    targets = {
        "app": _Target("window", extras=("list_apps", "list_windows"), approval=app_approval, answers=answers),
        "browser": _Target("browser", extras=_BROWSER_EXTRAS, approval=_upload_asks,
                           page_scripts=("evaluate",), authority="browser.control", answers=answers),
        "chrome": _Target("browser", app="chrome", extras=_BROWSER_EXTRAS, answers=answers,
                          approval=lambda subject, steps: Approval("computer_use controls your Google Chrome",
                                                                   "browser chrome")),
    }
    install(monkeypatch, *targets.values())
    yield targets, answers, effects
    runner.close()


def _steps(target):
    return [(subject, steps) for kind, subject, steps in target.calls if kind == "act"]


def test_code_loops_over_actions_and_keeps_variables(interpreter):
    targets, _, effects = interpreter
    app = targets["app"]
    result = code.run("seen = []\nfor value in '358':\n    reply = await cu.type_text(text=value)\n"
                      "    seen.append(reply['text'])\nprint(len(seen), seen[-1])", app, {"app": "Numbers"}, "s")
    assert not result.is_error and result.text == "3 window type_text ok"
    assert _steps(app) == [({"app": "Numbers"}, [("type_text", {"text": v})]) for v in "358"]
    assert not effects.list_unresolved("exec")
    # The interpreter is the agent's, not the target's: a later call on another target sees the same variables.
    assert code.run("print(seen)", targets["browser"], {}, "s").text == \
        "['window type_text ok', 'window type_text ok', 'window type_text ok']"


def test_code_stays_inside_the_target_and_subject_its_call_named(interpreter):
    targets, _, _ = interpreter
    other = code.run("await cu.click(app='Finder', element=1)", targets["app"], {"app": "Numbers"}, "s")
    assert other.is_error and "this code call operates app='Numbers'; a step cannot name app='Finder'" in other.text
    # The user's own Chrome is a target of its own: a built-in browser call cannot reach it.
    away = code.run("await cu.chrome.click(element='e1')", targets["browser"], {}, "s")
    assert away.is_error and 'computer_use(type="browser", app="chrome", code=...)' in away.text
    # An upload from the built-in browser is approved alone, which code cannot wait for.
    upload = code.run("await cu.upload_file(path='/tmp/a.txt', element='e1')", targets["browser"], {}, "s")
    assert upload.is_error and "upload_file needs its own approval" in upload.text
    assert not any(target.calls for target in targets.values())


def test_code_runs_page_scripts_and_opens_sites_like_any_other_action(interpreter):
    targets, _, effects = interpreter
    browser = targets["browser"]
    result = code.run("await cu.navigate(url='https://example.com')\n"
                      "print((await cu.evaluate(script='1'))['text'])", browser, {}, "s")
    assert not result.is_error and result.text == "browser evaluate ok"
    assert _steps(browser) == [({}, [("navigate", {"url": "https://example.com"})]),
                               ({}, [("evaluate", {"script": "1"})])]
    assert not effects.list_unresolved("exec")


@pytest.mark.parametrize("source, tier, runs", [
    ("web", "owner", True), ("agent_spawn", "owner", True),
    ("mcp", "owner", False), ("agent_spawn", "trusted", False),
])
def test_code_runs_page_scripts_only_where_the_owner_attends(interpreter, source, tier, runs):
    from types import SimpleNamespace
    from openprogram.runtime.agent.turn_request import reset_turn_request, set_turn_request

    targets, _, effects = interpreter
    browser = targets["browser"]
    token = set_turn_request(SimpleNamespace(source=source, authority_tier=tier, permission_mode="ask"))
    try:
        result = code.run("print((await cu.get_state())['text'])\n"
                          "print((await cu.evaluate(script='document.cookie'))['text'])", browser, {}, "s")
    finally:
        reset_turn_request(token)
    # Reading the page is an action like any other; only the script is held back.
    assert [call[0] if call[0] == "get_state" else call[2][0][0] for call in browser.calls] == \
        (["get_state", "evaluate"] if runs else ["get_state"])
    assert result.is_error is not runs
    assert ("page scripts run only in work the owner started" in result.text) is not runs
    assert not effects.list_unresolved("exec")


def test_a_step_that_fails_after_starting_is_reported_and_leaves_nothing_for_a_person(interpreter):
    """The call's own result tells the model the step may have applied; an
    open record would stop the turn at its next checkpoint instead."""
    from openprogram.runtime.execution.recovery.effects import EffectStatus
    targets, answers, effects = interpreter
    app = targets["app"]
    answers["act"] = lambda subject, steps, call: ToolReturn(text="Error: the window closed", is_error=True)
    result = code.run("await cu.click(element=3)", app, {"app": "Numbers"}, "s")
    assert result.is_error and "the window closed" in result.text
    assert "may or may not have been applied" in result.text and "read the current state" in result.text
    assert effects.list_unresolved("exec") == []
    rows = effects.executions._connect().rows("effects", execution_id="exec")
    settled = [effects.get(row["effect_id"]) for row in rows]
    assert [(effect.status, effect.receipt["state"], effect.receipt["reported_by"]) for effect in settled] \
        == [(EffectStatus.COMMITTED, "unknown", "call_result")]
    # The interpreter is still there: code that catches the failure goes on.
    caught = code.run("try:\n    await cu.click(element=3)\nexcept RuntimeError:\n    print('caught')", app,
                      {"app": "Numbers"}, "s")
    assert not caught.is_error and caught.text.startswith("caught") and "may or may not" in caught.text
    assert effects.list_unresolved("exec") == []


def test_reading_a_lot_and_printing_too_much_do_not_stop_the_interpreter(interpreter):
    targets, answers, effects = interpreter
    app = targets["app"]
    answers["state"] = "x" * 100_000
    # Ten whole trees of 100 KB each: what a step returns goes to the code, not into the output budget.
    result = code.run("total = 0\nfor _ in range(10):\n    total += len((await cu.get_state())['text'])\n"
                      "print(total)", app, {"app": "Numbers"}, "s")
    assert not result.is_error and result.text == "1000000"
    printed = code.run("print('y' * 400_000)\nkept = 7", app, {"app": "Numbers"}, "s")
    assert not printed.is_error and "past 256 KB was dropped" in printed.text
    assert code.run("print(kept, total)", app, {"app": "Numbers"}, "s").text == "7 1000000"


def test_code_past_its_time_says_so_and_settles_the_step_it_was_in(interpreter):
    import threading
    targets, answers, effects = interpreter
    release = threading.Event()

    def slow(subject, steps, call):
        release.wait(5)
        return ToolReturn(text="late")
    answers["act"] = slow
    try:
        result = code.run("print('completed earlier work')\nawait cu.click(element=3)", targets["app"],
                          {"app": "Numbers"}, "s", timeout=1)
    finally:
        release.set()
    assert result.is_error and "ran longer than its 1 seconds" in result.text
    assert "may or may not have been applied" in result.text and "starts again" in result.text
    assert "Output collected before stopping:\ncompleted earlier work" in result.text
    assert effects.list_unresolved("exec") == []


def test_cu_is_the_calls_target_and_nothing_else(interpreter):
    targets, _, _ = interpreter
    result = code.run("print(hasattr(cu, 'browser'), hasattr(cu, 'app'), hasattr(cu, 'click'), "
                      "hasattr(cu, 'list_apps'))", targets["browser"], {}, "s")
    # cu.browser on a browser call is not a second spelling of cu: cu is already the target.
    assert result.text == "False False True False"


def test_a_read_in_code_is_the_whole_tree(interpreter):
    targets, _, _ = interpreter
    app = targets["app"]
    # A name carried over from another tool reads the same way.
    code.run("await cu.get_app_state()", app, {"app": "Numbers"}, "s")
    assert app.calls == [("get_state", {"app": "Numbers"}, {"full": True, "include": "tree"}, False)]
