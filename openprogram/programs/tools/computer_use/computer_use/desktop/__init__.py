"""Desktop: read and operate application windows of this computer.

The ``app`` target (``providers/desktop.py``) drives these platform
backends; this package holds them and the pure helpers (key chord
parsing, Accessibility tree rendering, numbering and diffing) they share.
"""
from .keys import Chord, KeyNameError, modifier_flags, parse_chord
from .tree import Node, Snapshot, diff, number, render, signature, snapshot

__all__ = ["Chord", "KeyNameError", "Node", "Snapshot", "diff", "modifier_flags", "number", "parse_chord",
           "render", "signature", "snapshot"]
