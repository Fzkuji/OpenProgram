"""Memory tools — reading and correcting the Markdown memory workspace.

Memory is written in the background, so there is no tool here for
"save this". What the agent needs mid-conversation is to look things up,
and occasionally to fix something it can see is wrong or to record a
fact the user asked it to remember right now. Three tools cover that:
``memory_search`` finds, ``memory_get`` reads, ``memory_update`` edits.

Everything is a file: ``topics/**/*.md`` is the editable semantic
memory and ``timeline/`` a derived view rebuilt after every write. A
footnote cites the chat message a fact came from as
``/s/<session>#<message>``; the chat history is the evidence, and
``memory_get`` on such a link reads the conversation around it.

In a project with project Memory on, every read spans the project's
workspace and the personal one, and paths name their layer
(``project:topics/api.md``, ``personal:topics/me.md``).
"""

from __future__ import annotations

import json
from contextlib import closing, contextmanager
from typing import Any

from openprogram.runtime.permissions.authority import authority_from_message, turn_origin
from openprogram.state.authority import normalize_authority
from openprogram.state.memory import chat_refs, store
from openprogram.state.memory.management import MemoryWorkspace
from openprogram.state.memory.management.config import load_memory_config
from openprogram.state.memory.management.transaction import TransactionError
from openprogram.state.memory.backend import sanitize_context

MAX_SNIPPETS = 8


def _root():
    return store.ensure()


def _memory():
    """The layers this conversation reads, as one memory."""
    from openprogram.state.memory.project_layer import read_layers

    return read_layers(personal=_root())


def _fail(exc: TransactionError) -> str:
    error = {"code": exc.code, "message": exc.message, "path": exc.path}
    if exc.details:
        error["details"] = exc.details
    return json.dumps({"ok": False, "error": error}, ensure_ascii=False)


def _dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _turn() -> tuple[str, str]:
    """(session id, message id) of the message that asked for this turn.

    The call runs under the assistant reply's id; the request it answers
    is the reply's predecessor, and that is what memory cites and whose
    authority decides what the edit may do.
    """
    from openprogram.runtime.execution.run_control import get_current_session_id
    from openprogram.state.store import _current_turn_id

    session_id = get_current_session_id() or ""
    return session_id, turn_origin(session_id, _current_turn_id.get() or "")[0]


# -- search ----------------------------------------------------------------

SEARCH_NAME = "memory_search"
SEARCH_DESC = (
    "Search memory and return the matching paragraphs with their file "
    "paths, stable anchors and the chat messages they cite. By default it "
    "ranks by meaning; set `exact` to find a literal name, ID or phrase "
    "line by line."
)
SEARCH_SPEC: dict[str, Any] = {
    "name": SEARCH_NAME,
    "description": SEARCH_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "what to look for"},
            "exact": {
                "type": "boolean",
                "description": "match the literal string instead of ranking",
            },
            "top_k": {"type": "integer", "description": "results, max 10"},
            "path_prefix": {
                "type": "string",
                "description": "restrict to a subtree, e.g. topics/people/",
            },
        },
        "required": ["query"],
    },
}


#: What a hit that cites nothing says about where it came from, so a model
#: can weigh a person's own note above another tool's file.
SOURCE_LINES = {
    "outside": "source: outside file",
    "person": "source: added by person",
}


def memory_search(
    query: str | None = None,
    exact: bool = False,
    top_k: int = MAX_SNIPPETS,
    path_prefix: str | None = None,
    **_: Any,
) -> str:
    if not (query or "").strip():
        return "memory_search needs a query."
    try:
        config = load_memory_config()
        # Agent recall mode runs no ranking; the literal match still works.
        if exact or config.retrieval_method == "agent":
            return _grep(query, path_prefix or "")
        found = _memory().search(
            query, top_k=int(top_k or MAX_SNIPPETS),
            path_prefix=path_prefix or None,
            method=config.retrieval_method,
        )
    except TransactionError as exc:
        return _fail(exc)
    results = found.get("results", [])
    if not results:
        return f"No memory matches {query!r}."
    lines = []
    for hit in results:
        where = str(hit.get("path") or "?")
        block = hit.get("event_id")
        cites = [
            chat_refs.link(ref) for ref in hit.get("refs") or []
            if chat_refs.split(ref) is not None
        ]
        origin = SOURCE_LINES.get(str(hit.get("origin") or ""))
        lines.append(
            f"--- {where}{f'#^{block}' if block else ''}"
            + (f"\ncites: {', '.join(cites)}" if cites else "")
            + (f"\n{origin}" if origin else "")
            + f"\n{sanitize_context(str(hit.get('content') or ''))}"
        )
    return "\n\n".join(lines)


