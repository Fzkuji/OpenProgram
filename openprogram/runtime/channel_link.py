"""The link between the core and the chat channels (Telegram, Discord, Slack, WeChat).

Core code sends messages to a channel conversation, reads which channel
conversations an Agent is bound to, posts into the chat of the channel
turn that is running, and sends a bound chat's replies to its channel
conversation, without importing the channels interface. The channels
package (``openprogram.interfaces.channels``) registers its implementation
with :func:`register` when it is imported; the worker, the Server and the
CLI's in-process turns import it at start.

With no channels registered every operation raises
:class:`ChannelsUnavailable`. A caller that can run without channels (a
reminder in a process that has none, an always-on report delivery) already
treats a failed send as a recorded failure, so the named error is the
outcome it reports.
"""
from __future__ import annotations

from typing import Any, Protocol, Sequence


class ChannelsUnavailable(RuntimeError):
    """A call needs the chat channels and none are registered in this process."""


class SendOutcome(Protocol):
    """What a channel send returns: success, or a failure kind and detail."""

    ok: bool
    error_kind: str
    error_detail: str
    connect_failed: bool


class ChannelTurn(Protocol):
    """The chat a running channel turn came from."""

    channel: str
    account_id: str
    peer_id: str
    #: The Slack thread or Telegram forum topic the turn is in; empty when none.
    thread_id: str


class ReplyDelivery(Protocol):
    """One assistant reply streaming out to a bound chat's channel conversation."""

    def feed(self, delta: str) -> None:
        """A piece of reply text; whole paragraphs go out as they complete."""

    def flush(self, *, response_ended: bool = True) -> None:
        """A tool call starts: send the text written before it.
        ``response_ended`` is false when the same response goes on after it."""

    def restart(self) -> None:
        """The response being streamed is void and comes again from its start."""

    def close(self) -> list[SendOutcome]:
        """Send what is queued and stop; the failed sends."""

    def finish(self, reply_text: str, *, attached_files: Sequence[str] = (),
               streamed_only: bool = False) -> list[SendOutcome]:
        """Send the rest of ``reply_text`` (``streamed_only``: only the text
        streamed here, plus the files ``reply_text`` attaches); the failed sends.
        Only the files in ``attached_files`` are uploaded."""


class ChannelLink(Protocol):
    """The operations the channels interface implements for the core."""

    def send_message(self, channel: str, account_id: str, peer_id: str, text: str) -> SendOutcome:
        """Send ``text`` to one channel conversation through the outbound path."""

    def post_message(self, channel: str, account_id: str, peer_id: str, text: str,
                     *, thread_id: str = "") -> SendOutcome:
        """Post ``text`` into a chat, or one thread of it, directly through the
        platform transport."""

    def account_ids(self, channel: str) -> list[str]:
        """The account ids configured for ``channel``."""

    def peer_bindings(self, agent_id: str) -> list[dict[str, Any]]:
        """The routing bindings that send channel conversations to ``agent_id``."""

    def active_turn(self, session_id: str) -> ChannelTurn | None:
        """The channel turn running in ``session_id`` right now, if any."""

    def take_send(self, session_id: str) -> bool:
        """Count one message the running turn posts; False once its quota is used."""

    def sends_per_turn(self) -> int:
        """How many messages one channel turn may post on its own."""

    def owner_peer(self, channel: str, account_id: str) -> str:
        """The direct-chat peer id of the account's owner, or "" (only WeChat has one)."""

    def upload_marked_files(self, channel: str, account_id: str, peer_id: str, text: str,
                            attached_files: Sequence[str]) -> str:
        """Upload the files ``text``'s markers name, if a tool recorded them; the text without markers."""

    def open_reply(self, channel: str, account_id: str, peer_id: str, *, flatten: bool = False) -> "ReplyDelivery":
        """Start sending one assistant reply of a bound chat to ``peer_id``;
        ``flatten`` writes its tables one line per row."""

    def flatten_tables(self, text: str) -> str:
        """``text`` with each Markdown table written one line per row."""


_link: ChannelLink | Any | None = None


def register(link: ChannelLink | Any) -> None:
    """Make ``link`` the channel link of this process (the last one wins)."""
    global _link
    _link = link


def unregister(link: ChannelLink | Any | None = None) -> None:
    """Drop the registered link, or only ``link`` when it is the current one."""
    global _link
    if link is None or _link is link:
        _link = None


def current() -> ChannelLink | Any | None:
    return _link


def require() -> ChannelLink | Any:
    """The registered link, or :class:`ChannelsUnavailable` when there is none."""
    if _link is None:
        raise ChannelsUnavailable(
            "no channel interface is registered; import openprogram.interfaces.channels "
            "(the worker, the Server and CLI turns do) before using chat channels."
        )
    return _link


def send_message(channel: str, account_id: str, peer_id: str, text: str) -> SendOutcome:
    return require().send_message(channel, account_id, peer_id, text)


def post_message(channel: str, account_id: str, peer_id: str, text: str,
                 *, thread_id: str = "") -> SendOutcome:
    return require().post_message(channel, account_id, peer_id, text, thread_id=thread_id)


def account_ids(channel: str) -> list[str]:
    return list(require().account_ids(channel))


def peer_bindings(agent_id: str) -> list[dict[str, Any]]:
    return list(require().peer_bindings(agent_id))


def active_turn(session_id: str) -> ChannelTurn | None:
    return require().active_turn(session_id)


def take_send(session_id: str) -> bool:
    return bool(require().take_send(session_id))


def sends_per_turn() -> int:
    return int(require().sends_per_turn())


def owner_peer(channel: str, account_id: str) -> str:
    return str(require().owner_peer(channel, account_id) or "")


def upload_marked_files(channel: str, account_id: str, peer_id: str, text: str,
                        attached_files: Sequence[str]) -> str:
    return require().upload_marked_files(channel, account_id, peer_id, text, attached_files)


def open_reply(channel: str, account_id: str, peer_id: str, *, flatten: bool = False) -> ReplyDelivery:
    return require().open_reply(channel, account_id, peer_id, flatten=flatten)


def flatten_tables(text: str) -> str:
    return str(require().flatten_tables(text))


__all__ = [
    "ChannelLink", "ChannelTurn", "ChannelsUnavailable", "ReplyDelivery", "SendOutcome",
    "register", "unregister", "current", "require",
    "send_message", "post_message", "account_ids", "peer_bindings",
    "active_turn", "take_send", "sends_per_turn", "owner_peer", "open_reply", "flatten_tables",
]
