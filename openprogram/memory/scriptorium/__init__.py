"""Markdown-workspace memory: the implementation behind MemoryProvider.

Everything in here is one way of keeping memory — files the model writes
and edits, with block IDs and evidence footnotes. The contract it plugs
into is ``openprogram/memory/provider.py``; a different memory system
implements the same contract and drops in beside this one.
"""

from .provider import ScriptoriumMemoryProvider

__all__ = ["ScriptoriumMemoryProvider"]
