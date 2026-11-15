"""Turn conversation into memory, once there is enough of it.

Folding turns into topic files costs a model call, so it waits until a
batch has gathered — roughly sixteen thousand tokens of conversation,
rather than a call per turn.

The conversation is read back from the session store rather than
accumulated in memory. That store is durable and ordered, the nodes
themselves record which memory workspace has written them, and it is what
every Topic footnote cites: memory keeps no copy of the conversation.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, TYPE_CHECKING

from .backend import MemoryWriteFailureCode, WriteFailure
from .project_layer import written_elsewhere
from .management import load_memory_config, organize_topics
from .management.agent import _run_agent
from .management.workspace import foreign_source
from .management.api import render_writer_task
from .management.transaction import (
    TransactionError,
    git_commit_state,
    workspace_write_lock,
)
from .runtime.online import OnlineMemoryRuntime
from .runtime.state import ChatTurn, RuntimeStateStore
from .chat_refs import citable, is_runtime_turn, observed_at

if TYPE_CHECKING:
    from .writer_agent import MemoryWriterAgent

logger = logging.getLogger(__name__)

# Persisted in session-node metadata. The value is historical — it dates
# from when the subsystem carried a code name — and it stays as written:
# changing it would orphan every node already marked.
WRITTEN_NODE_MARKER = "memory_written_scriptorium"



def _agent(model: str | None = None) -> MemoryWriterAgent:
    """A detached writer on the configured chat-agent provider stack."""
    from openprogram.state.config import read_config
    from .writer_agent import writer_agent

    configured = (((read_config().get("memory") or {}).get("writer") or {})
                  .get("model"))
    override = model or (str(configured).strip() if configured else None)
    return writer_agent(override)


def _counter() -> Callable[[str], int]:
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


def _records(
    session_id: str, messages: list[dict[str, Any]]
) -> list[ChatTurn]:
    """Conversation turns as a write reads them, in session order.

    Only what a person said and what the assistant replied, from speakers
    whose words may become memory (``chat_refs.citable``). Tool calls and
    their results are the machinery of a turn, not its content, and so are
    the runtime's own scheduling turns.
    """
    from openprogram.state.authority import is_legacy_message, normalize_authority

    rows: list[ChatTurn] = []
    for index, message in enumerate(messages):
        if message.get("role") not in ("user", "assistant"):
            continue
        if is_runtime_turn(message) or not _text_of(message).strip():
            continue
        message_id = str(message.get("id") or "").strip()
        if not message_id:
            raise ValueError("memory source message requires a stable id")
        authority = normalize_authority(message)
        if not authority and not is_legacy_message(message):
            # A node this build wrote always carries authority. Missing it
            # means the turn reached here by a path that never attributed
            # it, so it is refused rather than written and believed.
            raise ValueError(
                "memory source message requires authority: " + message_id
            )
        if not citable(message):
            continue
        rows.append(ChatTurn(
            session_id=session_id or "session",
            message_id=message_id,
            ordinal=index,
            role=message["role"],
            content=_text_of(message),
            timestamp=observed_at(message.get("timestamp")),
            speaker_id=authority.get("speaker_id", message.get("speaker_id")),
            speaker_display=authority.get(
                "speaker_display", message.get("speaker_display")
            ),
            from_owner=not authority or authority.get("authority_tier") == "owner",
        ))
    return rows


def _marked_ids(
    messages: list[dict[str, Any]], workspace_id: str,
) -> set[str]:
    return {
        str(message.get("id"))
        for message in messages
        if message.get("id") and (
            message.get(WRITTEN_NODE_MARKER) == workspace_id
            or message.get(WRITTEN_NODE_MARKER) in written_elsewhere()
        )
    }


def _first_batch(
    records: list[ChatTurn], counter: Any, threshold: int
) -> list[ChatTurn]:
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
    from openprogram.models.usage import usage_scope
    from openprogram.models.usage.event import CALL_KIND_MEMORY

    with usage_scope(call_kind=CALL_KIND_MEMORY, session_id=session_id):
        return _write_session(
            session_id, messages,
            token_threshold=token_threshold, force=force, model=model,
        )


def _write_session(
    session_id: str,
    messages: list[dict[str, Any]],
    *,
    token_threshold: int,
    force: bool,
    model: str | None,
) -> bool:
    from . import store

    records = _records(session_id, messages)
    if not records:
        return False

    root = store.ensure()
    workspace_id = store.workspace_id()
    from openprogram.state.store.current import current_store
    db = current_store()
    counter = _counter()
    memory_config = load_memory_config()
    runtime = OnlineMemoryRuntime(
        root, token_counter=counter, token_threshold=token_threshold,
        memory_config=memory_config,
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

    def writer(space: Any, batch: tuple[ChatTurn, ...]) -> list[str] | None:
        observed = next(
            (
                record.timestamp[:10]
                for record in reversed(batch) if record.timestamp
            ),
            "undated",
        )
        turns = [(r.speaker_label, r.content) for r in batch]
        refs = [r.ref for r in batch]
        audit = _run_agent(
            space.memory_dir,
            agent=get_agent(),
            task=render_writer_task([{
                "observation_date": observed,
                "turns": turns,
                "refs": refs,
            }], memory_dir=space.memory_dir),
            stage="write",
            allowed_new_source_refs={record.ref for record in batch},
        )
        changed = _changed_files(audit)
        if not changed:
            # Nothing installed. A writer that judged the batch not worth
            # keeping is done with it; one whose edits kept failing is not.
            return [] if _declined(audit) else None
        if memory_config.writer_verify:
            from .management.verification import verify_after_write

            changed = sorted({*changed, *verify_after_write(
                space.memory_dir, agent=get_agent(),
                observation_date=observed, turns=turns, refs=refs,
            )})
        return changed

    def mark(batch: tuple[ChatTurn, ...]) -> None:
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
    with workspace_write_lock(root, timeout_s=1.0), foreign_source(not all(r.from_owner for r in pending)):
        wrote = runtime.process(
            pending,
            writer,
            marked_ids=marked_ids,
            mark=mark,
            local_manager=organizer,
            global_manager=organizer,
            force=force,
            history_message=f"memory: write {_session_label(db, session_id)}",
        )
    if wrote:
        from .runtime.writer_status import record_success

        record_success(root)
    return wrote


def _branch(session_id: str) -> list[dict[str, Any]]:
    from openprogram.state.store.current import current_store

    return current_store().get_branch(session_id) or []


def _pending(
    session_id: str, messages: list[dict[str, Any]]
) -> list[ChatTurn]:
    """What this session still owes memory."""
    from . import store

    records = _records(session_id, messages)
    if not records:
        return []
    workspace_id = store.workspace_id()
    return OnlineMemoryRuntime(
        store.ensure(), token_counter=_counter()
    ).pending(records, _marked_ids(messages, workspace_id))


def _eligible_session_branches(
    db: Any,
    session_id: str,
    fallback: list[dict[str, Any]] | None,
) -> list[tuple[str | None, list[dict[str, Any]]]]:
    """Current head path first, followed by the other live tip paths."""
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
    return [branch for branch in branches if not _scheduled_branch(branch[1])]


def _scheduled_branch(rows: list[dict[str, Any]]) -> bool:
    """Whether a branch ends in a run the scheduler started.

    Routine runs are not memory sources, whether they finished or were
    cut off. A branch the owner went on to reply on ends in their own row
    and stays eligible.
    """
    last_user = next(
        (row for row in reversed(rows) if row.get("role") == "user"), None
    )
    return last_user is not None and last_user.get("source") == "scheduler"


def _force_branches(
    session_id: str,
    fallback: list[dict[str, Any]] | None,
) -> tuple[Any, list[tuple[str | None, list[dict[str, Any]]]]]:
    from openprogram.state.store.current import current_store

    db = current_store()
    return db, _eligible_session_branches(db, session_id, fallback)


def _write_workspace(
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
    ``write_session`` raises travels up to the backend, which is where
    a transaction code becomes retryable or not.
    """
    from . import is_enabled

    if not is_enabled():
        return None
    if not session_id:
        return WriteFailure(
            "no session id", retryable=False,
            reason_code=MemoryWriteFailureCode.MISSING_SESSION_ID,
        )
    from . import store

    rows = messages if messages is not None else _branch(session_id)
    candidates = rows
    if force:
        _db, _branches = _force_branches(session_id, messages)
        candidates = [row for _, branch in _branches for row in branch]
    if not _records(session_id, candidates):
        return None
    store.ensure()
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
                return WriteFailure(
                    "the writer made no progress",
                    retryable=False,
                    reason_code=MemoryWriteFailureCode.WRITER_NO_PROGRESS,
                )
            if head is None:
                return WriteFailure(
                    "session nodes unavailable",
                    retryable=False,
                    reason_code=(
                        MemoryWriteFailureCode.SESSION_NODES_UNAVAILABLE
                    ),
                )
            branch_rows = db.get_branch(session_id, head) or []
    return None


