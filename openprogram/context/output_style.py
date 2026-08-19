"""Output styles: a named block of system-prompt text that shapes how the
model writes its replies.

A style is just markdown. Built-in styles live in ``BUILTIN_STYLES``; user
styles are ``<name>.md`` files discovered the same way skills are — user dir
first, project dir overriding it:

    1. Built-in    — :data:`BUILTIN_STYLES`
    2. User        — ``~/.openprogram/output-styles/<name>.md``
    3. Project     — ``<cwd>/output-styles/<name>.md``

Later sources win on a name collision, so a user file named ``concise``
replaces the built-in of that name. The file's stem is the style name; its
body (frontmatter stripped) is the text appended to the system prompt.

``default`` is the empty style: it contributes nothing, which is exactly the
behaviour before output styles existed.

The active style is a global preference (``agent.output_style`` in
config.json), like the theme and thinking effort — the assembler receives an
agent profile, not a session handle, so a per-session lookup would need its
own plumbing through every call site.
"""
from __future__ import annotations

import os
from pathlib import Path

from openprogram.paths import get_state_dir
from openprogram.skills import frontmatter as _h

DEFAULT_STYLE = "default"

# Built-in styles. Neutral and professional — these describe output shape,
# not a character. ``default`` is absent on purpose: it means "add nothing".
BUILTIN_STYLES: dict[str, str] = {
    "concise": (
        "## Output style: concise\n\n"
        "Answer in as few words as the question allows. Lead with the result, "
        "then only the detail needed to act on it. Prefer a short sentence or a "
        "small list over a paragraph. Omit preamble, restatement of the "
        "question, and closing summaries."
    ),
    "explanatory": (
        "## Output style: explanatory\n\n"
        "Give the answer, then explain the reasoning behind it. Name the "
        "trade-offs considered and why the chosen approach wins. When touching "
        "code, say what the surrounding code does and how the change fits it. "
        "Depth serves understanding, so skip explanation the reader plainly "
        "already has."
    ),
    "direct": (
        "## Output style: direct\n\n"
        "State conclusions without hedging. Say what is true and what to do. "
        "Skip caveats that do not change the decision. Disagree plainly when "
        "the premise of a question is wrong, and give the correction."
    ),
    "detailed": (
        "## Output style: detailed\n\n"
        "Cover the task thoroughly. Include edge cases, failure modes, and the "
        "assumptions the answer rests on. Structure longer answers with "
        "headings or lists so the reader can navigate them. Completeness is "
        "the goal, but every sentence still has to carry information."
    ),
}


def user_dir() -> Path:
    return get_state_dir() / "output-styles"


def project_dir(cwd: str | os.PathLike | None = None) -> Path:
    return Path(cwd or os.getcwd()) / "output-styles"


def _read_style_file(path: Path) -> str | None:
    """Return the style body, or None if the file is unreadable/empty."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    _, body = _h.parse_frontmatter(text)
    body = body.strip()
    return body or None


def list_styles(cwd: str | os.PathLike | None = None) -> dict[str, str]:
    """Every available style name mapped to its prompt text.

    Built-ins first, then user and project ``*.md`` files overriding them.
    ``default`` is always present and always empty.
    """
    styles: dict[str, str] = {DEFAULT_STYLE: "", **BUILTIN_STYLES}
    for root in (user_dir(), project_dir(cwd)):
        try:
            if not root.is_dir():
                continue
            found = sorted(root.glob("*.md"))
        except OSError:
            continue
        for md in found:
            body = _read_style_file(md)
            if body:
                styles[md.stem] = body
    return styles


def get_active_style() -> str:
    """Name of the configured style. Unset or unknown ⇒ ``default``."""
    from openprogram.setup import _read_config
    try:
        cfg = _read_config()
    except Exception:
        return DEFAULT_STYLE
    agent = cfg.get("agent")
    name = agent.get("output_style") if isinstance(agent, dict) else None
    return str(name).strip() if name and str(name).strip() else DEFAULT_STYLE


def style_text(name: str | None = None, cwd: str | os.PathLike | None = None) -> str:
    """Prompt text for ``name`` (default: the active style).

    An unknown name resolves to ``""`` rather than raising — a stale config
    entry must not break every turn.
    """
    resolved = name or get_active_style()
    if resolved == DEFAULT_STYLE:
        return ""
    return list_styles(cwd).get(resolved, "")


__all__ = [
    "BUILTIN_STYLES",
    "DEFAULT_STYLE",
    "get_active_style",
    "list_styles",
    "project_dir",
    "style_text",
    "user_dir",
]
