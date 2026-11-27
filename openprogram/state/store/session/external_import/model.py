"""The source-neutral shape every external transcript parses into.

A parser reads one Claude Code or Codex transcript file and returns an
:class:`ExternalSession`: the conversation as user turns, each holding
the assistant's reply as ordered blocks (text / thinking / tool). The
writer turns that into the same DAG a native turn produces, so nothing
downstream needs to know where a conversation came from.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

CLAUDE_CODE = "claude-code"
CODEX = "codex"
DEEPSEEK_HARNESS = "deepseek-harness"
#: The agents ``openprogram sessions import`` reads; DeepSeek Harness
#: conversations come in only by the app sync (``app_sync``).
AGENTS = (CLAUDE_CODE, CODEX)

AGENT_LABELS = {CLAUDE_CODE: "Claude Code", CODEX: "Codex", DEEPSEEK_HARNESS: "DeepSeek Harness"}


@dataclass
class ToolBlock:
    call_id: str
    name: str
    arguments: Any
    result: Optional[str] = None
    is_error: bool = False
    timestamp: Optional[float] = None


@dataclass
class Turn:
    """One user message and everything the agent did in answer to it."""
    user_text: str
    timestamp: Optional[float] = None
    # Ordered reply: {"type": "text"|"thinking", "text": str} dicts and
    # ToolBlock instances, in emission order.
    blocks: list[Any] = field(default_factory=list)
    model: str = ""
    ended_at: Optional[float] = None
    # The user line was a slash command (``/review …``), not prose.
    command: bool = False
    # Fingerprint of the turn as read, kept when its text is redacted so
    # the turn's identity does not depend on what redaction removed.
    source_fingerprint: Optional[str] = None
    # The agent's own id of the turn this message belongs to (Codex's
    # ``turn_id``), when the transcript records one.
    turn_id: Optional[str] = None
    # The pictures the message carried, in order: (base64 bytes, mime type, the
    # placeholder the text holds for it: ``[image]``, Codex's ``[Image #1]``; None
    # for one that follows the picture before it). No bytes: the transcript lacks them.
    images: list[tuple[str, str, Optional[str]]] = field(default_factory=list)

    def reply_text(self) -> str:
        parts = [b["text"].strip() for b in self.blocks
                 if isinstance(b, dict) and b.get("type") == "text"]
        return "\n\n".join(p for p in parts if p)

    def tools(self) -> list[ToolBlock]:
        return [b for b in self.blocks if isinstance(b, ToolBlock)]

    def digest(self) -> str:
        """Changes whenever the reply grows: more text, a call, a result."""
        h = hashlib.sha256()
        for b in self.blocks:
            if isinstance(b, ToolBlock):
                h.update(f"tool\0{b.call_id}\0{b.result is not None}\0{b.is_error}".encode())
            else:
                h.update(f"{b.get('type')}\0{b.get('text')}".encode("utf-8"))
        return h.hexdigest()[:16]

    def fingerprint(self) -> str:
        """Stable identity of this turn, for detecting a grown source."""
        if self.source_fingerprint:
            return self.source_fingerprint
        key = f"{self.timestamp or 0:.3f}\0{self.user_text}"
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


@dataclass
class ExternalSession:
    agent: str
    source_id: str
    path: Path
    cwd: Optional[str] = None
    title: str = ""
    turns: list[Turn] = field(default_factory=list)
    model: str = ""
    #: The provider the agent ran ``model`` on, where the agent names one.
    model_provider: str = ""

    @property
    def started_at(self) -> Optional[float]:
        return next((t.timestamp for t in self.turns if t.timestamp), None)

    @property
    def updated_at(self) -> Optional[float]:
        for t in reversed(self.turns):
            if t.ended_at or t.timestamp:
                return t.ended_at or t.timestamp
        return None


@dataclass
class Candidate:
    """A transcript found on disk, summarised without a full parse."""
    agent: str
    source_id: str
    path: Path
    cwd: Optional[str]
    title: str
    updated_at: float
    size: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "source_id": self.source_id,
            "path": str(self.path),
            "cwd": self.cwd,
            "title": self.title,
            "updated_at": self.updated_at,
            "size": self.size,
        }
