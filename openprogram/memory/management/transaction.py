"""Structured write transaction for interactive memory editing.

``MemoryWorkspace.shell()`` runs the same normalize-validate-install
pipeline but reaches it by executing an arbitrary shell command in the stage.
That is acceptable for a controlled experiment agent and unacceptable for a
tool exposed to a user's editor session, so this module drives the identical
pipeline from a unified diff instead.
"""

from __future__ import annotations

import errno
import hashlib
import os
import re
from collections.abc import Callable
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..markdown import parse_topic_tree
from ..runtime.state import RuntimeStateStore, SourceRecord
from ..workspace_layout import (
    ensure_runtime_dir,
    is_internal_path,
    is_state_file,
    runtime_dir,
)

WRITABLE_PREFIX = "topics/"
SOURCE_LABEL_PATTERN = re.compile(r"new-source-[a-z0-9-]+")
SOURCE_PROVIDER = "claude-code"
VALID_ROLES = ("user", "assistant", "system", "tool")


class TransactionError(Exception):
    """A transaction was rejected. ``code`` is a stable machine-readable tag."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        path: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.path = path
        self.details = details or {}


@dataclass(frozen=True)
class SourceInput:
    label: str
    role: str
    content: str
    observed_at: str | None = None


@dataclass(frozen=True)
class SourceProvenance:
    """Who a Source came from, as the Runtime persisted it.

    Every field here is read from Runtime state, never from the model's
    payload: a caller that could name its own ``principal_id`` or
    ``trust_state`` would be writing its own trust decision into the
    archive. ``speaker_display`` is mutable and deliberately absent from
    the identity hash — a renamed account must not mint new Source IDs
    for speech it already said.
    """

    principal_id: str
    speaker_kind: str
    speaker_id: str
    authority_tier: str
    origin_id: str
    speaker_display: str = ""

    def identity_seed(self) -> str:
        return "\x1f".join((
            self.principal_id,
            self.speaker_kind,
            self.speaker_id,
            self.authority_tier,
            self.origin_id,
        ))


def provenance_from_authority(
    authority: Any, *, origin_id: str,
) -> SourceProvenance:
    """Build provenance from persisted authority, or fail closed.

    An incomplete authority record is not a reason to fall back to
    ``principal_id=unknown`` with ``trust_state=trusted``: that exact
    combination is what task 2 exists to make unreachable.
    """
    from openprogram.agent.authority import normalize_authority

    normalized = normalize_authority(authority)
    if not normalized or not str(origin_id).strip():
        raise TransactionError(
            "WRITER_PRECONDITION_FAILED",
            "creating a source requires complete persisted authority",
        )
    return SourceProvenance(
        principal_id=normalized["principal_id"],
        speaker_kind=normalized["speaker_kind"],
        speaker_id=normalized["speaker_id"],
        authority_tier=normalized["authority_tier"],
        origin_id=str(origin_id),
        speaker_display=normalized["speaker_display"],
    )


@dataclass
class TransactionResult:
    revision: str
    source_ids: dict[str, str] = field(default_factory=dict)
    block_ids: dict[str, str] = field(default_factory=dict)
    evidence_ids: dict[str, str] = field(default_factory=dict)
    changed_files: list[str] = field(default_factory=list)
    memory_committed: bool = True
    git_committed: bool = False
    git_commit: str | None = None


@dataclass(frozen=True)
class TransactionLimits:
    max_sources: int = 64
    max_source_bytes: int = 256_000
    max_patch_bytes: int = 512_000
    max_commit_message_chars: int = 500


def workspace_revision(memory_dir: Path) -> str:
    """Content fingerprint of the committed workspace.

    Derived from bytes rather than mtime so a revision cannot silently repeat
    when two writes land inside one filesystem timestamp granule.
    """
    root = Path(memory_dir)
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root)
        # Retrieval caches are derived and may be rewritten by a read, and the
        # write lock is touched by every transaction. Neither is memory state,
        # so neither may look like a concurrent write.
        if is_internal_path(relative) and not is_state_file(relative):
            continue
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:32]


@contextmanager
def workspace_write_lock(memory_dir: Path, *, timeout_s: float = 10.0):
    """Exclusive cross-process lock covering one transaction."""
    lock_path = ensure_runtime_dir(memory_dir) / "write.lock"
    # Owner-only like everything else under the profile. The lock holds no
    # memory, but a file another account can open is one it can hold, and
    # that is enough to stall every write this workspace attempts.
    handle = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        _acquire(handle, lock_path, timeout_s)
        try:
            yield
        finally:
            _release(handle)
    finally:
        os.close(handle)


def _acquire(handle: int, lock_path: Path, timeout_s: float) -> None:
    import time

    deadline = time.monotonic() + timeout_s
    while True:
        try:
            _lock_exclusive(handle)
            return
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise
            if time.monotonic() >= deadline:
                raise TransactionError(
                    "CONCURRENT_UPDATE",
                    "another write transaction holds the workspace lock",
                    details={"lock": lock_path.name},
                ) from exc
            time.sleep(0.05)


if os.name == "nt":  # pragma: no cover - exercised on Windows only
    import msvcrt

    def _lock_exclusive(handle: int) -> None:
        msvcrt.locking(handle, msvcrt.LK_NBLCK, 1)

    def _release(handle: int) -> None:
        try:
            os.lseek(handle, 0, os.SEEK_SET)
            msvcrt.locking(handle, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
else:
    import fcntl

    def _lock_exclusive(handle: int) -> None:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _release(handle: int) -> None:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        except OSError:
            pass


def validate_writable_path(path: str) -> None:
    """Reject a workspace-relative path a hand edit may not write.

    Two rules, and both are about where bytes may land: nothing may climb
    out of the workspace, and inside it only Topic Markdown is authored by
    hand. ``sources/`` is the append-only evidence record, and ``core.md``
    is rendered from ``topics/core.md`` like the rest of the derived views,
    so an edit to either is refused rather than overwritten on the next
    write.
    """
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise TransactionError(
            "PATH_OUTSIDE_WORKSPACE",
            "path escapes the workspace",
            path=path,
        )
    posix = candidate.as_posix()
    if posix.startswith(WRITABLE_PREFIX) and posix.endswith(".md"):
        return
    raise TransactionError(
        "READ_ONLY_PATH",
        "only topics/**/*.md is writable",
        path=path,
    )


def staged_edit(
    root: Path,
    write: Callable[[Path], None],
    *,
    deleting: str = "",
    timeout_s: float = 5.0,
) -> tuple[bool, str]:
    """Apply a hand edit through the workspace stage, or not at all.

    ``write`` edits the staging copy the way it would edit the real tree.
    Someone editing a topic file by hand can drop a block ID or strand a
    footnote, and nothing else would notice until a later write failed, so
    the check runs here while the person who made the edit is still looking
    at it.

    Two things make that check mean something. The baseline is read from
    the committed workspace *before* anything is staged — read afterwards
    it would measure the edit against itself, and a dropped block ID would
    look like there never was one. And the edit lands only by installing a
    validated stage, so a rejected edit leaves the committed workspace
    byte-for-byte as it was rather than needing to be undone.

    ``deleting`` names a topic whose block IDs go away on purpose. Every
    other committed ID must still be reachable after the edit.
    """
    from .workspace import MemoryWorkspace

    try:
        # The lock spans staging, validation and install: the background
        # writer stages from this same tree and would otherwise install
        # over the edit, or be installed over by it.
        with workspace_write_lock(root, timeout_s=timeout_s):
            with closing(MemoryWorkspace(root)) as space:
                units, block_ids = committed_baseline(space)
                if deleting:
                    block_ids -= {
                        unit.memory_id for unit in units
                        if unit.topic_path == deleting
                    }
                write(space.stage_dir)
                install_state(space, units, block_ids)
    except TransactionError as exc:
        return False, exc.message
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    return True, ""


def parse_sources(raw: Any, limits: TransactionLimits) -> list[SourceInput]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise TransactionError("INVALID_ARGUMENT", "sources must be a list")
    if len(raw) > limits.max_sources:
        raise TransactionError(
            "INVALID_ARGUMENT",
            f"at most {limits.max_sources} sources per transaction",
        )
    seen: set[str] = set()
    total_bytes = 0
    parsed: list[SourceInput] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise TransactionError(
                "INVALID_ARGUMENT", f"sources[{index}] must be an object"
            )
        label = str(item.get("label", "")).strip()
        if not SOURCE_LABEL_PATTERN.fullmatch(label):
            raise TransactionError(
                "INVALID_ARGUMENT",
                f"sources[{index}].label must match new-source-[a-z0-9-]+",
                details={"label": label},
            )
        if label in seen:
            raise TransactionError(
                "INVALID_ARGUMENT",
                f"duplicate source label: {label}",
            )
        seen.add(label)
        role = str(item.get("role", "")).strip()
        if role not in VALID_ROLES:
            raise TransactionError(
                "INVALID_ARGUMENT",
                f"sources[{index}].role must be one of {list(VALID_ROLES)}",
            )
        content = str(item.get("content", ""))
        if not content.strip():
            raise TransactionError(
                "INVALID_ARGUMENT", f"sources[{index}].content is empty"
            )
        total_bytes += len(content.encode("utf-8"))
        if total_bytes > limits.max_source_bytes:
            raise TransactionError(
                "INVALID_ARGUMENT",
                f"source content exceeds {limits.max_source_bytes} bytes",
            )
        observed_raw = item.get("observed_at")
        observed = None
        if observed_raw not in (None, ""):
            observed = _normalize_timestamp(str(observed_raw), index)
        parsed.append(SourceInput(label, role, content, observed))
    return parsed


def _normalize_timestamp(value: str, index: int) -> str:
    from datetime import datetime

    try:
        return datetime.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise TransactionError(
            "INVALID_ARGUMENT",
            f"sources[{index}].observed_at must be ISO 8601",
            details={"observed_at": value},
        ) from exc


def source_records(
    sources: list[SourceInput], provenance: SourceProvenance,
) -> list[SourceRecord]:
    """Derive identity from Runtime provenance plus content.

    Identity combines the provenance seed with role, content, observation
    time and ordinal. A retry by the same subject in the same origin lands
    on the same IDs and is idempotent; a different principal, speaker or
    origin does not collide even when the quoted text is identical.
    """
    if not sources:
        return []
    seed_prefix = provenance.identity_seed()
    canonical = "\n".join(
        [seed_prefix] + [
            f"{item.role}\x1f{item.content}\x1f{item.observed_at or ''}"
            for item in sources
        ]
    )
    thread_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    records = []
    for ordinal, item in enumerate(sources, start=1):
        seed = (
            f"{seed_prefix}\x1f{thread_id}\x1f{ordinal}\x1f{item.role}"
            f"\x1f{item.content}\x1f{item.observed_at or ''}"
        )
        message_id = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
        records.append(SourceRecord(
            provider=SOURCE_PROVIDER,
            thread_id=thread_id,
            message_id=message_id,
            ordinal=ordinal,
            role=item.role,
            content=item.content,
            timestamp=item.observed_at,
            speaker_id=provenance.speaker_id,
            speaker_display=provenance.speaker_display,
            speaker_kind=provenance.speaker_kind,
            principal_id=provenance.principal_id,
            authority_tier=provenance.authority_tier,
            trust_state="trusted",
        ))
    return records


def resolve_source_labels(patch: str, mapping: dict[str, str]) -> str:
    """Replace transaction-local labels with archived source handles.

    Longest label first so ``new-source-a`` cannot partially match inside
    ``new-source-ab``.
    """
    unknown = {
        label for label in SOURCE_LABEL_PATTERN.findall(patch)
        if label not in mapping
    }
    if unknown:
        raise TransactionError(
            "MISSING_SOURCE",
            f"patch references undeclared source label: {sorted(unknown)[0]}",
            details={"labels": sorted(unknown)},
        )
    for label in sorted(mapping, key=len, reverse=True):
        patch = patch.replace(label, mapping[label])
    return patch


def committed_baseline(workspace: Any) -> tuple[list[Any], set[str]]:
    """Snapshot committed topic units and block IDs before staging any edit.

    ``shell()`` can read this from the stage because the stage still matches
    the committed tree at that point. A patch is applied to the stage first,
    so the baseline must come from ``memory_dir`` instead — reading the stage
    afterwards would treat the patch's own placeholders as pre-existing.
    """
    units = parse_topic_tree(workspace.memory_dir / "topics")
    return units, {unit.memory_id for unit in units}


def install_state(
    workspace: Any, before_units: list[Any], before_block_ids: set[str]
) -> None:
    """Validate staged topics and install them over the committed workspace.

    Mirrors the successful branch of ``MemoryWorkspace.shell()``.
    """
    workspace._normalize_topic_edits(before_block_ids)
    workspace._validate_topic_contract(before_units, before_block_ids)
    workspace._synchronize()


def git_commit_state(memory_dir: Path, message: str) -> str | None:
    return RuntimeStateStore(Path(memory_dir)).git_commit(message)
