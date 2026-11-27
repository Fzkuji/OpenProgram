"""Kigi (``$KIGI_SHARE_DIR/config.toml``, default ``~/.kigi``).

After yetone/magpie ``internal/agent/kigi.go`` (MIT License, Copyright (c)
yetone). Kigi (ELY-Open-Source/Kigi-CLI) is a community fork of Grok Build
that signs in to many vendors' plans. It keeps Grok Build's config.toml:
``[models] default`` and ``default_reasoning_effort``, and models of the
user's own as ``[model."<id>"]`` tables with ``model``, ``base_url``,
``api_key``, ``api_backend`` and ``context_window``, as Kigi's
ModelEntryConfig reads them. So it is wired as Grok Build is (``grok.py``):
one ``"openprogram/<provider>/<model>"`` table per enabled model, pointed at
the gateway with OpenProgram's own key, and the default picked here. Kigi
compacts at 85% of a model's window as Grok Build does and reads the same
``[features] campaigns`` switch over its remote campaign patches.

Its User-Agent is ``kigi/<version> (<os>; <arch>)``.
"""
from __future__ import annotations

from .base import Agent
from .grok import GrokKind, grok_like

KIGI = GrokKind(
    id="kigi", name="Kigi", aliases=("kigi-cli",), ua=("kigi",),
    env="KIGI_SHARE_DIR", dir=".kigi", proc=r"(^|/)kigi( |$)",
    notice="Kigi reads its settings at start-up — restart open kigi sessions to use this.",
)


def agent() -> Agent:
    return grok_like(KIGI)
