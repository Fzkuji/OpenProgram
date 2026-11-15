"""computer_use_agent code: the isolated interpreter reaches targets only through the broker."""
import sys

import pytest

from openprogram.programs.tools.computer_use.computer_use_agent.sandbox.broker import GuiBroker
from openprogram.programs.tools.computer_use.computer_use_agent.sandbox.runner import GuiPythonRunner
from openprogram.runtime.execution.attempts import AttemptStore
from openprogram.runtime.execution.effects import EffectStore
from openprogram.runtime.execution.model import CapabilitySet
from openprogram.runtime.execution.store import ExecutionStore
from openprogram.programs._host.execution import ToolReturn
from openprogram.programs.tools.computer_use.computer_use_agent import code
from openprogram.programs.tools.computer_use.computer_use_agent.targets import app, browser, vm

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="strict macOS sandbox")


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
    monkeypatch.setattr(app, "available", lambda: True)
    monkeypatch.setattr(vm, "available", lambda: False)
    calls = []

    def record(name):
        def run(steps, args, session):
            calls.append((name, steps, args.get("app")))
            return ToolReturn(text=f"{name} {steps[0][0]} ok", images=[b"not a png"])
        return run
    monkeypatch.setattr(app, "run", record("app"))
    monkeypatch.setattr(browser, "run", record("browser"))
    yield calls, effects
    runner.close()


def test_code_loops_over_actions_and_keeps_variables(interpreter):
    calls, effects = interpreter
    result = code.run("seen = []\nfor value in '358':\n    reply = await cu.app.type_text(app='Numbers', text=value)\n"
                      "    seen.append(reply['text'])\nprint(len(seen), seen[-1])", {"app": "Numbers"}, "s")
    assert not result.is_error and result.text == "3 app type_text ok"
    assert [step for _, step, _ in calls] == [[("type_text", {"app": "Numbers", "text": v})] for v in "358"]
    assert not effects.list_unresolved("exec")
    assert code.run("print(seen)", {"app": "Numbers"}, "s").text == "['app type_text ok', 'app type_text ok', " \
                                                                    "'app type_text ok']"


def test_code_stays_inside_the_app_and_browser_its_call_named(interpreter):
    calls, _ = interpreter
    other = code.run("await cu.app.click(app='Finder', element_index=1)", {"app": "Numbers"}, "s")
    assert other.is_error and "approved for app numbers" in other.text
    # The grant of a call names one browser; a step cannot reach the user's own from the built-in one.
    away = code.run("await cu.browser.click(browser='chrome', ref='e1')", {}, "s")
    assert away.is_error and "operates the built-in browser" in away.text
    # An upload from the built-in browser is approved alone, which code cannot wait for.
    upload = code.run("await cu.browser.upload(path='/tmp/a.txt', ref='e1')", {}, "s")
    assert upload.is_error and "own approval" in upload.text
    assert calls == []


def test_code_runs_page_scripts_and_opens_sites_like_any_other_action(interpreter):
    calls, effects = interpreter
    result = code.run("await cu.browser.navigate(url='https://example.com')\n"
                      "print((await cu.browser.evaluate(script='1'))['text'])", {}, "s")
    assert not result.is_error and result.text == "browser evaluate ok"
    assert [steps for _, steps, _ in calls] == [[("navigate", {"url": "https://example.com"})],
                                                [("evaluate", {"script": "1"})]]
    assert not effects.list_unresolved("exec")


@pytest.mark.parametrize("source, tier, runs", [
    ("web", "owner", True), ("agent_spawn", "owner", True),
    ("mcp", "owner", False), ("agent_spawn", "trusted", False),
])
def test_code_runs_page_scripts_only_where_the_owner_attends(interpreter, source, tier, runs):
    from types import SimpleNamespace
    from openprogram.runtime.agent.turn_request import reset_turn_request, set_turn_request

    calls, effects = interpreter
    token = set_turn_request(SimpleNamespace(source=source, authority_tier=tier, permission_mode="ask"))
    try:
        result = code.run("print((await cu.browser.read())['text'])\n"
                          "print((await cu.browser.evaluate(script='document.cookie'))['text'])", {}, "s")
    finally:
        reset_turn_request(token)
    # Reading the page is an action like any other; only the script is held back.
    assert [steps[0][0] for _, steps, _ in calls] == (["read", "evaluate"] if runs else ["read"])
    assert result.is_error is not runs
    assert ("page scripts run only in work the owner started" in result.text) is not runs
    assert not effects.list_unresolved("exec")


