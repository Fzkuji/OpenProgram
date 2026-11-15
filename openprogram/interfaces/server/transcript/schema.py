"""The wire shapes of a transcript read; TypeScript is generated from these."""
from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict, Union

RowKind = Literal[
    "user", "assistant", "function_run", "system_text", "system_event", "compaction_card",
    "agent_notice", "received_message", "spawned_root", "sub_agent_step", "external_reference", "silent",
]
RowStatus = Literal["pending", "running", "streaming", "paused", "cancelling", "done", "error", "cancelled", "interrupted"]
RowOrigin = Literal["person", "agent_delivery"]


class Block(TypedDict, total=False):
    id: str
    index: int
    type: str
    text: str
    tool: str
    tool_call_id: str
    input: str
    result: str
    is_error: bool
    outcome: str
    truncated: bool
    total_bytes: int
    # A result the agent loop bounded: how long the tool's whole output is
    # (``total_lines``, ``total_chars``) and how much the card shows
    # (``shown_chars``); "view full output" reads the saved file.
    full_output: dict[str, int]
    message_id: str
    node_id: str
    provider_executed: bool
    citations: list[Any]
    timestamp: float


class Row(TypedDict, total=False):
    """The existing ChatMsg, built on the server, plus what the projection decides."""

    id: str
    role: Literal["user", "assistant", "system"]
    rowKind: RowKind
    content: str
    status: RowStatus
    timestamp: float
    blocks: list[Block]
    kind: Literal["compaction", "snip", "event"]
    slot: Literal["card", "event"]
    coveredBy: str
    origin: RowOrigin
    clientRequestId: str
    steeringReplyId: str
    continuedBy: str
    continues: list[str]
    blockId: str | None
    attachCards: list[Row]
    runtimeChildren: list[Row]
    callRoots: list[dict[str, Any]]


class Turn(TypedDict):
    id: str
    rows: list[Row]


class Window(TypedDict):
    """The span a read returned, counted in turns of the branch."""

    head_id: str | None
    revision: int
    before: str | None
    after: str | None
    start: int
    end: int
    total: int


class TurnUpsert(TypedDict):
    kind: Literal["turn_upsert"]
    turn: Turn
    afterTurnId: str | None


class RowUpsert(TypedDict):
    kind: Literal["row_upsert"]
    turnId: str
    afterRowId: str | None
    row: Row


ItemUpsert = TypedDict("ItemUpsert", {
    "kind": Literal["item_upsert"],
    "turnId": str,
    "rowId": str,
    "list": Literal["blocks", "attachCards", "runtimeChildren", "callRoots"],
    "item": dict[str, Any],
    "index": NotRequired[int],
})


class RowPatch(TypedDict):
    kind: Literal["row_patch"]
    turnId: str
    rowId: str
    fields: dict[str, Any]


class WindowStale(TypedDict):
    kind: Literal["window_stale"]
    head_id: str | None
    reason: Literal["branch", "structure"]


TranscriptChange = Union[TurnUpsert, RowUpsert, ItemUpsert, RowPatch, WindowStale]


class TranscriptUpdate(TypedDict):
    """A numbered change to one view; ``previous`` is the revision it applies on."""

    session_id: str
    view: str
    previous: int
    revision: int
    changes: list[TranscriptChange]
