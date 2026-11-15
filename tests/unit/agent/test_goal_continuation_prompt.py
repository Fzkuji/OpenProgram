"""A Goal started from an always-on standing goal is told it is one objective, not a routine."""

from openprogram.programs.tools.planning.goal import chat


def test_plain_goal_continues_without_the_standing_note():
    prompt = chat.continuation_prompt({"text": "Fix the build"})
    assert prompt.startswith("Continue the active Goal.")
    assert chat.STANDING_GOAL_NOTE not in prompt
    assert chat.IDLE_WARNING not in prompt


def test_always_on_goal_is_one_objective_not_a_routine():
    prompt = chat.continuation_prompt({"text": "Check the build and report", "origin": "always_on"})
    assert chat.STANDING_GOAL_NOTE in prompt
    assert "scheduled check-ins" in prompt


def test_idle_warning_still_follows_the_note():
    prompt = chat.continuation_prompt({"origin": "always_on", "idle_turns": 2})
    assert prompt.index(chat.STANDING_GOAL_NOTE) < prompt.index(chat.IDLE_WARNING)