def test_a_step_that_fails_after_starting_is_reported_and_leaves_nothing_for_a_person(interpreter, monkeypatch):
    """The call's own result tells the model the step may have applied; an
    open record would stop the turn at its next checkpoint instead."""
    from openprogram.runtime.execution.effects import EffectStatus
    calls, effects = interpreter
    monkeypatch.setattr(app, "run", lambda steps, args, session: ToolReturn(text="Error: the window closed", is_error=True))
    result = code.run("await cu.app.click(app='Numbers', element_index=3)", {"app": "Numbers"}, "s")
    assert result.is_error and "the window closed" in result.text
    assert "may or may not have been applied" in result.text and "read the current state" in result.text
    assert effects.list_unresolved("exec") == []
    rows = effects.executions._connect().rows("effects", execution_id="exec")
    settled = [effects.get(row["effect_id"]) for row in rows]
    assert [(effect.status, effect.receipt["state"], effect.receipt["reported_by"]) for effect in settled] \
        == [(EffectStatus.COMMITTED, "unknown", "call_result")]
    # The interpreter is still there: code that catches the failure goes on.
    caught = code.run("try:\n    await cu.app.click(app='Numbers', element_index=3)\nexcept RuntimeError:\n"
                      "    print('caught')", {"app": "Numbers"}, "s")
    assert not caught.is_error and caught.text.startswith("caught") and "may or may not" in caught.text
    assert effects.list_unresolved("exec") == []


def test_reading_a_lot_and_printing_too_much_do_not_stop_the_interpreter(interpreter, monkeypatch):
    calls, effects = interpreter
    monkeypatch.setattr(app, "run", lambda steps, args, session: ToolReturn(text="x" * 100_000))
    # Ten whole trees of 100 KB each: what a step returns goes to the code, not into the output budget.
    result = code.run("total = 0\nfor _ in range(10):\n    total += len((await cu.app.get_app_state(app='Numbers'))['text'])\n"
                      "print(total)", {"app": "Numbers"}, "s")
    assert not result.is_error and result.text == "1000000"
    printed = code.run("print('y' * 400_000)\nkept = 7", {"app": "Numbers"}, "s")
    assert not printed.is_error and "past 256 KB was dropped" in printed.text
    assert code.run("print(kept, total)", {"app": "Numbers"}, "s").text == "7 1000000"


def test_code_past_its_time_says_so_and_settles_the_step_it_was_in(interpreter, monkeypatch):
    import threading
    calls, effects = interpreter
    release = threading.Event()

    def slow(steps, args, session):
        release.wait(5)
        return ToolReturn(text="late")
    monkeypatch.setattr(app, "run", slow)
    try:
        result = code.run("print('completed earlier work')\nawait cu.app.click(app='Numbers', element_index=3)",
                          {"app": "Numbers"}, "s", timeout=1)
    finally:
        release.set()
    assert result.is_error and "ran longer than its 1 seconds" in result.text
    assert "may or may not have been applied" in result.text and "starts again" in result.text
    assert "Output collected before stopping:\ncompleted earlier work" in result.text
    assert effects.list_unresolved("exec") == []


def test_without_an_app_there_is_no_app_target(interpreter):
    result = code.run("print(hasattr(cu, 'app'), hasattr(cu, 'browser'), hasattr(cu, 'vm'))", {}, "s")
    assert result.text == "False True False"


def test_a_read_in_code_is_the_whole_tree(interpreter):
    calls, _ = interpreter
    code.run("await cu.app.get_app_state(app='Numbers')", {"app": "Numbers"}, "s")
    assert calls == [("app", [("get_app_state", {"app": "Numbers", "full": True, "include": "tree"})], "Numbers")]
