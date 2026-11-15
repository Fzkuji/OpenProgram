"""chat_send — post a message to the chat a channel conversation is in.

A turn that came in through Telegram, Discord, Slack or WeChat normally
answers with its final reply, which the channel layer posts. Two cases
need the agent to speak on its own:

* **Room events.** In a group set to ``/activation ambient``, a message
  that does not address the bot still reaches the agent, marked
  ``[room event]`` at the top. The final reply of such a turn stays in
  the transcript and is never posted; this tool is the only way the room
  hears from the agent, so staying quiet is the default.
* **A short note before long work** in an addressed turn ("checking,
  one moment"). The final reply still goes out afterwards.

The tool knows only the session id. Which chat to post to comes from the
turn the channel layer registered (reached through
:mod:`openprogram.runtime.channel_link`), never from the session row: a ``session_scope: main`` session holds
several chats.
"""
from __future__ import annotations

from openprogram.programs._host.runtime import ToolReturn, function
# The tool is hidden on every other source.
from openprogram.runtime.execution.foreground import CHANNEL_SOURCES

_DESCRIPTION = (
    "Post a message to the chat this conversation is in (Telegram, Discord, "
    "Slack or WeChat).\n"
    "\n"
    "- A user message that starts with `[room event]` was said in a group "
    "without addressing you. Your final reply to it is NOT posted; this tool "
    "is the only way to speak in the room. Call it only when you have "
    "something the people there would want: an answer to a question you can "
    "settle, a correction, something they asked you earlier. Otherwise do "
    "not call it, and end the turn with a few words noting why you stayed "
    "quiet.\n"
    "- In any other turn your final reply is posted automatically. Use this "
    "tool only for a short note before long work; never repeat the reply "
    "through it.\n"
    "- Write as a chat message: short, no headings. At most 5 calls per turn."
)


@function(name="chat_send", description=_DESCRIPTION, toolset=["core"])
def chat_send(text: str) -> str | ToolReturn:
    """Post a message to the chat this conversation is in.

    Args:
        text: The message to post.
    """
    out = _chat_send_impl(text)
    if out.startswith("Error:"):
        return ToolReturn(text=out, is_error=True)
    return out


def _chat_send_impl(text: str) -> str:
    from openprogram.runtime import channel_link
    from openprogram.runtime.execution.run_control import get_current_session_id

    body = (text or "").strip()
    if not body:
        return "Error: text is required."
    session_id = get_current_session_id() or ""
    try:
        turn = channel_link.active_turn(session_id)
    except channel_link.ChannelsUnavailable as exc:
        return f"Error: {exc}"
    if turn is None:
        return ("Error: this conversation is not running a chat-channel turn, "
                "so there is no chat to post to.")
    if not channel_link.take_send(session_id):
        return (f"Error: already posted {channel_link.sends_per_turn()} "
                "messages this turn.")
    from openprogram.runtime.conversation.bound_reply import flattens_tables
    if flattens_tables(session_id):
        body = channel_link.flatten_tables(body)
    result = channel_link.post_message(
        turn.channel, turn.account_id, turn.peer_id, body,
        thread_id=turn.thread_id,
    )
    if not result.ok:
        return f"Error: the {turn.channel} message was not sent ({result.error_kind})."
    return f"Posted to the {turn.channel} chat."


__all__ = ["chat_send", "_chat_send_impl", "CHANNEL_SOURCES"]
