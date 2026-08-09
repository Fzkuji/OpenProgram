"""Turn conversation into memory, once there is enough of it.

Folding turns into topic files costs a model call, so it waits until a
batch has gathered — roughly sixteen thousand tokens of conversation,
rather than a call per turn.

The conversation is read back from the session store rather than
accumulated in memory. That store is durable and ordered, so a turn's
identity survives a worker restart, and the cursor in ``runtime/online``
can tell what has already been written without keeping any state of its
own here. The alternative, buffering turns in a module-level dict, loses
the buffer on restart and hands out positions that change between runs —
which is exactly what a cursor cannot tolerate.
"""

from __future__ import annotations

import logging
from typing import Any

from ..management import organize_topics
from ..management.agent import _run_agent
from ..management.api import render_writer_task
from ..management.transaction import TransactionError, workspace_write_lock
from ..runtime.online import OnlineMemoryRuntime
from ..runtime.state import SourceRecord

logger = logging.getLogger(__name__)

PROVIDER = "openprogram"


def _agent(model: str | None = None) -> Any:
    """A writer that runs on the user's own login.

    Memory is written on the user's behalf, out of the quota they are
    already paying for, so it asks for no separate credential and no
    separate model.
    """
    from ..agent_runtime import ClaudeCodeAgent, ClaudeCodeConfig

    return ClaudeCodeAgent(ClaudeCodeConfig.inherited(model=model))


def _counter() -> Any:
    from ..runtime.tokenization import TokenCounter

    return TokenCounter.resolve(requested_model="claude").count


def _text_of(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(part for part in parts if part).strip()
    return ""


def _records(
    session_id: str, messages: list[dict[str, Any]]
) -> list[SourceRecord]:
    """Conversation turns as evidence, in the order the session holds them.

    Only what a person said and what the assistant replied. Tool calls
    and their results are the machinery of a turn, not its content, and
    recording them would bury the conversation in file listings.
    """
    rows: list[SourceRecord] = []
    for index, message in enumerate(messages):
        role = message.get("role")
        if role not in ("user", "assistant"):
            continue
        text = _text_of(message)
        if not text:
            continue
        rows.append(SourceRecord(
            provider=PROVIDER,
            thread_id=session_id or "session",
            message_id=str(message.get("id") or f"m{index:06d}"),
            ordinal=index,
            role=role,
            content=text,
            timestamp=str(message.get("timestamp") or "") or None,
        ))
    return rows


def _first_batch(
    records: list[SourceRecord], counter: Any, threshold: int
) -> list[SourceRecord]:
    """The leading turns that together reach the threshold.

    The threshold says when writing is worth doing, not how much to write
    at once. A session running all day arrives with far more backlog than
    one model call can hold.
    """
    total = 0
    for index, record in enumerate(records):
        total += counter(record.content)
        if total >= threshold:
            return records[:index + 1]
    return records


def write_session(
    session_id: str,
    messages: list[dict[str, Any]],
    *,
    token_threshold: int,
    force: bool = False,
    model: str | None = None,
) -> bool:
    """Write a session's unwritten turns. True when something was written.

    Returns False without calling a model when the session holds nothing
    new, when the batch is below the threshold, or when another writer
    holds the workspace.
    """
    from .. import store

    records = _records(session_id, messages)
    if not records:
        return False

    root = store.ensure()
    counter = _counter()
    runtime = OnlineMemoryRuntime(
        root, token_counter=counter, token_threshold=token_threshold
    )
    pending = runtime.pending(records)
    if not pending:
        return False
    pending = _first_batch(pending, counter, token_threshold)

    agent = _agent(model)

    def writer(space: Any, batch: tuple[SourceRecord, ...]) -> None:
        observed = next(
            (
                record.timestamp[:10]
                for record in reversed(batch) if record.timestamp
            ),
            "undated",
        )
        _run_agent(
            space.memory_dir,
            agent=agent,
            task=render_writer_task([{
                "observation_date": observed,
                "turns": [(r.role, r.content) for r in batch],
                "refs": [r.source_id for r in batch],
            }]),
            stage="write",
        )

    def organizer(space: Any) -> None:
        organize_topics(space.memory_dir, agent=agent)

    try:
        # Short wait: a chat session writing right now is ordinary, and
        # the next turn brings this back around. Nothing should queue
        # behind background maintenance.
        with workspace_write_lock(root, timeout_s=1.0):
            return runtime.process(
                pending,
                writer,
                local_manager=organizer,
                global_manager=organizer,
                force=force,
            )
    except TransactionError as exc:
        if exc.code == "CONCURRENT_UPDATE":
            logger.debug("memory busy; leaving this batch for the next turn")
            return False
        raise


def _branch(session_id: str) -> list[dict[str, Any]]:
    from openprogram.agent.session_db import default_db

    return default_db().get_branch(session_id) or []


def record_turn(session_id: str, *, token_threshold: int) -> bool:
    """Called after a finished turn. Writes only when enough has gathered."""
    if not session_id:
        return False
    return write_session(
        session_id, _branch(session_id), token_threshold=token_threshold
    )


def flush(session_id: str, messages: list[dict[str, Any]] | None = None) -> bool:
    """Write what a session has left, however little it is.

    The threshold keeps short exchanges from each costing a call. Once a
    session is over there is no later batch to join, so the remainder
    goes in regardless of size.
    """
    if not session_id:
        return False
    rows = messages if messages is not None else _branch(session_id)
    return write_session(
        session_id, rows, token_threshold=1, force=True
    )


def sweep(*, model: str | None = None) -> dict[str, Any]:
    """Reorganise every topic file. Called by the nightly scheduler.

    Writing only ever makes files longer; nothing shortens them. Left
    alone a workspace becomes one enormous file per subject with its
    timeline cut into pieces by topic, which is the shape that makes
    ordering and counting questions unanswerable.
    """
    from .. import store

    root = store.ensure()
    topics = list((root / "topics").rglob("*.md"))
    if not topics:
        return {"status": "empty", "topics": 0}
    try:
        with workspace_write_lock(root, timeout_s=5.0):
            organize_topics(root, agent=_agent(model))
    except TransactionError as exc:
        if exc.code == "CONCURRENT_UPDATE":
            return {"status": "busy", "topics": len(topics)}
        raise
    return {"status": "ok", "topics": len(topics)}
