"""Transactional editable memory workspace."""

import copy
import hashlib
import logging
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from openprogram.host import sandbox as _sandbox

from ..markdown import read_checked_tree, read_topic_tree
from .block_views import BlockViewsMixin
from .config import MemoryConfig
from .patching import apply_memory_changes
from .topic_normalization import TopicNormalizationMixin
from .transaction import (
    TransactionError,
    TransactionLimits,
    TransactionResult,
    committed_baseline,
    git_commit_state,
    install_state,
    preserve_creation_order,
    workspace_files,
    workspace_revision,
    workspace_write_lock,
)
from ..workspace_layout import (
    TEMPORARY_PREFIX,
    is_internal_path,
    is_state_file,
    runtime_dir,
)

_log = logging.getLogger(__name__)


#: A Topic file past this size holds several subjects and is split into
#: a directory of them; the structure listing says so beside the file.
TOPIC_SPLIT_BYTES = 16_000


def _topic_size_note(relative: Path, text: str) -> str:
    """`` (12 KB)`` beside a Topic path, with the split instruction past the limit."""
    size = len(text.encode("utf-8"))
    note = f"  ({max(1, round(size / 1000))} KB"
    if size > TOPIC_SPLIT_BYTES and relative.name != "core.md":
        target = relative.with_suffix("").as_posix() + "/"
        note += f" — over {TOPIC_SPLIT_BYTES // 1000} KB: split into {target}"
    return note + ")"


_FOREIGN_SOURCE: ContextVar[bool] = ContextVar("memory_foreign_source", default=False)


@contextmanager
def foreign_source(active: bool) -> Iterator[None]:
    """While ``active``, workspaces opened here refuse shell commands that cannot be sandboxed.

    For a batch holding words from someone other than the owner, which the
    writer agent reads and could be talked into acting on.
    """
    token = _FOREIGN_SOURCE.set(active)
    try:
        yield
    finally:
        _FOREIGN_SOURCE.reset(token)


