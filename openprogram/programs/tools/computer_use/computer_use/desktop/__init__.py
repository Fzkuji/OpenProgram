"""Desktop: read and operate application windows of this computer.

The ``window`` target (``providers/window/``) drives these backends, one
package per platform (``macos/``, ``windows/``, ``linux/``), each a
``computer.py`` composed of one module per concern. ``backends.py`` picks
the one for this host; ``common/`` holds what they share (results and
errors, key chords, the element tree, OCR, audio).
"""
from .common.keys import Chord, KeyNameError, modifier_flags, parse_chord
from .common.tree import Node, Snapshot, diff, number, render, signature, snapshot

__all__ = ["Chord", "KeyNameError", "Node", "Snapshot", "diff", "modifier_flags", "number", "parse_chord",
           "render", "signature", "snapshot"]
