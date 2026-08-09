"""Persistent, machine-wide memory for OpenProgram agents.

Memory is a workspace of Markdown files the model writes and edits
directly. Three layers, all readable in an editor and diffable in Git:

1. **Sources** (``sources/<provider>/<thread>.md``)
       The append-only evidence record: what was actually said, archived
       verbatim. Written only by the runtime, never edited.

2. **Topics** (``topics/**/*.md``)
       The curated semantic memory — one file per person, project or
       recurring theme. Every paragraph carries a stable ``^block-id``
       and a footnote citing the source it rests on, so any claim can be
       traced back to the message it came from. This is the layer the
       model edits.

3. **Core** (``core.md``)
       A small always-on block injected into every session's system
       prompt.

``timeline/``, ``recent_events.jsonl`` and ``relations.json`` are derived
views, rebuilt by the runtime after every successful write. Editing them
by hand accomplishes nothing.

Writing runs in the background rather than in the conversation: turns
accumulate and are written once there is enough to be worth a model call,
and reorganisation follows once enough has landed. See ``provider.py``
for the lifecycle hooks the agent runtime calls, and ``scheduler.py`` for
the nightly maintenance sweep.

Storage location: ``<state>/memory/`` (profile-global by default —
shared across every agent and conversation on the machine).
"""

from .management import MemoryConfig, MemoryWorkspace
from .workspace_layout import RUNTIME_DIR, runtime_dir

__all__ = [
    "MemoryConfig",
    "MemoryWorkspace",
    "RUNTIME_DIR",
    "runtime_dir",
]
