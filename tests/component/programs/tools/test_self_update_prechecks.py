"""A prepare call is judged by what its kind of install asks for."""
from openprogram.programs._authoring import prechecks
import openprogram.programs.tools.system.self_update  # noqa: F401 - registers the precheck


def _problem(arguments):
    found = prechecks.call_problem("self_update", arguments)
    return None if found is None else str(getattr(found, "message", found))


def test_a_prepare_by_task_needs_no_commit():
    assert _problem({"action": "prepare", "task": "feature", "goal": "g", "assertions": ["works"]}) is None


def test_a_prepare_by_task_still_needs_a_goal():
    assert "goal" in _problem({"action": "prepare", "task": "feature", "assertions": ["works"]})


def test_a_prepare_by_commit_still_needs_a_full_commit():
    assert "candidate_sha" in _problem({"action": "prepare", "candidate_sha": "abc", "goal": "g", "assertions": ["works"]})