def _grep(query: str, prefix: str) -> str:
    found = _memory().grep(query, prefix=prefix)
    matches = found.get("matches", [])
    if not matches:
        return f"No line in memory contains {query!r}."
    lines = [
        f"{m.get('path')}:{m.get('line')}: "
        f"{sanitize_context(m.get('text', '').strip())}"
        for m in matches
    ]
    if found.get("truncated"):
        lines.append("… more matches; narrow with `path_prefix`.")
    return "\n".join(lines)


# -- read ------------------------------------------------------------------

GET_NAME = "memory_get"
GET_DESC = (
    "Read memory. Without `path` it lists the files memory holds and the "
    "writer's health. With a file path, pass `heading` for one section or "
    "`block_id` for one paragraph with the footnotes it cites. With a "
    "citation such as `/s/<session>#<message>`, it reads that chat message "
    "and the turns around it, to check what was actually said."
)
GET_SPEC: dict[str, Any] = {
    "name": GET_NAME,
    "description": GET_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "e.g. topics/people/dave.md, or a citation /s/<session>#<message>"
                ),
            },
            "heading": {"type": "string"},
            "block_id": {"type": "string", "description": "without the ^"},
        },
        "required": [],
    },
}


def memory_get(
    path: str | None = None,
    heading: str | None = None,
    block_id: str | None = None,
    **_: Any,
) -> str:
    target = (path or "").strip()
    try:
        if not target and not block_id:
            return _overview()
        if not target and block_id:
            target = _derived_path(block_id) or ""
        if not target:
            return _overview()
        cited = chat_refs.from_target(target)
        if cited is not None:
            return _cited_chat(cited)
        found = _memory().read_file(
            target, heading=heading or None, block_id=(block_id or None),
        )
    except TransactionError as exc:
        return _fail(exc)
    return sanitize_context(found.get("content", "")) or "(empty)"


def _derived_path(block_id: str) -> str | None:
    """The file a derived paragraph ID was read from, found by the path
    key inside the ID, in whichever memory layer holds it."""
    from openprogram.state.memory.markdown.parser import file_for_derived_id

    memory = _memory()
    for layer in getattr(memory, "layers", None) or [memory.default]:
        found = file_for_derived_id(layer.root / "topics", block_id.lstrip("^"))
        if found is not None:
            relative = found.relative_to(layer.root).as_posix()
            return relative if memory.single else memory.qualify(layer, relative)
    return None


def _cited_chat(ref: str) -> str:
    """The cited message in its conversation, if this turn may read it."""
    session_id, _message_id = chat_refs.split(ref)
    current, turn_id = _turn()
    authority = normalize_authority(authority_from_message(current, turn_id))
    # Another conversation is the owner's to read, like read_conversation.
    if session_id != current and authority.get("authority_tier") != "owner":
        from openprogram.runtime.permissions import refusal
        raise refusal.memory_access(f'memory_get "{ref}"')
    excerpt = chat_refs.ChatLookup().excerpt(ref)
    return sanitize_context(chat_refs.render_excerpt(excerpt))


