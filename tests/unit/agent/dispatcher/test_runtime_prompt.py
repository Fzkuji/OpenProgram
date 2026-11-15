"""Prompts the runtime writes are sent to the model but not drawn as a
message from the owner."""
from openprogram.runtime.conversation.turn_runtime.prep import is_runtime_prompt
from openprogram.runtime.agent.turn_request import TurnRequest


def _request(**changes):
    fields = {"session_id": "s", "user_text": "continue",
              "agent_id": "main", "source": "web"}
    return TurnRequest(**{**fields, **changes})


def test_owner_message_is_drawn():
    assert not is_runtime_prompt(_request())


def test_restart_resume_continues_without_a_message():
    assert is_runtime_prompt(_request(restart_resume=True))


def test_followups_and_goal_triggers_stay_hidden():
    assert is_runtime_prompt(_request(source="job_followup"))
    assert is_runtime_prompt(_request(goal_trigger=True))
