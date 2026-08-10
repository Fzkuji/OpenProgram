"""Turn conversation into memory, once there is enough of it.

Folding turns into topic files costs a model call, so it waits until a
batch has gathered — roughly sixteen thousand tokens of conversation,
rather than a call per turn.

The conversation is read back from the session store rather than
accumulated in memory. That store is durable and ordered, and the source
nodes themselves record which memory workspace has written them.
"""

from __future__ import annotations

import hashlib
import logging
from contextlib import closing
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from ..provider import WriteFailure
from .management import organize_topics
from .management.agent import _run_agent
from .management.api import render_writer_task
from .management.transaction import TransactionError, workspace_write_lock
from .runtime.online import OnlineMemoryRuntime
from .runtime.state import SourceRecord

logger = logging.getLogger(__name__)

PROVIDER = "openprogram"
WRITTEN_NODE_MARKER = "memory_written_scriptorium"

# Turns the runtime writes to drive itself: the notification a finished
# sub-agent posts back, and the prompt a branch merge assembles.
RUNTIME_SOURCES = frozenset({"task_followup", "merge_turn"})


def _agent(model: str | None = None) -> Any:
    """A detached writer on the configured chat-agent provider stack."""
    from openprogram.setup import _read_config
    from .agent_runtime import OpenProgramAgent

    configured = (((_read_config().get("memory") or {}).get("writer") or {})
                  .get("model"))
    override = model or (str(configured).strip() if configured else None)
    return OpenProgramAgent(model=override)


def _counter() -> Any:
    from .runtime.tokenization import TokenCounter

    return TokenCounter.resolve(requested_model="claude").count


def _text_of(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(part for part in parts if part)
    return ""


def _is_runtime_turn(message: dict[str, Any]) -> bool:
    """A turn the runtime scheduled, not one anybody said.

    A sub-agent's completion notice and a merge prompt are written as
    user-role rows so the model has something to answer, and the chat
    marks them ``display="runtime"`` rather than drawing them as the
    user talking (dag/overview.md). The reply carries the same
    ``source`` as the trigger, so this covers both halves of the turn.
    """
    return (
        message.get("display") == "runtime"
        or message.get("source") in RUNTIME_SOURCES
    )


def _observed_at(value: Any) -> str | None:
    """A session row's stamp, as the ISO 8601 the memory layer stores.

    The session store keeps Unix seconds; everything downstream of a
    ``SourceRecord`` wants a calendar time — the writer's observation
    date is this string's first ten characters, the source archive
    prints it beside the turn, and ``runtime/online`` parses it. So the
    conversion belongs here, at the boundary between the two.

    In the machine's own zone, offset included: the date a claim is
    filed under should be the date the user would name, and an offset
    keeps it comparable with an aware ``now``.
    """
    if value is None or value == "":
        return None
    try:
        return datetime.fromtimestamp(
            float(value), timezone.utc
        ).astimezone().isoformat()
    except (TypeError, ValueError):
        # Already a written date — ``archive_sessions`` builds records
        # that way — so pass it through rather than mangling it.
        return str(value).strip() or None


def _records(
    session_id: str, messages: list[dict[str, Any]]
) -> list[SourceRecord]:
    """Conversation turns as evidence, in the order the session holds them.

    Only what a person said and what the assistant replied. Tool calls
    and their results are the machinery of a turn, not its content, and
    recording them would bury the conversation in file listings; the
    runtime's own scheduling turns are machinery for the same reason.

    The ordinal retains source ordering for the append-only archive. A
    record's identity is always the session node's own stable ID.
    """
    rows: list[SourceRecord] = []
    for index, message in enumerate(messages):
        role = message.get("role")
        if role not in ("user", "assistant"):
            continue
        if _is_runtime_turn(message):
            continue
        text = _text_of(message)
        if not text.strip():
            continue
        message_id = str(message.get("id") or "").strip()
        if not message_id:
            raise ValueError("memory source message requires a stable id")
        from openprogram.agent.authority import (
            has_capability,
            normalize_authority,
        )

        authority = normalize_authority(message)
        if authority and not has_capability(
            authority, "memory.source.append"
        ):
            continue
        rows.append(SourceRecord(
            provider=PROVIDER,
            thread_id=session_id or "session",
            message_id=message_id,
            ordinal=index,
            role=role,
            content=text,
            timestamp=_observed_at(message.get("timestamp")),
            speaker_id=authority.get("speaker_id", message.get("speaker_id")),
            speaker_display=authority.get(
                "speaker_display", message.get("speaker_display")
            ),
            speaker_kind=authority.get("speaker_kind", "unknown"),
            principal_id=authority.get("principal_id", "unknown"),
            authority_tier=authority.get("authority_tier"),
            trust_state="trusted",
        ))
    return rows


def archive_unpaired_group_message(
    *,
    channel: str,
    account_id: str,
    chat_id: str,
    message_id: str,
    user_id: str,
    user_display: str,
    text: str,
    timestamp: float = 0.0,
) -> str:
    """Archive denied group speech as pending evidence, without an agent turn."""
    from .. import is_enabled, store
    from .management import MemoryWorkspace

    if not is_enabled():
        return ""

    native_id = str(message_id or "").strip()
    seed = "\x1f".join((
        str(channel), str(account_id), str(chat_id), native_id,
        str(user_id), str(timestamp), str(text),
    ))
    record = SourceRecord(
        provider="channel-" + quote(str(channel), safe="-_."),
        thread_id=quote(
            f"{account_id}:{chat_id}", safe="-_.",
        ),
        message_id=hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24],
        ordinal=int(float(timestamp or 0) * 1000),
        role="user",
        content=str(text),
        timestamp=_observed_at(timestamp) if timestamp else None,
        speaker_id=str(user_id or "") or None,
        speaker_display=str(user_display or "") or None,
        speaker_kind="human",
        principal_id="unknown",
        authority_tier=None,
        trust_state="pending",
    )
    root = store.ensure()
    with workspace_write_lock(root, timeout_s=1.0):
        with closing(MemoryWorkspace(root)) as workspace:
            workspace.archive_source_records([record])
    return record.source_id


