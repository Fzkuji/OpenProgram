"""cache_control passthrough — a caller-marked prompt-cache breakpoint on a
content block reaches the Anthropic Messages API verbatim, with zero change
when not passed. See docs/plans/cache-control-passthrough.md.
"""
from __future__ import annotations

from types import SimpleNamespace

from openprogram.agentic_programming.runtime import _build_pi_context
from openprogram.providers.anthropic.anthropic import _build_messages
from openprogram.providers.types import ImageContent, UserMessage


# runtime carries it, anthropic preserves it (the full chain)

def test_passthrough_runtime_to_anthropic_body():
    ctx, _sys = _build_pi_context([
        {"type": "text", "text": "stable rules prefix",
         "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "dynamic part"},
    ])
    # change 2: runtime carried the marker onto the TextContent
    assert ctx.messages[0].content[0].cache_control == {"type": "ephemeral"}
    assert ctx.messages[0].content[1].cache_control is None
    # change 3: it lands on the right block in the Anthropic API body
    blocks = _build_messages(ctx, cache_control={"type": "ephemeral"})[0]["content"]
    assert blocks[0].get("cache_control") == {"type": "ephemeral"}


def test_image_cache_control_in_anthropic_body():
    ctx = SimpleNamespace(messages=[UserMessage(content=[
        ImageContent(type="image", data="AAAA", mime_type="image/png",
                     cache_control={"type": "ephemeral"}),
    ], timestamp=0)])
    block = _build_messages(ctx, cache_control=None)[0]["content"][0]
    assert block["type"] == "image"
    assert block.get("cache_control") == {"type": "ephemeral"}


# zero regression when not passed

def test_no_cache_control_body_unchanged():
    ctx, _sys = _build_pi_context([{"type": "text", "text": "hi"}])
    assert ctx.messages[0].content[0].cache_control is None
    # with no auto breakpoint either, the block is byte-identical to before
    blocks = _build_messages(ctx, cache_control=None)[0]["content"]
    assert blocks == [{"type": "text", "text": "hi"}]


def test_auto_last_block_still_applies_without_caller_breakpoint():
    ctx, _sys = _build_pi_context([{"type": "text", "text": "hi"}])
    blocks = _build_messages(ctx, cache_control={"type": "ephemeral"})[0]["content"]
    assert blocks[-1].get("cache_control") == {"type": "ephemeral"}


def test_auto_does_not_clobber_callers_last_block_marker():
    # caller marks the (only/last) block with a custom ttl — the auto
    # breakpoint must not overwrite it with the plain ephemeral one.
    ctx, _sys = _build_pi_context([
        {"type": "text", "text": "hi", "cache_control": {"type": "ephemeral", "ttl": "1h"}},
    ])
    blocks = _build_messages(ctx, cache_control={"type": "ephemeral"})[0]["content"]
    assert blocks[-1].get("cache_control") == {"type": "ephemeral", "ttl": "1h"}


def test_caller_early_breakpoint_suppresses_auto_last_block():
    # Caller marks an EARLY stable-prefix block; the provider must NOT also
    # stamp its auto breakpoint on the last (dynamic) block — that would waste
    # one of Anthropic's limited cache_control slots and shadow the prefix.
    ctx, _sys = _build_pi_context([
        {"type": "text", "text": "stable rules", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "dynamic part"},
    ])
    blocks = _build_messages(ctx, cache_control={"type": "ephemeral"})[0]["content"]
    assert blocks[0].get("cache_control") == {"type": "ephemeral"}  # caller's prefix breakpoint
    assert blocks[1].get("cache_control") is None                   # no auto breakpoint on the tail
