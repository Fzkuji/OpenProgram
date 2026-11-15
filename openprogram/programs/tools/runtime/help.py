"""The ``help`` tool: read a tool guide by name."""
from __future__ import annotations

from openprogram.programs._authoring import guides
from openprogram.programs._host.runtime import function

_DESC = (
    "Read a tool's instructions. Tools with more rules than their schema "
    "shows list guide names in their results (`help(\"self_update.prepare\")`); "
    "pass that name, or a tool name for its overview. A call that needs a "
    "guide is refused until the guide is in view, and the refusal carries it."
)


@function(name="help", description=_DESC, toolset=["core"])
def guide_help(name: str = "") -> str:
    """Return a guide's text.

    Args:
        name: A guide name from a tool result, or a tool name.
    """
    query = (name or "").strip()
    if not query:
        return "[help error] pass a guide or tool name; guides: " + ", ".join(guides.names())
    try:
        return guides.help_text(query)
    except LookupError as exc:
        return f"[help error] {exc}"
