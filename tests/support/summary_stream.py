"""Stand-ins for the provider stream a compaction summary request reads.

A summary request reads ``openprogram.models.providers.stream_simple``
(``compaction/bounded_summary.summary_reply``), so a test that scripts the
summarizer installs a stream there.
"""
from __future__ import annotations


def summary_stream(reply):
    """A ``stream_simple`` whose only event is ``await reply(model, context, options)``.

    An exception ``reply`` raises comes out of the stream, as a provider
    error would.
    """
    async def stream(model, context, options=None):
        yield {"type": "done", "message": await reply(model, context, options)}
    return stream


def install_summary_reply(monkeypatch, reply) -> None:
    """Answer every summary request with ``reply(model, context, options)``."""
    monkeypatch.setattr("openprogram.models.providers.stream_simple", summary_stream(reply))


def sectioned(text: str = "recap") -> str:
    """A summary reply with every section the summary prompt asks for, so
    the summarizer keeps it instead of asking again."""
    from openprogram.runtime.context.compaction.prompts import SUMMARY_SECTIONS

    return "\n".join(f"## {name}\n{text}" for name in SUMMARY_SECTIONS)