def write(session_id: str, messages=None, *, token_threshold: int, force=False):
    """Restore durable policy separately for each authorized destination."""
    from .policy import current, eligible, from_snapshot, node_policy, scope
    rows = messages if messages is not None else _branch(session_id)
    if force and session_id:
        _, branches = _force_branches(session_id, messages)
        rows = [row for _, branch in branches for row in branch]
    elif not force and _scheduled_branch(rows):
        # The per-turn pass reads the checked-out path, and the owner can
        # have a task's branch open when any turn finishes.
        return None
    policies = {}
    outer = current()
    for row in rows:
        # A report is never a source, so it names no destination either.
        if row.get("always_on_report") or not eligible(row):
            continue
        raw = node_policy(row)
        policy = from_snapshot(raw) if raw else None
        if outer is not None and policy != outer:
            continue
        key = (policy.agent_id, policy.epoch, policy.created_at, policy.write_space) if policy else None
        policies[key] = policy
    if not session_id:
        return _write_workspace(session_id, messages, token_threshold=token_threshold, force=force)
    from .project_layer import write_target
    for policy in policies.values():
        with scope(policy), write_target(session_id):
            left = _write_workspace(session_id, messages, token_threshold=token_threshold, force=force)
        if left is not None:
            return left
    return None


def _changed_files(audit: list[dict[str, Any]]) -> list[str]:
    """The topic files an agent run committed a change to."""
    return sorted({
        path
        for entry in audit
        if entry.get("tool") == "commit" and entry.get("status") == "ok"
        for path in entry.get("topic_paths") or []
    })