class MemoryWorkspace(
    TopicNormalizationMixin,
    BlockViewsMixin,
):
    def __init__(
        self,
        memory_dir: str | Path,
        *,
        config: MemoryConfig | None = None,
        allowed_new_source_refs: set[str] | None = None,
    ):
        self.memory_dir = Path(memory_dir).resolve()
        # Topic files written outside OpenProgram (paths under topics/):
        # on the stage to be read, closed to every edit. Refreshed with
        # the stage.
        self.outside: set[str] = set()
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.stage_dir = Path(tempfile.mkdtemp(prefix=f"{TEMPORARY_PREFIX}topics-"))
        self.pending: dict[str, dict[str, Any]] = {}
        self.config = config or MemoryConfig()
        self._allowed_new_source_refs = (
            None
            if allowed_new_source_refs is None
            else frozenset(allowed_new_source_refs)
        )
        self.committed = False
        self._stage_usable = True
        self._shell_needs_sandbox = _FOREIGN_SOURCE.get()
        # Set for the duration of one structured transaction; see update().
        self._transaction_source_refs: frozenset[str] | None = None
        # The chat message a record edit cites when it names none.
        self.current_ref: str | None = None
        self.last_changed_topics: list[str] = []
        self.last_created_blocks = 0
        self._refresh_stage()

    def close(self) -> None:
        """Remove the staging directory.

        A workspace built for a single edit and dropped leaves one staged
        copy of memory in the temp directory per call. ``contextlib.closing``
        is the usual caller.
        """
        self._discard_stage()

    def _discard_stage(self) -> None:
        shutil.rmtree(self.stage_dir, ignore_errors=True)

    def _refresh_stage(self) -> None:
        with workspace_write_lock(self.memory_dir):
            self._refresh_stage_locked()

    def _refresh_stage_locked(self) -> None:
        self._stage_usable = False
        self._discard_stage()
        self.stage_dir.mkdir()
        for name in ("topics", "timeline"):
            source = self.memory_dir / name
            if source.exists():
                shutil.copytree(source, self.stage_dir / name)
        (self.stage_dir / "topics").mkdir(exist_ok=True)
        from . import ownership

        self.outside = ownership.outside_paths(self.memory_dir)
        for name in ("recent_events.jsonl", "commitments.jsonl"):
            derived = self.memory_dir / name
            if derived.exists():
                shutil.copy2(derived, self.stage_dir / derived.name)
        relations = self.memory_dir / "relations.json"
        if relations.exists():
            shutil.copy2(relations, self.stage_dir / relations.name)
        for name in ("core.md", "MEMORY.md"):
            root_file = self.memory_dir / name
            if root_file.exists():
                shutil.copy2(root_file, self.stage_dir / root_file.name)
        runtime = runtime_dir(self.memory_dir) / "runtime.json"
        staged_runtime = self.stage_dir / runtime.parent.name / "runtime.json"
        staged_runtime.parent.mkdir(parents=True, exist_ok=True)
        if runtime.exists():
            shutil.copy2(runtime, staged_runtime)
        # What each committed file held when the stage was copied, so a
        # write that ends later can tell what moved underneath it.
        self._base_files = workspace_files(self.memory_dir)
        self._stage_usable = True

    def shell(
        self, command: str, *, allow_correction: bool = False
    ) -> subprocess.CompletedProcess[str]:
        self.last_changed_topics = []
        self.last_created_blocks = 0
        before = self._workspace_fingerprint()
        before_units, before_block_ids, before_topics = self.baseline()
        # This MCP endpoint is the nested agent's only command path. It runs
        # in the sandbox wherever the platform has one, even while interactive
        # shell sandboxing is off, and runs without it elsewhere, unless the
        # text it is working from came from someone other than the owner.
        from openprogram.host.backend.local import _invocation

        policy = _sandbox.resolve_policy(required=True)
        assert policy is not None
        reason = _sandbox.unavailable_reason()
        if reason is None:
            args, use_shell, env, _sandboxed = _invocation(command, str(self.stage_dir), policy=policy)
        elif self._shell_needs_sandbox:
            raise _sandbox.SandboxUnavailable(
                f"this batch holds messages from someone other than the owner and there is no sandbox here ({reason}); "
                "use the Read, Write and Edit tools")
        else:
            _log.warning("memory workspace command runs without a sandbox: %s", reason)
            with _sandbox.without_policy():
                args, use_shell, env, _sandboxed = _invocation(command, str(self.stage_dir))
            env = _sandbox.child_env(policy)
        result = subprocess.run(
            args,
            cwd=self.stage_dir,
            shell=use_shell,
            text=True, encoding="utf-8", errors="replace",
            capture_output=True,
            timeout=120,
            env=env,
        )
        changed = self._workspace_fingerprint() != before
        if result.returncode != 0 and changed:
            self._refresh_stage()
        elif result.returncode == 0 and changed:
            self.commit_edits(before_units, before_block_ids, before_topics)
        return result

    def baseline(self) -> tuple[list[Any], set[str], dict[str, str]]:
        """Snapshot the staged tree so an edit can be committed against it."""
        units = read_topic_tree(
            self.stage_dir / "topics", outside=self.outside,
        ).units
        return (
            units,
            own_block_ids(units),
            self._topic_fingerprints(self.stage_dir / "topics"),
        )

    def is_outside(self, relative: str) -> bool:
        """Whether a path under ``topics/`` is a file written outside."""
        from . import ownership

        return ownership.contains(self.outside, relative)

    def _written_paths(self, stage: Path | None = None) -> set[str]:
        """Topic files (paths under ``topics/``) this write created or
        changed on ``stage``: the files it answers for. Measured against
        the committed bytes the stage was copied from."""
        topics = (stage or self.stage_dir) / "topics"
        written: set[str] = set()
        for path in topics.rglob("*.md") if topics.is_dir() else ():
            if not path.is_file():
                continue
            relative = path.relative_to(topics).as_posix()
            if self.is_outside(relative):
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if self._base_files.get(f"topics/{relative}") != digest:
                written.add(relative)
        return written

    def _parse_stage(self, stage: Path | None = None) -> list[Any]:
        """The staged tree, each file this write wrote held to the strict
        structure and every other one read as it is."""
        stage = stage or self.stage_dir
        return read_checked_tree(
            stage / "topics",
            checked=self._written_paths(stage),
            outside=self.outside,
        )

    def adopt_person_edits(self) -> None:
        """A person saving a file written outside makes it memory's own:
        from this install on it is checked, normalized and recorded as
        ours, like any file a person edits."""
        from ..policy import owner_editing

        if not owner_editing() or not self.outside:
            return
        topics = self.stage_dir / "topics"
        for relative in sorted(self.outside):
            staged = topics / relative
            current = (
                hashlib.sha256(staged.read_bytes()).hexdigest()
                if staged.is_file() else None
            )
            if current != self._base_files.get(f"topics/{relative}"):
                self.outside.discard(relative)

    def _refuse_outside_edits(self, stage: Path) -> None:
        """A file written outside OpenProgram is read-only to every
        transaction but a person's save: changed, deleted or moved on the
        stage, the whole write is refused."""
        from ..policy import owner_editing

        if owner_editing():
            return
        topics = stage / "topics"
        for relative in sorted(self.outside):
            staged = topics / relative
            base = self._base_files.get(f"topics/{relative}")
            current = (
                hashlib.sha256(staged.read_bytes()).hexdigest()
                if staged.is_file() else None
            )
            if current != base:
                raise TransactionError(
                    "OUTSIDE_FILE_READ_ONLY",
                    f"topics/{relative} was written outside OpenProgram and "
                    "is read-only: leave it as it is and put new facts in "
                    "another Topic file",
                    path=f"topics/{relative}",
                )

    def commit_edits(
        self,
        before_units: list[Any],
        before_block_ids: set[str],
        before_topics: dict[str, str],
    ) -> None:
        """Assign IDs, validate, rebuild derived views, and install.

        Called per shell command, and once after an agent turn that edited
        the stage through the built-in file tools. Any failure discards the
        staged edits and re-raises.
        """
        from ..store import _ensure_git_history

        _ensure_git_history(self.memory_dir)
        if not self._stage_usable:
            raise RuntimeError("memory stage is unavailable after rollback failure")
        self.last_changed_topics = []
        self.last_created_blocks = 0
        try:
            self._refuse_outside_edits(self.stage_dir)
            preserve_creation_order(self, before_units)
            self._normalize_topic_edits(before_block_ids, before_units)
            # A writer may delete a block (Git keeps it); a link left
            # pointing at it is refused when the views are rebuilt.
            self._validate_topic_contract(before_units)
            selected_refs = self._allowed_new_source_refs
            if selected_refs is not None:
                # The batch the Runtime selected is what this commit may newly
                # cite. _synchronize checks it per block, which a workspace-wide
                # set difference cannot: a new paragraph would otherwise pass
                # by reusing a reference some other Topic already cites.
                self._transaction_source_refs = frozenset(selected_refs)
            try:
                with workspace_write_lock(self.memory_dir):
                    self._take_in_outside_changes()
                    self._synchronize()
            finally:
                self._transaction_source_refs = None
            after_units = read_topic_tree(self.memory_dir / "topics").units
            after_topics = self._topic_fingerprints(self.stage_dir / "topics")
            self.last_changed_topics = [
                "topics/" + path
                for path in sorted(set(before_topics) | set(after_topics))
                if before_topics.get(path) != after_topics.get(path)
            ]
            self.last_created_blocks = len(
                {unit.memory_id for unit in after_units}
                - {unit.memory_id for unit in before_units}
            )
        except Exception:
            self._refresh_stage()
            raise

    # What the stage mirrors of the workspace, and installs back over it.
    _MIRRORED_DIRS = ("topics", "timeline")
    _MIRRORED_FILES = (
        "recent_events.jsonl", "commitments.jsonl", "relations.json",
        "core.md", "MEMORY.md",
    )

    def _take_in_outside_changes(self) -> None:
        """Rebase the stage onto files that changed on disk since it was
        copied. Called under the write lock, right before install.

        A file this write left alone takes the new bytes from disk, whatever
        they hold: a file another tool rewrote is read as it is and fails no
        write. One this write changed too, or the cursor, is a conflict, as
        is a taken file that leaves a link of ours dangling.
        """
        current = workspace_files(self.memory_dir)
        base = self._base_files
        moved = sorted(
            path for path in set(base) | set(current)
            if base.get(path) != current.get(path)
        )
        taken: list[str] = []
        conflicts: list[str] = []
        for relative in moved:
            path = Path(relative)
            if is_state_file(path):
                conflicts.append(relative)
                continue
            if not (
                path.parts[0] in self._MIRRORED_DIRS
                or relative in self._MIRRORED_FILES
            ):
                continue
            staged = self.stage_dir / relative
            staged_hash = (
                hashlib.sha256(staged.read_bytes()).hexdigest()
                if staged.is_file() else None
            )
            if staged_hash != base.get(relative):
                conflicts.append(relative)
            else:
                taken.append(relative)

        def conflict(reason: str) -> TransactionError:
            return TransactionError(
                "CONCURRENT_UPDATE",
                f"workspace changed while the writer was running: {reason}",
                details={"paths": conflicts[:20]},
            )

        if conflicts:
            raise conflict(", ".join(conflicts[:5]))
        taken_topics = [r for r in taken if r.startswith("topics/")]
        dangling_before = (
            _dangling(read_topic_tree(self.stage_dir / "topics").units)
            if taken_topics else set()
        )
        for relative in taken:
            staged = self.stage_dir / relative
            source = self.memory_dir / relative
            if source.is_file():
                staged.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, staged)
            else:
                staged.unlink(missing_ok=True)
        if taken_topics:
            from . import ownership

            # Which files are outside is a fact about the disk, which just
            # moved; the stage now holds exactly those bytes.
            self.outside = ownership.outside_paths(self.memory_dir)
            dangling = sorted(
                _dangling(read_topic_tree(self.stage_dir / "topics").units)
                - dangling_before
            )
            if dangling:
                conflicts = taken_topics
                raise conflict(f"dangling block link: {dangling[0]}")
        self._base_files = current

    def check_edits(self, baseline: tuple[Any, ...] | None) -> str | None:
        """Run the commit's checks on a copy of the stage, installing nothing.

        Returns the first problem, or None when the staged edits would
        commit. The Write and Edit tools call it after every change so a
        writer hears about a broken rule while it can still fix it, instead
        of losing the whole turn when it ends.
        """
        if baseline is None or not self._stage_usable:
            return None
        before_units, before_block_ids, _topics = baseline
        probe_dir = Path(tempfile.mkdtemp(prefix=f"{TEMPORARY_PREFIX}check-"))
        try:
            shutil.copytree(self.stage_dir, probe_dir, dirs_exist_ok=True)
            self._refuse_outside_edits(probe_dir)
            probe = copy.copy(self)
            probe.stage_dir = probe_dir
            preserve_creation_order(probe, before_units)
            probe._normalize_topic_edits(before_block_ids, before_units)
            probe._validate_topic_contract(before_units)
            probe._transaction_source_refs = self._allowed_new_source_refs
            probe._check_block_references(probe._parse_stage())
        except Exception as exc:  # noqa: BLE001 - any failure is the answer
            return f"{type(exc).__name__}: {exc}"
        finally:
            shutil.rmtree(probe_dir, ignore_errors=True)
        return None

    def stage_is_dirty(self) -> bool:
        """Whether the stage differs from what is committed on disk."""
        staged = {
            path.relative_to(self.stage_dir).as_posix(): path.read_bytes()
            for path in sorted(self.stage_dir.rglob("*"))
            if path.is_file()
            and not is_internal_path(path.relative_to(self.stage_dir))
        }
        committed = {
            path.relative_to(self.memory_dir).as_posix(): path.read_bytes()
            for path in sorted(self.memory_dir.rglob("*"))
            if path.is_file()
            and not is_internal_path(path.relative_to(self.memory_dir))
            # A workspace from before chat citations still holds its old
            # archive until migration removes it; the stage never does.
            and path.relative_to(self.memory_dir).parts[:1] != ("sources",)
        }
        return staged != committed

    def revision(self) -> str:
        """Fingerprint of the committed workspace, for optimistic concurrency."""
        return workspace_revision(self.memory_dir)

    def update(
        self,
        *,
        memory_changes: Any,
        commit_message: str | None = None,
        git_commit: str = "auto",
        limits: TransactionLimits | None = None,
        append_only: bool = False,
        allowed_refs: set[str] | frozenset[str] | None = None,
        current_ref: str | None = None,
    ) -> TransactionResult:
        """Apply record operations as one atomic transaction.

        Each record cites the chat messages it rests on. ``current_ref`` is
        the message a record cites when it names none — for a chat edit, the
        user message that asked for it. ``allowed_refs`` limits what a new
        citation may name, such as the messages of that chat; None leaves
        any message the chat history holds citable.
        """
        if git_commit not in ("auto", "on", "off"):
            raise TransactionError(
                "INVALID_ARGUMENT", "git_commit must be auto, on or off"
            )
        if git_commit != "off":
            from ..store import _ensure_git_history

            _ensure_git_history(self.memory_dir)
        if commit_message is not None and not isinstance(commit_message, str):
            raise TransactionError(
                "INVALID_ARGUMENT", "commit_message must be a string"
            )
        limits = limits or TransactionLimits()
        message = (commit_message or "Update memory").replace("\n", " ").strip()
        message = "".join(
            char for char in message if char.isprintable()
        )[:limits.max_commit_message_chars] or "Update memory"

        with workspace_write_lock(self.memory_dir):
            self._refresh_stage()
            before_files = self._committed_files()
            before_units, before_block_ids = committed_baseline(self)
            self.current_ref = current_ref
            if allowed_refs is not None:
                self._transaction_source_refs = frozenset(allowed_refs)
            try:
                changed = apply_memory_changes(
                    self,
                    memory_changes,
                    max_changes=limits.max_changes,
                    max_bytes=limits.max_bytes,
                )
                if append_only:
                    for relative in changed:
                        before = self.memory_dir / relative
                        after = self.stage_dir / relative
                        if before.exists() and (
                            not after.is_file()
                            or not after.read_bytes().startswith(before.read_bytes())
                        ):
                            raise TransactionError(
                                "APPEND_ONLY_REQUIRED",
                                "paired memory updates may create or append, "
                                "but cannot rewrite existing content",
                                path=relative,
                            )
                # A record edit may remove only the IDs it deletes by name.
                install_state(
                    self,
                    before_units,
                    before_block_ids,
                    allow_removed={
                        item.get("memory_id")
                        for item in memory_changes
                        if isinstance(item, dict)
                        and item.get("op") in ("delete_record", "delete")
                    },
                )
            except TransactionError:
                self._refresh_stage()
                raise
            except Exception as exc:
                self._refresh_stage()
                raise TransactionError(
                    "INVALID_TOPIC_FORMAT", str(exc)
                ) from exc
            finally:
                self._transaction_source_refs = None
                self.current_ref = None

            after_files = self._committed_files()
            changed_files = sorted(
                set(changed)
                | {
                    path
                    for path in set(before_files) | set(after_files)
                    if before_files.get(path) != after_files.get(path)
                }
            )
            result = TransactionResult(
                revision=workspace_revision(self.memory_dir),
                block_ids=dict(getattr(self, "last_block_id_map", {}) or {}),
                evidence_ids=dict(
                    getattr(self, "last_evidence_id_map", {}) or {}
                ),
                changed_files=changed_files,
            )
            if git_commit == "off":
                return result
            try:
                commit = git_commit_state(self.memory_dir, message)
            except Exception as exc:
                if git_commit == "on":
                    raise TransactionError(
                        "GIT_COMMIT_FAILED",
                        # Files are already installed; a failed commit does not
                        # undo them and must not be reported as a rollback.
                        f"memory committed but git commit failed: {exc}",
                        details={"memory_committed": True, "git_committed": False},
                    ) from exc
                return result
            if commit is None and git_commit == "on":
                raise TransactionError(
                    "GIT_COMMIT_FAILED",
                    "memory committed but workspace is not a git repository",
                    details={"memory_committed": True, "git_committed": False},
                )
            result.git_commit = commit
            result.git_committed = commit is not None
            result.revision = workspace_revision(self.memory_dir)
            return result

    def _committed_files(self) -> dict[str, str]:
        root = self.memory_dir
        result = {}
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(root)
            if is_internal_path(relative):
                continue
            result[relative.as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
        return result

    def _workspace_fingerprint(self) -> str:
        digest = hashlib.sha256()
        for path in sorted(self.stage_dir.rglob("*")):
            if path.is_file():
                digest.update(path.relative_to(self.stage_dir).as_posix().encode())
                digest.update(path.read_bytes())
        return digest.hexdigest()

    def structure(self) -> str:
        """List the workspace, with each Topic file's headings and block IDs.

        A bare path list tells the writer nothing about what a file already
        holds, so it reads every file before editing any of them. Showing the
        headings and IDs up front answers that question without a shell call.
        """
        lines = []
        topics = self.stage_dir / "topics"
        for path in sorted(self.stage_dir.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(self.stage_dir)
            if is_internal_path(relative):
                continue
            if path.suffix != ".md" or topics not in path.parents:
                lines.append(relative.as_posix())
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if self.is_outside(path.relative_to(topics).as_posix()):
                # Read it for what it says; it is not ours to change.
                lines.append(
                    relative.as_posix() + "  (written outside OpenProgram: "
                    "read-only. Read and mention it, but do not edit, move "
                    "or delete it, and do not link to its paragraphs)"
                )
                continue
            lines.append(relative.as_posix() + _topic_size_note(relative, text))
            heading = ""
            for line in text.splitlines():
                if line.startswith("#"):
                    heading = line.strip()
                    continue
                block = re.search(r"\s\^([A-Za-z0-9-]+)\s*$", line)
                if not block:
                    continue
                summary = re.sub(r"\[\^[^]]+\]", "", line[: block.start()])
                summary = " ".join(summary.split())
                if len(summary) > 90:
                    summary = summary[:87] + "..."
                lines.append(
                    f"    ^{block.group(1)}"
                    + (f"  [{heading.lstrip('# ')}]" if heading else "")
                    + f"  {summary}"
                )
                heading = ""
        return "\n".join(lines) or "(empty workspace)"


def own_block_ids(units: list[Any]) -> set[str]:
    """The block IDs a write must keep: the ``^id`` of every paragraph in
    memory's own files. A derived ID is never written, and a file written
    outside changes its IDs as it likes."""
    return {
        unit.memory_id for unit in units
        if not unit.derived_id and not unit.outside
    }


def _dangling(units: list[Any]) -> set[str]:
    """Link targets no unit holds."""
    ids = {unit.memory_id for unit in units}
    return {
        target for unit in units for target in unit.relation_targets
    } - ids
