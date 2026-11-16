"""computer_use_agent checks each action's arguments before anything runs."""
from __future__ import annotations

import re
from pathlib import Path

from openprogram.programs.tools.computer_use.computer_use_agent import params
from openprogram.programs.tools.computer_use.computer_use_agent.targets import app, browser, vm

GUIDES = Path(params.__file__).parent / "guides"


def test_every_action_of_every_target_has_its_arguments():
    from openprogram.programs._host import services as _services

    _services.load_programs()
    for name, actions in (("app", app.ACTIONS), ("browser", browser.actions()), ("vm", vm.ACTIONS)):
        assert set(params.ACTION_ARGS[name]) == set(actions), name


def test_the_guides_name_only_arguments_the_table_accepts():
    for name in ("app", "browser", "vm"):
        guide = (GUIDES / f"{name}.md").read_text(encoding="utf-8")
        shared = set(params.SHARED_ARGS[name])
        for action, args in params.ACTION_ARGS[name].items():
            for match in re.finditer(rf"(?<![\w.]){action}\(([^)]*)\)", guide):
                named = set()
                for item in match.group(1).split(","):
                    # "a | b" offers alternatives; after "=" come values, not names.
                    for part in ([item] if "=" in item else item.split("|")):
                        word = re.match(r"\s*([a-z_]+)", part)
                        if word and not part.strip().startswith('"'):
                            named.add(word.group(1))
                assert named <= set(args) | shared, (name, action, named - set(args) - shared)


def test_playwright_spellings_are_rewritten():
    action, step, error = params.check_step("browser", "select_option", {"ref": "e356", "values": ["F1"]})
    assert (action, step, error) == ("select", {"ref": "e356", "value": "F1"}, "")
    action, step, error = params.check_step("browser", "evaluate", {"expression": "() => 1"})
    assert (action, step, error) == ("evaluate", {"script": "() => 1"}, "")


def test_unknown_arguments_and_actions_fail_with_the_signature():
    _, _, error = params.check_step("browser", "click", {"ref": "e1", "selector": "#go"})
    assert error == ("click does not take selector; it is "
                     "click(ref, role, name, x, y, mouse_button, click_count, modifiers).")
    _, _, error = params.check_step("app", "clik", {})
    assert "Did you mean click(" in error


def test_one_step_splits_shared_arguments_and_several_steps_keep_them_apart():
    steps, shared, error = params.check("browser", [("type", {"ref": "e3", "text": "hi", "browser": "chrome"})],
                                        {"browser": "chrome", "ref": "e3", "text": "hi"})
    assert error == ""
    assert steps == [("type", {"ref": "e3", "text": "hi"})]
    assert shared == {"browser": "chrome"}
    _, _, error = params.check("app", [("click", {"element_index": 1}), ("press_key", {"key": "Return"})],
                               {"app": "Notes", "text": "x"})
    assert "move text into the steps" in error
