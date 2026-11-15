"""Clack-style terminal UI primitives.

Ported from ``@clack/prompts`` (OpenClaw / create-svelte / astro use the
same visual language). Pure stdout printing — no TTY detection, no
color codes — so output stays readable in logs and CI. Callers that
want color can wrap.

The core shape we render is a *note box*::

    ◇  OpenAI Codex OAuth ─────────────────────────────────────────────╮
    │                                                                  │
    │  Browser will open for OpenAI authentication.                    │
    │  If the callback doesn't auto-complete, paste the redirect URL.  │
    │  OpenAI OAuth uses localhost:1455 for the callback.              │
    │                                                                  │
    ├──────────────────────────────────────────────────────────────────╯

Width is driven by the longest line (title or body). Box chars are
Unicode; assumes the user's terminal can render them (every default
macOS / Linux / Windows Terminal font does).
"""
from __future__ import annotations


# Box-drawing glyphs — matched to @clack/prompts defaults.
_GLYPH_NOTE = "◇"
_V = "│"
_H = "─"
_TR = "╮"
_BL = "├"
_BR = "╯"


def note(title: str, body: str | list[str], *, glyph: str = _GLYPH_NOTE) -> str:
    """Render a clack-style note box as a string. Caller prints it.

    ``body`` can be a single string (``\\n``-separated) or a list of
    lines. Leading/trailing blank lines are trimmed and replaced with
    the standard 1-row top/bottom padding clack uses.
    """
    if isinstance(body, str):
        lines = body.splitlines()
    else:
        lines = list(body)
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()

    width = max([len(l) for l in lines] + [len(title)] + [1])

    # Top: "◇  <title> " + horizontal fill + "╮". Compare against the
    # side lines to land the right edge on the same column: a side
    # line spans `│  <content pad to width>  │` = width + 6 cols.
    top_prefix = f"{glyph}  {title} "
    top = top_prefix + _H * (width + 6 - len(top_prefix) - 1) + _TR

    padding_row = f"{_V}  {'':<{width}}  {_V}"
    body_rows = [padding_row]
    for line in lines:
        body_rows.append(f"{_V}  {line:<{width}}  {_V}")
    body_rows.append(padding_row)

    bottom = _BL + _H * (width + 4) + _BR

    return "\n".join([top, *body_rows, bottom])


def print_note(title: str, body: str | list[str], *, glyph: str = _GLYPH_NOTE) -> None:
    """Convenience: render and print a note box with a trailing blank line."""
    print(note(title, body, glyph=glyph))
    print()