def _session_label(db: Any, session_id: str) -> str:
    """How a history commit names the conversation it wrote from."""
    try:
        title = str((db.get_session(session_id) or {}).get("title") or "").strip()
    except Exception:  # noqa: BLE001 - a label must not fail the write
        title = ""
    title = " ".join(title.split())[:60]
    return f'"{title}" ({session_id})' if title else session_id


def _declined(audit: list[dict[str, Any]]) -> bool:
    """The run ended normally having tried no edit that failed.

    That is a writer deciding the batch holds nothing worth keeping. A run
    that spent its turns on edits that kept failing also ends without a
    change, and its batch is still owed to memory.
    """
    finished = any(entry.get("tool") == "agent" for entry in audit)
    return finished and not any(
        entry.get("status") == "error"
        and entry.get("tool") in ("Write", "Edit", "shell", "commit")
        for entry in audit
    )


def oversized_topics(root: Path) -> list[str]:
    """Topic files past the split size, largest first (``topics/core.md`` aside)."""
    from .management.workspace import TOPIC_SPLIT_BYTES
    sizes = [
        (path.stat().st_size, path.relative_to(root).as_posix())
        for path in (root / "topics").rglob("*.md")
        if path.relative_to(root).as_posix() != "topics/core.md"
    ]
    return [rel for size, rel in sorted(sizes, reverse=True) if size > TOPIC_SPLIT_BYTES]


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
    from . import is_enabled, store

    if not is_enabled():
        return {"status": "disabled"}

    root = store.ensure()
    topics = list((root / "topics").rglob("*.md"))
    if not topics:
        return {"status": "empty", "topics": 0, "changed_files": []}
    try:
        with workspace_write_lock(root, timeout_s=5.0):
            agent = _agent(model)
            audit: list[dict[str, Any]] = []
            # One pass over every file runs out before it reaches a large
            # file late in the list, so each oversized file gets a pass of
            # its own first, then the whole workspace gets the usual one.
            # Each pass is its own commit, so one bad pass can be undone
            # without losing the others.
            for path in oversized_topics(root):
                audit += organize_topics(root, agent=agent, touched={path})
                git_commit_state(root, f"memory: split {path}")
            audit += organize_topics(root, agent=agent)
            RuntimeStateStore(root).git_commit("memory: reorganize topics")
    except TransactionError as exc:
        if exc.code == "CONCURRENT_UPDATE":
            return {"status": "busy", "topics": len(topics), "changed_files": []}
        raise
    return {
        "status": "ok",
        "topics": len(topics),
        "changed_files": _changed_files(audit),
    }
