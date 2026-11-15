"""Send a bound chat's assistant reply to its channel conversation.

A chat tied to a channel account (the composer's channel chip, or a
conversation that arrived from the channel) gets every assistant reply
in the channel too, whatever started the turn: a message typed in the
App, an always-on wake, a scheduled job, the reply to a handoff. Turns
that receive a handoff message answer its sender instead. Turns from the
channel are answered by the inbound dispatcher (``_conversation``) and
never reach this module; ``openprogram.runtime.conversation.bound_reply``
decides which turns do.

The reply goes out exactly like an inbound turn's reply on a platform
without message edits: paragraphs of at least ``_BlockStreamer.MIN_CHARS``
as they stream, the text written before a tool call before the tool
runs, then the rest, with files handed back by ``send_file`` uploaded
as native attachments. Only the reply text is sent; the user's message,
tool progress and thinking never are. ``flatten`` (an always-on Agent's
chat) holds each table until it is complete and sends it one line per row.
"""
from __future__ import annotations

from typing import Any, Sequence


class ReplyDelivery:
    """One reply on its way to ``(channel, account_id, peer_id)``."""

    def __init__(self, channel: str, account_id: str, peer_id: str, *, flatten: bool = False) -> None:
        from openprogram.interfaces.channels._conversation import _BlockStreamer
        self.channel = channel
        self.account_id = account_id
        self.peer_id = peer_id
        self._streamer = _BlockStreamer(channel, account_id, peer_id, flatten=flatten)

    def feed(self, delta: str) -> None:
        """A piece of reply text from the stream."""
        self._streamer.feed(delta)

    def flush(self, *, response_ended: bool = True) -> None:
        """A tool call starts: send what was written before it.

        ``response_ended`` is false for a tool the provider runs itself:
        the same response goes on after it."""
        self._streamer.flush(response_ended=response_ended)

    def restart(self) -> None:
        """The response being streamed is void and is sent again from its
        start: its unsent text is dropped, and the text already sent is
        not sent a second time when the new response says it again."""
        self._streamer.restart()

    def close(self) -> list[Any]:
        """Send the queued paragraphs and stop; the failures so far."""
        self._streamer.close()
        return list(self._streamer.failures)

    def finish(self, reply_text: str, *, attached_files: Sequence[str] = (),
               streamed_only: bool = False) -> list[Any]:
        """Send the part of the reply not sent yet; every failure.

        ``streamed_only``: this attempt continues a turn that ran before
        (it paused for an approval, or a restart interrupted it), and
        everything that earlier part wrote was sent when it stopped.
        ``reply_text`` then repeats that earlier text, so only what this
        attempt streamed goes out, plus the files ``reply_text`` attaches.
        Only the files in ``attached_files`` are uploaded. Markers in the
        stream are not sent as text: the attempt that ends the turn settles
        every marker from ``reply_text``, when the recorded files are known.
        """
        from openprogram.interfaces.channels._conversation import (
            _deliver_outbound_files,
        )
        streamer = self._streamer
        streamer.close()
        body = (reply_text or "").strip()
        if streamed_only:
            from openprogram.state.attachments import find_markers, strip_markers
            rest = strip_markers(streamer.streamed[streamer.sent_upto:]).strip()
            markers = [marker for marker, _name, _path in find_markers(body)]
            unsent = "\n\n".join(part for part in [rest, *markers] if part)
        else:
            unsent = streamer.unsent_part(body) if body else ""
        if unsent.strip():
            text = _deliver_outbound_files(
                self.channel, self.account_id, self.peer_id, unsent,
                attached_files=attached_files,
            )
            if text.strip() and text != "(empty reply)":
                streamer.send(text)
        return list(streamer.failures)


def open_reply(channel: str, account_id: str, peer_id: str, *, flatten: bool = False) -> ReplyDelivery:
    return ReplyDelivery(channel, account_id, peer_id, flatten=flatten)