def _overview() -> str:
    memory = _memory()
    listing = memory.list_files(prefix="")
    files = listing.get("files", [])
    try:
        status = memory.status()
    except Exception:  # noqa: BLE001 - the listing is still worth returning
        status = {}
    lines = []
    if status.get("layers"):
        lines.append(
            "layers: " + ", ".join(status["layers"])
            + f"; writes go to {status.get('default_layer')} unless `layer` is given"
        )
    writer = status.get("writer") or {}
    if writer:
        lines.append("writer: " + json.dumps(writer, ensure_ascii=False))
    if not files:
        lines.append(
            "Memory is empty. It fills in on its own as you talk — "
            "there is nothing to do about this."
        )
        return "\n".join(lines)
    lines += [f"{len(files)} file(s):", ""]
    lines += [
        f"  {f.get('path')}"
        + (f"  ({f.get('blocks')} blocks)" if f.get("blocks") else "")
        for f in files
    ]
    if listing.get("truncated"):
        lines.append("  … truncated.")
    return "\n".join(lines)


# -- update ----------------------------------------------------------------

UPDATE_NAME = "memory_update"
UPDATE_DESC = (
    "Correct or add something in memory. Conversation is written up in the "
    "background, so use this only for what the user asked you to remember "
    "right now, or to fix something you can see is wrong. Each change is "
    "one record: create, update, delete or move a paragraph by its "
    "memory_id. A record cites the chat messages it rests on; leave "
    "`sources` out to cite the user message that asked for this edit. The "
    "Runtime assigns IDs, checks every record and rebuilds derived views."
)
UPDATE_SPEC: dict[str, Any] = {
    "name": UPDATE_NAME,
    "description": UPDATE_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "memory_changes": {
                "type": "array",
                "description": "record changes applied together or not at all",
                "items": {
                    "type": "object",
                    "properties": {
                        "op": {
                            "type": "string",
                            "enum": [
                                "create_record",
                                "update_record",
                                "delete_record",
                                "move_records",
                            ],
                        },
                        "memory_id": {"type": "string"},
                        "memory_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "destination": {
                            "type": "object",
                            "properties": {
                                "topic_path": {"type": "string"},
                                "headings": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "position": {
                                    "type": "string",
                                    "enum": ["start", "end", "before", "after"],
                                },
                                "anchor_memory_id": {"type": "string"},
                            },
                            "required": ["topic_path", "headings", "position"],
                            "additionalProperties": False,
                        },
                        "content": {"type": "string"},
                        "time": {
                            "type": "string",
                            "description": "YYYY, YYYY-MM, YYYY-MM-DD, or undated",
                        },
                        "sources": {
                            "type": "array",
                            "description": (
                                "the chat messages this record rests on; omit "
                                "to cite the current user message"
                            ),
                            "items": {
                                "type": "object",
                                "properties": {
                                    "source": {
                                        "type": "string",
                                        "description": (
                                            "A chat message link /s/<session>#<message>, "
                                            "or current for the message that asked for this edit."
                                        ),
                                    },
                                    "label": {
                                        "type": "string",
                                        "description": (
                                            "Context-specific plain keyword: Chinese at most "
                                            "8 visible characters or English at most 6 words. "
                                            "Do not use an ID, speaker, date, URL or Markdown."
                                        ),
                                    },
                                },
                                "required": ["source", "label"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["op"],
                    "additionalProperties": False,
                },
            },
            "commit_message": {"type": "string"},
            "layer": {
                "type": "string",
                "enum": ["project", "personal"],
                "description": (
                    "Only when memory_get lists layers: project for what "
                    "belongs to this project, personal for what holds "
                    "everywhere. Omit for the default layer it names."
                ),
            },
        },
        "required": ["memory_changes"],
    },
}


def memory_update(
    memory_changes: list[dict[str, Any]] | None = None,
    commit_message: str | None = None,
    layer: str | None = None,
    **_: Any,
) -> str:
    if not memory_changes:
        return _fail(TransactionError(
            "INVALID_ARGUMENT", "memory_changes is required"
        ))
    try:
        session_id, turn_id = _turn()
        authority = normalize_authority(authority_from_message(
            session_id, turn_id,
        ))
        owner = authority.get("authority_tier") == "owner"
        current_ref = (
            chat_refs.ref(session_id, turn_id) if session_id and turn_id else None
        )
        from openprogram.state.memory.project_layer import (
            PROJECT, first_write_root,
        )
        from openprogram.state.memory.retrieval.layers import Layer

        memory = _memory()
        try:
            target = memory.resolve(layer) if layer else memory.default
        except ValueError as exc:
            raise TransactionError("INVALID_ARGUMENT", str(exc)) from exc
        if memory.single and layer == PROJECT:
            raise TransactionError(
                "INVALID_ARGUMENT",
                "this conversation has no project memory",
            )
        # A project whose memory is still empty is not read yet, but a new
        # record goes there, as the background writer's do. Edits of what
        # exists stay with the personal memory that holds it.
        if memory.single and not layer and _creates_only(memory_changes):
            first = first_write_root(session_id or None)
            if first is not None:
                target = Layer(PROJECT, first)
        # The workspace stages a copy of memory under the temp directory;
        # dropped without closing, one copy is left behind per call.
        with (
            _project_layer(target.root)
            if target.name == PROJECT else _no_layer()
        ), closing(MemoryWorkspace(
            target.root, config=load_memory_config(),
        )) as space:
            result = space.update(
                memory_changes=memory_changes,
                # History names the conversation the edit came from.
                commit_message=(
                    f"memory: {(commit_message or 'update').strip()}"
                    + (f" (chat {session_id})" if session_id else " (chat)")
                ),
                current_ref=current_ref,
                # The owner may cite any conversation; anyone else only
                # the one they are in.
                allowed_refs=None if owner else _session_refs(session_id),
                # Only an explicitly persisted owner turn may rewrite or
                # delete existing memory. Missing context fails closed.
                append_only=not owner,
            )
    except TransactionError as exc:
        return _fail(exc)
    return _dump({
        "ok": True,
        **(
            {} if memory.single and target.name != PROJECT
            else {"layer": target.name}
        ),
        "revision": result.revision,
        "block_ids": result.block_ids,
        "evidence_ids": result.evidence_ids,
        "changed_files": result.changed_files,
        "memory_committed": result.memory_committed,
        "git_committed": result.git_committed,
        "git_commit": result.git_commit,
    })


def _session_refs(session_id: str) -> set[str]:
    if not session_id:
        return set()
    from openprogram.runtime.context.session_db import default_db

    return {
        chat_refs.ref(session_id, str(row["id"]))
        for row in default_db().get_messages(session_id) or []
        if row.get("id")
    }


def _creates_only(memory_changes: list[dict[str, Any]]) -> bool:
    """Whether the changes only add records, anchored to nothing existing."""
    return all(
        isinstance(change, dict)
        and change.get("op") == "create_record"
        and not (change.get("destination") or {}).get("anchor_memory_id")
        for change in memory_changes
    )


@contextmanager
def _no_layer():
    yield


@contextmanager
def _project_layer(root):
    """Select the project workspace, creating it on its first edit as the
    background writer does."""
    from openprogram.state.memory.project_layer import use

    with use(root, write=True):
        store.ensure()
        yield


__all__ = [
    "SEARCH_NAME", "SEARCH_SPEC", "memory_search",
    "GET_NAME", "GET_SPEC", "memory_get",
    "UPDATE_NAME", "UPDATE_SPEC", "memory_update",
]

# A registry filter is not an execution boundary: Python function calls and
# previously disclosed tool handles must observe the same policy.
from openprogram.state.memory.policy import guard_tool as _guard_memory_tool
for _memory_name, _memory_spec, _memory_write in (
    ('memory_search', SEARCH_SPEC, False),
    ('memory_get', GET_SPEC, False),
    ('memory_update', UPDATE_SPEC, True),
):
    _memory_spec['parameters']['properties']['space'] = {
        'type': 'string', 'enum': ['self', 'legacy_global'],
        'description': 'Omit to use this execution\'s memory. Pass a space only when the user names one; an unauthorized space is denied.',
    }
    globals()[_memory_name] = _guard_memory_tool(globals()[_memory_name], write=_memory_write)
