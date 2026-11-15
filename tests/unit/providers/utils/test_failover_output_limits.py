"""Failover candidates recompute output limits independently."""
from __future__ import annotations

import asyncio

from openprogram.runtime.context.compaction.request import RequestCompactor
from openprogram.models.providers import SimpleStreamOptions
from openprogram.models.providers.types import Context, Model


def _model(mid: str, *, window: int, max_tokens: int) -> Model:
    return Model(
        id=mid,
        name=mid,
        api="openai-completions",
        provider="test",
        base_url="https://example.invalid",
        context_window=window,
        max_tokens=max_tokens,
    )


def test_prepare_assigns_per_candidate_output_cap():
    async def run():
        primary = _model("primary", window=32_000, max_tokens=8_000)
        fallback = _model("fallback", window=128_000, max_tokens=64_000)
        context = Context(system_prompt="", messages=[], tools=[])
        compactor = RequestCompactor()

        opts_a = SimpleStreamOptions(max_tokens=None)
        await compactor.prepare(context, primary, opts_a)
        assert opts_a.max_tokens == 8_000

        # Explicit reset like agent_loop.snapshot_stream before the next candidate.
        opts_b = opts_a.model_copy(update={"max_tokens": None})
        await compactor.prepare(context, fallback, opts_b)
        assert opts_b.max_tokens == 64_000
        assert opts_a.max_tokens == 8_000

        # Explicit user cap must survive both prepares.
        opts_c = SimpleStreamOptions(max_tokens=1200)
        await compactor.prepare(context, primary, opts_c)
        assert opts_c.max_tokens == 1200
        opts_d = opts_c.model_copy(update={"max_tokens": 1200})
        await compactor.prepare(context, fallback, opts_d)
        assert opts_d.max_tokens == 1200

    asyncio.run(run())