def _marked_ids(
    messages: list[dict[str, Any]], workspace_id: str,
) -> set[str]:
    return {
        str(message.get("id"))
        for message in messages
        if message.get("id") and message.get(WRITTEN_NODE_MARKER) == workspace_id
    }


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
    new, or when the batch is below the threshold. Both are ordinary.
    """
    from .. import store

    records = _records(session_id, messages)
    if not records:
        return False

    root = store.ensure()
    workspace_id = store.workspace_id()
    from openprogram.agent.session_db import default_db
    db = default_db()
    counter = _counter()
    runtime = OnlineMemoryRuntime(
        root, token_counter=counter, token_threshold=token_threshold
    )
    marked_ids = _marked_ids(messages, workspace_id)
    pending = runtime.pending(records, marked_ids)
    if not pending:
        return False
    pending = _first_batch(pending, counter, token_threshold)

    agent = None

    def get_agent():
        nonlocal agent
        if agent is None:
            agent = _agent(model)
        return agent

    def writer(space: Any, batch: tuple[SourceRecord, ...]) -> list[str]:
        observed = next(
            (
                record.timestamp[:10]
                for record in reversed(batch) if record.timestamp
            ),
            "undated",
        )
        audit = _run_agent(
            space.memory_dir,
            agent=get_agent(),
            task=render_writer_task([{
                "observation_date": observed,
                "turns": [(r.speaker_label, r.content) for r in batch],
                "refs": [r.source_id for r in batch],
            }]),
            stage="write",
        )
        return _changed_files(audit)

    def mark(batch: tuple[SourceRecord, ...]) -> None:
        db.merge_node_metadata_batch(session_id, {
            record.message_id: {WRITTEN_NODE_MARKER: workspace_id}
            for record in batch
        })

    def organizer(space: Any) -> None:
        organize_topics(space.memory_dir, agent=get_agent())

    # Short wait: a chat session writing right now is ordinary, and the
    # next turn brings this back around. The busy workspace raises
    # rather than becoming a bare False, because a caller that cannot
    # tell "not enough to write yet" from "somebody else holds the
    # lock" has nothing truthful to report about either.
    with workspace_write_lock(root, timeout_s=1.0):
        return runtime.process(
            pending,
            writer,
            marked_ids=marked_ids,
            mark=mark,
            local_manager=organizer,
            global_manager=organizer,
            force=force,
        )


def _branch(session_id: str) -> list[dict[str, Any]]:
    from openprogram.agent.session_db import default_db

    return default_db().get_branch(session_id) or []


def _pending(
    session_id: str, messages: list[dict[str, Any]]
) -> list[SourceRecord]:
    """What this session still owes memory."""
    from .. import store

    records = _records(session_id, messages)
    if not records:
        return []
    workspace_id = store.workspace_id()
    return OnlineMemoryRuntime(
        store.ensure(), token_counter=_counter()
    ).pending(records, _marked_ids(messages, workspace_id))


def _force_branches(
    session_id: str,
    fallback: list[dict[str, Any]] | None,
) -> tuple[Any, list[tuple[str | None, list[dict[str, Any]]]]]:
    """Current head path first, followed by the other live tip paths."""
    from openprogram.agent.session_db import default_db

    db = default_db()
    session = db.get_session(session_id)
    current = (session or {}).get("head_id")
    heads: list[str] = [current] if current else []
    for branch in db.list_branches(session_id):
        head = branch.get("head_msg_id")
        if head and not branch.get("archived") and head not in heads:
            heads.append(head)
    branches = [
        (head, db.get_branch(session_id, head) or []) for head in heads
    ]
    if not branches and fallback is not None:
        branches.append((None, fallback))
    return db, branches


def write(
    session_id: str,
    messages: list[dict[str, Any]] | None = None,
    *,
    token_threshold: int,
    force: bool = False,
) -> WriteFailure | None:
    """Fold a session's conversation into memory. Nothing back on success.

    Unforced, this is the after-every-turn call: one pass, which writes
    only if the session has crossed the threshold. Having written
    nothing because there is not yet enough is the ordinary outcome and
    not something to report.

    Forced, it is the session-boundary call, and it gets written however
    little there is: the threshold keeps short exchanges from each
    costing a call, and once a session is over there is no later batch
    to join. The threshold still bounds one call's worth, so a day-long
    session takes several passes — hence the loop. Stopping after the
    first pass would strand the rest for good, because the caller marks
    the session done on the way out.

    A ``WriteFailure`` means turns are still unwritten. Whatever
    ``write_session`` raises travels up to the provider, which is where
    a transaction code becomes retryable or not.
    """
    from .. import is_enabled

    if not is_enabled():
        return None
    if not session_id:
        return WriteFailure("no session id", retryable=False)
    from .. import store
    from openprogram.agent.session_db import default_db
    from .runtime.mark_archived_turns import migrate

    root = store.ensure()
    migrate(root, default_db(), store.workspace_id())
    rows = messages if messages is not None else _branch(session_id)
    if not force:
        write_session(session_id, rows, token_threshold=token_threshold)
        return None
    db, branches = _force_branches(session_id, messages)
    for branch_index, (head, branch_rows) in enumerate(branches):
        if head is not None:
            branch_rows = db.get_branch(session_id, head) or []
        while True:
            pending = _pending(session_id, branch_rows)
            if not pending:
                break
            force_branch = branch_index == 0
            counter = _counter()
            if not force_branch and sum(
                counter(record.content) for record in pending
            ) < token_threshold:
                break
            if not write_session(
                session_id,
                branch_rows,
                token_threshold=token_threshold,
                force=force_branch,
            ):
                # Pending turns reached no topic file, so no source node
                # was marked and a later pass must retry this branch.
                return WriteFailure("the writer made no progress")
            if head is None:
                return WriteFailure("session nodes unavailable")
            branch_rows = db.get_branch(session_id, head) or []
    return None


def _changed_files(audit: list[dict[str, Any]]) -> list[str]:
    """The topic files an agent run committed a change to."""
    return sorted({
        path
        for entry in audit
        if entry.get("tool") == "commit" and entry.get("status") == "ok"
        for path in entry.get("topic_paths") or []
    })


def distill_promoted_source(
    memory_dir: Any,
    source_id: str,
    *,
    model: str | None = None,
) -> list[str] | None:
    """Write one newly trusted archived source into Topics.

    ``None`` means an existing Topic already cites the source. An empty list
    means the writer ran but made no accepted edit, so a later explicit
    promotion can retry without duplicating an already cited source.
    """
    from pathlib import Path

    from .markdown import parse_topic_tree
    from .retrieval.bm25 import parse_source_file
    from .source_format import provider_source_location

    root = Path(memory_dir).resolve()
    with workspace_write_lock(root, timeout_s=5.0):
        if any(
            source_id in unit.source_refs
            for unit in parse_topic_tree(root / "topics")
        ):
            return None
        location = provider_source_location(source_id, v2=True)
        if location is None or not (root / location[0]).is_file():
            raise ValueError(f"source not found: {source_id}")
        event = next(
            (
                row
                for row in parse_source_file(root / location[0], root / "sources")
                if row.event_id == source_id
            ),
            None,
        )
        if event is None or event.trust_state != "trusted":
            raise ValueError(f"source is not trusted: {source_id}")
        audit = _run_agent(
            root,
            agent=_agent(model),
            task=render_writer_task([{
                "observation_date": event.date or "undated",
                "turns": [(
                    event.speaker_label
                    or event.speaker_display
                    or event.speaker_id
                    or "user",
                    event.content,
                )],
                "refs": [source_id],
            }]),
            stage="promote",
        )
    return _changed_files(audit)


def reorganize(*, model: str | None = None) -> dict[str, Any]:
    """Rewrite every topic file. Called by the nightly scheduler.

    Writing only ever makes files longer; nothing shortens them. Left
    alone a workspace becomes one enormous file per subject with its
    timeline cut into pieces by topic, which is the shape that makes
    ordering and counting questions unanswerable.

    ``changed_files`` is what this pass actually rewrote. What to
    rearrange is the model's judgment, and a model that judges there is
    nothing to do does nothing, silently and correctly under its own
    criterion. An empty list is what makes that visible, and ``topics``
    beside it counts the files the pass looked at.
    """
    from .. import is_enabled, store

    if not is_enabled():
        return {"status": "disabled"}

    root = store.ensure()
    topics = list((root / "topics").rglob("*.md"))
    if not topics:
        return {"status": "empty", "topics": 0, "changed_files": []}
    try:
        with workspace_write_lock(root, timeout_s=5.0):
            audit = organize_topics(root, agent=_agent(model))
    except TransactionError as exc:
        if exc.code == "CONCURRENT_UPDATE":
            return {"status": "busy", "topics": len(topics), "changed_files": []}
        raise
    return {
        "status": "ok",
        "topics": len(topics),
        "changed_files": _changed_files(audit),
    }
