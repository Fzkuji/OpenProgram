"""Activity pairs every returned ``agent`` call with the sub-agent it started."""
import json
from types import SimpleNamespace

from openprogram.runtime.execution.activity_calls import agent_calls


class _Store:
    def __init__(self, inputs):
        self.inputs = inputs

    def get_job_agent_input(self, execution_id):
        return self.inputs.get(execution_id)


def _call(row_id, caller, prompt, description, *, content="ok", is_error=False, outcome="completed", at=1.0):
    return {
        "id": row_id, "role": "tool", "function": "agent", "caller": caller,
        "content": content, "is_error": str(is_error), "outcome": outcome, "timestamp": str(at),
        "extra": json.dumps({"tool_use": {"name": "agent", "arguments": json.dumps(
            {"prompt": prompt, "description": description})}}),
    }


def _job(job_id, caller, prompt):
    return {"turn_request": {"spawn_caller": caller, "user_text": prompt}}


def test_each_call_pairs_with_the_job_it_started_and_failed_ones_with_none():
    executions = [SimpleNamespace(execution_id=i, session_id="s") for i in ("exec_main", "j_a", "j_b")]
    store = _Store({"j_a": _job("j_a", "r1_reply", "Read the docs"), "j_b": _job("j_b", "j_a_user_reply", "Check links")})
    messages = [
        # Before failed spawns were marked as errors only the text said so.
        _call("r1_reply_t_1", "r1_reply", "Read the docs", "Docs",
              content="[agent error] JobAgentInputError: Job Agent input must be JSON serializable", at=1),
        _call("r1_reply_t_2", "r1_reply", "Read the docs", "Docs", at=2),
        _call("j_a_user_reply_t_3", "j_a_user_reply", "Check links", "Links", at=3),
        {"id": "r1_reply", "role": "assistant", "content": "done"},
    ]
    rows = agent_calls(store, "s", executions, lambda sid: messages)
    assert [(row["id"], row["job_id"], row["owner_job_id"], row["failed"]) for row in rows] == [
        ("r1_reply_t_1", None, None, True),
        ("r1_reply_t_2", "j_a", None, False),
        ("j_a_user_reply_t_3", "j_b", "j_a", False),
    ]
    assert rows[0]["error"].startswith("[agent error] JobAgentInputError")
    assert rows[1]["prompt"] == "Read the docs" and rows[1]["description"] == "Docs"


def test_a_lone_job_pairs_with_its_callers_only_call_when_prompts_differ():
    executions = [SimpleNamespace(execution_id="j_a", session_id="s")]
    store = _Store({"j_a": _job("j_a", "r1_reply", "Read the docs (trimmed)")})
    messages = [_call("r1_reply_t_1", "r1_reply", "Read the docs", "Docs", content="Cancelled: ",
                      is_error=True, outcome="cancelled")]
    assert agent_calls(store, "s", executions, lambda sid: messages)[0]["job_id"] == "j_a"
