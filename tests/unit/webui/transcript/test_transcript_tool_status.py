from __future__ import annotations

from openprogram.interfaces.server.transcript.blocks import project_blocks


def _status(**block):
    _blocks, tools, _thinking = project_blocks("r", [{"type": "tool", "tool": "bash", "tool_call_id": "c", "input": "{}", **block}])
    return tools[0]["status"]


def test_a_call_with_no_result_yet_is_running_and_one_answered_is_done():
    assert _status(result=None) == "running"
    assert _status(result="done\n") == "done"
    assert _status(result="") == "done"


def test_a_call_that_failed_stopped_or_never_ran_is_not_running():
    assert _status(result=None, is_error=True) == "error"
    assert _status(result=None, outcome="cancelled") == "cancelled"
    assert _status(result=None, outcome="not_started") == "error"
    assert _status(result=None, outcome="waiting") == "done"
    assert _status(result=None, provider_executed=True) == "done"
