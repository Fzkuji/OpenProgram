from types import SimpleNamespace

from openprogram.runtime.conversation.event_parsing import agent_event_to_envelope
from openprogram.models.providers.types import EventRetry


def test_retry_status_reaches_chat_stream_with_turn_identity():
    event = SimpleNamespace(
        type="message_update",
        assistant_message_event=EventRetry(
            attempt=2, max_attempts=3, reason="transport", delay_ms=1500,
        ),
    )
    request = SimpleNamespace(session_id="session", user_msg_id="user")

    envelope = agent_event_to_envelope(event, request)

    assert envelope == {
        "type": "chat_response",
        "data": {
            "type": "stream_event",
            "session_id": "session",
            "msg_id": "user",
            "event": {
                "type": "retry",
                "attempt": 2,
                "max_attempts": 3,
                "reason": "transport",
                "delay_ms": 1500,
            },
        },
    }
