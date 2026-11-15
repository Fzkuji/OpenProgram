"""A stored turn stays readable by the version before the always-on work.

That version refuses a stored request naming a field it does not know, and
it re-reads stored requests to resume a paused turn, continue one after a
restart, or start a background process's notice. Fields added since then
are therefore stored only when they say something.
"""
from openprogram.runtime.agent.turn_request import TurnRequest
from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter
from openprogram.runtime.conversation.production_driver.shared import normalize_agent_turn_payload

ADDED_SINCE_THE_PREVIOUS_VERSION = {"last_round_text", "resumes_message_id", "automation", "recall_query", "detached"}


def test_an_ordinary_turn_stores_only_fields_the_previous_version_reads():
    payload = normalize_agent_turn_payload(CanonicalAgentAdapter.payload_for(TurnRequest("s", "x", "a", "web")))
    assert not ADDED_SINCE_THE_PREVIOUS_VERSION & set(payload["request"])


def test_a_copied_request_with_unset_marks_stores_none_of_them():
    # A process notice copies the last request and writes its marks, unset or not.
    request = dict(CanonicalAgentAdapter.payload_for(TurnRequest("s", "x", "a", "web"))["request"])
    request.update(automation=None, detached=False, recall_query=None, resumes_message_id=None, last_round_text="")
    payload = normalize_agent_turn_payload({"version": 1, "kind": "chat", "request": request})
    assert not ADDED_SINCE_THE_PREVIOUS_VERSION & set(payload["request"])


def test_a_mark_that_says_something_is_kept():
    request = TurnRequest("s", "x", "a", "web", automation="handoff", detached=True, recall_query="")
    stored = normalize_agent_turn_payload(CanonicalAgentAdapter.payload_for(request))["request"]
    assert (stored["automation"], stored["detached"], stored["recall_query"]) == ("handoff", True, "")
    assert TurnRequest(**stored).automation == "handoff"
