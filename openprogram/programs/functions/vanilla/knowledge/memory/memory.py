"""Memory tools — reading and correcting the Markdown memory workspace.

Memory is written in the background, so there is no tool here for
"save this". What the agent needs mid-conversation is to look things up,
and occasionally to fix something it can see is wrong or to record a
fact the user asked it to remember right now.

Everything is a file: ``topics/**/*.md`` is the editable semantic
memory, ``sources/**`` the read-only evidence it cites, and
``timeline/`` a derived view rebuilt after every write.
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from openprogram.agent.authority import (
    AuthorityError,
    authority_from_message,
    has_capability,
    normalize_authority,
    owner_principal_id,
)
from openprogram.memory import store
from openprogram.memory.management import MemoryWorkspace
from openprogram.memory.management.config import load_memory_config
from openprogram.memory.management.transaction import (
    TransactionError,
    provenance_from_authority,
    workspace_write_lock,
    workspace_revision,
)
from openprogram.memory.backend import sanitize_context
from openprogram.memory.retrieval import inspect
from openprogram.memory.source_format import (
    encode_source_metadata,
    provider_source_location,
    scan_source_archive,
)
from openprogram.memory.runtime.state import RuntimeStateStore
from openprogram.memory.workspace_layout import runtime_dir

MAX_SNIPPETS = 8


def _root():
    return store.ensure()


def _fail(exc: TransactionError) -> str:
    error = {"code": exc.code, "message": exc.message, "path": exc.path}
    if exc.details:
        error["details"] = exc.details
    return json.dumps({"ok": False, "error": error}, ensure_ascii=False)


def _dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


# -- search ----------------------------------------------------------------

SEARCH_NAME = "memory_search"
SEARCH_DESC = (
    "Search memory by meaning and return the matching paragraphs with "
    "their file paths and stable anchors. Use this when you know what you are "
    "looking for but not how it was worded. For an exact name, ID or "
    "phrase use `memory_grep` instead."
)
SEARCH_SPEC: dict[str, Any] = {
    "name": SEARCH_NAME,
    "description": SEARCH_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "what to look for"},
            "top_k": {"type": "integer", "description": "results, max 10"},
            "path_prefix": {
                "type": "string",
                "description": "restrict to a subtree, e.g. topics/people/",
            },
            "speaker": {
                "type": "string",
                "description": "restrict source records to this speaker ID or label",
            },
        },
        "required": ["query"],
    },
}


def memory_search(
    query: str | None = None,
    top_k: int = MAX_SNIPPETS,
    path_prefix: str | None = None,
    speaker: str | None = None,
    **_: Any,
) -> str:
    if not (query or "").strip():
        return "memory_search needs a query."
    try:
        config = load_memory_config()
        if config.retrieval_method == "agent":
            return (
                "Ranked memory_search is disabled in Agent recall mode. "
                "Use memory_browse, memory_get, or memory_grep when needed."
            )
        found = inspect.search(
            _root(), query, top_k=int(top_k or MAX_SNIPPETS),
            path_prefix=path_prefix or None,
            speaker=speaker or None,
            method=config.retrieval_method,
            include_sources=config.retrieval_include_sources,
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
        source = where.startswith("sources/")
        suffix = ""
        if block:
            if source:
                location = provider_source_location(str(block))
                if location is not None:
                    suffix = f"#{location[1]}"
            else:
                suffix = f"#^{block}"
        metadata = ""
        if source:
            speaker_metadata = {
                "speaker_trusted": hit.get("speaker_trusted") is True,
                "speaker_id": sanitize_context(
                    str(hit.get("speaker_id") or "")
                ),
                "speaker_display": sanitize_context(
                    str(hit.get("speaker_display") or "")
                ),
            }
            if hit.get("trust_state") == "pending":
                speaker_metadata["trust_state"] = "pending"
            if "authority_tier" in hit:
                speaker_metadata["authority_tier"] = hit["authority_tier"]
            metadata = "\nspeaker: " + json.dumps(
                speaker_metadata, ensure_ascii=False, separators=(",", ":"),
            )
        lines.append(
            f"--- {where}{suffix}{metadata}\n"
            f"{sanitize_context(str(hit.get('content') or ''))}"
        )
    return "\n\n".join(lines)


# -- grep ------------------------------------------------------------------

GREP_NAME = "memory_grep"
GREP_DESC = (
    "Find an exact string in memory — a name, an identifier, a quoted "
    "phrase. Returns matching lines with their file and line number."
)
GREP_SPEC: dict[str, Any] = {
    "name": GREP_NAME,
    "description": GREP_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "prefix": {"type": "string", "description": "restrict to a subtree"},
            "case_sensitive": {"type": "boolean"},
        },
        "required": ["query"],
    },
}


def memory_grep(
    query: str | None = None,
    prefix: str = "",
    case_sensitive: bool = False,
    **_: Any,
) -> str:
    if not (query or "").strip():
        return "memory_grep needs a query."
    try:
        found = inspect.grep(
            _root(), query, prefix=prefix or "",
            case_sensitive=bool(case_sensitive),
        )
    except TransactionError as exc:
        return _fail(exc)
    matches = found.get("matches", [])
    if not matches:
        return f"No line in memory contains {query!r}."
    return "\n".join(
        f"{m.get('path')}:{m.get('line')}: {m.get('text', '').strip()}"
        for m in matches
    )


# -- read ------------------------------------------------------------------

GET_NAME = "memory_get"
GET_DESC = (
    "Read a memory file, or one section or block of it. Pass `heading` "
    "for a single section and `block_id` for a single paragraph with the "
    "footnotes it cites — reading a whole file is rarely what you want."
)
GET_SPEC: dict[str, Any] = {
    "name": GET_NAME,
    "description": GET_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "e.g. topics/people/dave.md"},
            "heading": {"type": "string"},
            "block_id": {"type": "string", "description": "without the ^"},
        },
        "required": ["path"],
    },
}


def memory_get(
    path: str | None = None,
    heading: str | None = None,
    block_id: str | None = None,
    **_: Any,
) -> str:
    if not (path or "").strip():
        return "memory_get needs a path. Use `memory_browse` to see them."
    try:
        found = inspect.read_file(
            _root(), path, heading=heading or None,
            block_id=(block_id or None),
        )
    except TransactionError as exc:
        return _fail(exc)
    return sanitize_context(found.get("content", "")) or "(empty)"


# -- browse ----------------------------------------------------------------

BROWSE_NAME = "memory_browse"
BROWSE_DESC = (
    "List what memory holds: the topic files, the archived sources, and "
    "the derived timeline. Start here when you do not know what exists."
)
BROWSE_SPEC: dict[str, Any] = {
    "name": BROWSE_NAME,
    "description": BROWSE_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "prefix": {
                "type": "string",
                "description": "restrict to a subtree, e.g. topics/",
            },
        },
        "required": [],
    },
}


def memory_browse(prefix: str = "", **_: Any) -> str:
    try:
        listing = inspect.list_files(_root(), prefix=prefix or "")
    except TransactionError as exc:
        return _fail(exc)
    files = listing.get("files", [])
    if not files:
        return (
            "Memory is empty. It fills in on its own as you talk — "
            "there is nothing to do about this."
        )
    lines = [f"{len(files)} file(s):", ""]
    lines += [
        f"  {f.get('path')}"
        + (f"  ({f.get('blocks')} blocks)" if f.get("blocks") else "")
        for f in files
    ]
    if listing.get("truncated"):
        lines.append("  … truncated; narrow with `prefix`.")
    return "\n".join(lines)


# -- update ----------------------------------------------------------------

UPDATE_NAME = "memory_update"
UPDATE_DESC = (
    "Correct or add one thing in memory. "
    "Conversation is written up in "
    "the background, so use this only for what the user asked you to "
    "remember right now, or to fix something you can see is wrong. "
    "Use record-level memory_changes when one or more final memory records "
    "are known. The Runtime creates, updates, deletes or moves Topic records "
    "and rebuilds derived views. A Scheduler lifecycle may be recorded when "
    "it has durable value; update it when the task closes, or delete it when "
    "nothing should remain. Structured whole-file changes and the older "
    "unified-diff patch form remain accepted for direct Markdown edits."
)
UPDATE_SPEC: dict[str, Any] = {
    "name": UPDATE_NAME,
    "description": UPDATE_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "base_revision": {"type": "string"},
            "patch": {"type": "string", "description": "unified diff"},
            "changes": {
                "type": "array",
                "description": "atomic whole-file writes and deletes",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "action": {"type": "string", "enum": ["write", "delete"]},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "action"],
                    "additionalProperties": False,
                },
            },
            "memory_changes": {
                "type": "array",
                "description": "atomic final-record CRUD and structural move",
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
                        "source_refs": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["op"],
                    "additionalProperties": False,
                },
            },
            "sources": {
                "type": "array",
                "description": "quoted statements the edit rests on",
                "items": {"type": "object"},
            },
            "commit_message": {"type": "string"},
        },
        "required": ["base_revision"],
    },
}


def memory_update(
    base_revision: str | None = None,
    patch: str | None = None,
    changes: list[dict[str, Any]] | None = None,
    memory_changes: list[dict[str, Any]] | None = None,
    sources: list[dict[str, Any]] | None = None,
    commit_message: str | None = None,
    **_: Any,
) -> str:
    if not (base_revision or "").strip():
        return _fail(TransactionError(
            "INVALID_ARGUMENT", "base_revision is required"
        ))
    try:
        # The workspace stages a copy of memory under the temp directory;
        # dropped without closing, one copy is left behind per call.
        from openprogram.agent.run_control import get_current_session_id
        from openprogram.store import _current_turn_id

        session_id = get_current_session_id() or ""
        turn_id = _current_turn_id.get() or ""
        authority = normalize_authority(authority_from_message(
            session_id, turn_id,
        ))
        # Identity fields come from the Runtime's own record of this turn, so
        # the model's payload cannot choose its trust state, tier or principal.
        provenance = (
            provenance_from_authority(
                authority, origin_id=f"{session_id}/{turn_id}",
            )
            if sources
            else None
        )
        with closing(MemoryWorkspace(
            _root(), config=load_memory_config(),
        )) as space:
            result = space.update(
                base_revision=base_revision,
                patch=patch or "",
                changes=changes,
                memory_changes=memory_changes,
                sources=sources,
                commit_message=commit_message,
                provenance=provenance,
                # Only an explicitly persisted owner turn may rewrite or
                # delete existing memory. Missing context fails closed.
                append_only=authority.get("authority_tier") != "owner",
            )
    except TransactionError as exc:
        return _fail(exc)
    return _dump({
        "ok": True,
        "revision": result.revision,
        "source_ids": result.source_ids,
        "block_ids": result.block_ids,
        "evidence_ids": result.evidence_ids,
        "changed_files": result.changed_files,
        "memory_committed": result.memory_committed,
        "git_committed": result.git_committed,
        "git_commit": result.git_commit,
    })


# -- trust promotion -------------------------------------------------------

PROMOTE_NAME = "memory_promote"
PROMOTE_DESC = (
    "Promote one pending unpaired-group source, then distill it into Topics. "
    "Only the local owner in an interactive turn can do this."
)
PROMOTE_SPEC: dict[str, Any] = {
    "name": PROMOTE_NAME,
    "description": PROMOTE_DESC,
    "parameters": {
        "type": "object",
        "properties": {"source_id": {"type": "string"}},
        "required": ["source_id"],
    },
}


def _restore_source_bytes(path: Path, original: str, mode: int) -> None:
    """Put the pre-promotion archive bytes back, atomically.

    The rename is what has to happen; the fsync before it only decides
    whether the restored bytes survive a power loss. So a failing fsync
    is not allowed to abandon the rollback — the very failure being
    undone here can be an fsync failure, and letting it stop the restore
    would leave the Source trusted with no audit entry, which is the one
    outcome promotion exists to prevent.
    """
    descriptor, temporary = tempfile.mkstemp(
        prefix="memory-promote-undo-", dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(original)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _restore_audit_append(path: Path, length: int, existed: bool) -> None:
    """Undo a partial append: cut the file back to the length it had.

    Truncation rather than a rewrite, because the file is append-only and
    every byte below ``length`` is an entry some earlier promotion already
    committed. A file that did not exist before is removed outright.

    Best-effort by construction: this runs while an error is already on
    its way up, and raising a second one here would replace the failure
    the caller needs to see with the failure of cleaning up after it.
    """
    try:
        if not existed:
            path.unlink(missing_ok=True)
            return
        descriptor = os.open(path, os.O_WRONLY)
        try:
            os.ftruncate(descriptor, length)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        pass


def _promote_source(
    root: Path, source_id: str, authority: dict[str, Any],
) -> dict[str, Any]:
    root = Path(root).resolve()
    auth = normalize_authority(authority)
    if not (
        auth
        and auth["speaker_kind"] == "owner"
        and auth["interaction"] == "interactive"
        and auth["principal_id"] == owner_principal_id()
        and has_capability(auth, "memory.trusted.promote")
    ):
        raise AuthorityError("only the local owner can promote memory")

    with workspace_write_lock(root, timeout_s=1.0):
        location = provider_source_location(str(source_id), v2=True)
        if location is None:
            raise ValueError("source_id must be provider/thread/message")
        path = root / location[0]
        if not path.is_file():
            raise ValueError(f"source not found: {source_id}")
        with path.open("r", encoding="utf-8", newline="") as handle:
            original = handle.read()
        scan = scan_source_archive(original, location[0])
        if not scan.complete:
            raise ValueError("source archive is invalid or truncated")
        frame = next(
            (item for item in scan.frames if item.source_id == source_id), None,
        )
        if frame is None:
            raise ValueError(f"source not found: {source_id}")
        if frame.metadata is None or frame.metadata_index is None:
            raise ValueError("source has no recorded authority metadata")
        if frame.metadata.get("trust_state") == "trusted":
            return {
                "source_id": source_id,
                "promoted": False,
                "trust_state": "trusted",
            }

        mode = path.stat().st_mode & 0o777
        metadata = {**frame.metadata, "trust_state": "trusted"}
        lines = original.split("\n")
        lines[frame.metadata_index] = encode_source_metadata(metadata)
        descriptor, temporary = tempfile.mkstemp(
            prefix="memory-promote-", dir=path.parent,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
                handle.write("\n".join(lines))
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, mode)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

        # A trusted Source with no audit record is the one outcome this must
        # never leave behind, so the audit append is what commits the
        # promotion: if it fails at any point, both files go back to what
        # they were and the caller sees the error. Repeating the promotion
        # then re-runs both halves and produces exactly one audit entry.
        #
        # Both files, not just the Source. A partial append leaves a
        # truncated JSON line that no later reader can parse, and rolling
        # back only the archive would keep that line forever — so the
        # audit file's original length is recorded first and truncated
        # back to it, which is exactly undoing an append.
        audit_path = runtime_dir(root) / "trust-audit.jsonl"
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_existed = audit_path.exists()
        audit_length = audit_path.stat().st_size if audit_existed else 0
        try:
            audit = json.dumps({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "action": "promote",
                "source_id": source_id,
                "principal_id": auth["principal_id"],
                "speaker_id": unquote(frame.encoded_speaker_id or ""),
                "authority_tier": auth["authority_tier"],
            }, ensure_ascii=False, separators=(",", ":")) + "\n"
            payload = audit.encode("utf-8")
            audit_fd = os.open(
                audit_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600,
            )
            try:
                # A short write is a normal outcome of os.write, not an
                # error: the kernel is free to accept part of the buffer.
                # Treating one as failure aborted a promotion that had
                # already half-landed, so the remainder is written instead.
                written = 0
                while written < len(payload):
                    count = os.write(audit_fd, payload[written:])
                    if count <= 0:
                        raise OSError("memory trust audit write made no progress")
                    written += count
                os.fsync(audit_fd)
            finally:
                os.close(audit_fd)
        except BaseException:
            _restore_source_bytes(path, original, mode)
            _restore_audit_append(audit_path, audit_length, audit_existed)
            raise
        RuntimeStateStore(root).git_commit(
            f"memory: trust source {source_id}"
        )
        return {
            "source_id": source_id,
            "promoted": True,
            "trust_state": "trusted",
        }


def memory_promote(source_id: str | None = None, **_: Any) -> str:
    if not (source_id or "").strip():
        return "memory_promote needs a source_id."
    try:
        from openprogram.agent.run_control import get_current_session_id
        from openprogram.store import _current_turn_id

        session_id = get_current_session_id() or ""
        authority = authority_from_message(session_id, _current_turn_id.get() or "")
        root = _root()
        result = _promote_source(root, str(source_id), authority)
        from openprogram.memory.writing import (
            distill_promoted_source,
        )

        changed = distill_promoted_source(root, str(source_id))
        result["distilled"] = changed is None or bool(changed)
        result["changed_files"] = changed or []
        return _dump(result)
    except TransactionError as exc:
        return _fail(exc)
    except (AuthorityError, ValueError) as exc:
        return _dump({"ok": False, "error": str(exc)})


# -- status ----------------------------------------------------------------

STATUS_NAME = "memory_status"
STATUS_DESC = (
    "Memory workspace identity, size, current revision, writer health, and "
    "pending turn count. The revision is required by `memory_update`."
)
STATUS_SPEC: dict[str, Any] = {
    "name": STATUS_NAME, "description": STATUS_DESC,
    "parameters": {"type": "object", "properties": {}, "required": []},
}


def memory_status(**_: Any) -> str:
    try:
        root = _root()
        return _dump(inspect.status(root))
    except TransactionError as exc:
        return _fail(exc)
    except Exception:  # noqa: BLE001
        return _dump({"workspace": inspect.workspace_identity(root),
                      "revision": workspace_revision(root)})


__all__ = [
    "SEARCH_NAME", "SEARCH_SPEC", "memory_search",
    "GREP_NAME", "GREP_SPEC", "memory_grep",
    "GET_NAME", "GET_SPEC", "memory_get",
    "BROWSE_NAME", "BROWSE_SPEC", "memory_browse",
    "UPDATE_NAME", "UPDATE_SPEC", "memory_update",
    "PROMOTE_NAME", "PROMOTE_SPEC", "memory_promote",
    "STATUS_NAME", "STATUS_SPEC", "memory_status",
]
