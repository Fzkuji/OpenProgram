"""Transactional editable memory workspace."""

import hashlib
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from openprogram import sandbox as _sandbox

from ..markdown import parse_topic_tree
from .block_views import BlockViewsMixin
from .config import MemoryConfig
from .event_writing import EventWritingMixin
from .patching import apply_patch
from .source_archive import SourceArchiveMixin
from .topic_normalization import TopicNormalizationMixin
from .transaction import (
    SourceProvenance,
    TransactionError,
    TransactionLimits,
    TransactionResult,
    committed_baseline,
    git_commit_state,
    install_state,
    parse_sources,
    resolve_source_labels,
    source_records,
    workspace_revision,
    workspace_write_lock,
)
from ..workspace_layout import TEMPORARY_PREFIX, is_internal_path, runtime_dir


class MemoryWorkspace(
    TopicNormalizationMixin,
    SourceArchiveMixin,
    EventWritingMixin,
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
        # Set for the duration of one structured transaction; see update().
        self._transaction_source_refs: frozenset[str] | None = None
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
        # Staged sources are chmod'd read-only, so restore write access before
        # removing the tree: on some systems the file's own mode blocks it.
        sources = self.stage_dir / "sources"
        if sources.exists():
            for path in sources.rglob("*"):
                if path.is_file():
                    path.chmod(0o644)
        shutil.rmtree(self.stage_dir, ignore_errors=True)

    def _refresh_stage(self) -> None:
        self._discard_stage()
        self.stage_dir.mkdir()
        for name in ("topics", "timeline", "sources"):
            source = self.memory_dir / name
            if source.exists():
                shutil.copytree(source, self.stage_dir / name)
        (self.stage_dir / "topics").mkdir(exist_ok=True)
        recent = self.memory_dir / "recent_events.jsonl"
        if recent.exists():
            shutil.copy2(recent, self.stage_dir / recent.name)
        relations = self.memory_dir / "relations.json"
        if relations.exists():
            shutil.copy2(relations, self.stage_dir / relations.name)
        core = self.memory_dir / "core.md"
        if core.exists():
            shutil.copy2(core, self.stage_dir / core.name)
        runtime = runtime_dir(self.memory_dir) / "runtime.json"
        staged_runtime = self.stage_dir / runtime.parent.name / "runtime.json"
        staged_runtime.parent.mkdir(parents=True, exist_ok=True)
        if runtime.exists():
            shutil.copy2(runtime, staged_runtime)
        if self._allowed_new_source_refs is None:
            self._protect_staged_sources()
        else:
            # A selected writer batch already receives its complete source
            # text in the prompt. Hiding the archive prevents it from reading
            # pending or out-of-batch evidence and attributing that content to
            # one of the selected references.
            shutil.rmtree(self.stage_dir / "sources", ignore_errors=True)

    def _restore_staged_sources(self) -> None:
        """Restore the committed archive after a restricted agent turn."""
        staged = self.stage_dir / "sources"
        if staged.exists():
            for path in staged.rglob("*"):
                if path.is_file():
                    path.chmod(0o644)
            shutil.rmtree(staged)
        committed = self.memory_dir / "sources"
        if committed.exists():
            shutil.copytree(committed, staged)

    def _protect_staged_sources(self) -> None:
        """Make the staged evidence record read-only on the filesystem.

        Telling a writer that sources/ is off limits does not stop one from
        editing it anyway, and the transaction only notices at the end of the
        turn, discarding the good edits alongside the bad. A denied write
        fails at the point of the attempt and reports why.
        """
        sources = self.stage_dir / "sources"
        if not sources.exists():
            return
        for path in sources.rglob("*"):
            if path.is_file():
                path.chmod(0o444)

    def shell(
        self, command: str, *, allow_correction: bool = False
    ) -> subprocess.CompletedProcess[str]:
        self.last_changed_topics = []
        self.last_created_blocks = 0
        before = self._workspace_fingerprint()
        before_topics = self._topic_fingerprints(self.stage_dir / "topics")
        before_sources = self._tree_fingerprint(self.stage_dir / "sources")
        before_units = parse_topic_tree(self.stage_dir / "topics")
        before_block_ids = {unit.memory_id for unit in before_units}
        # This MCP endpoint is the nested agent's only command path. It is
        # always sandboxed, even while interactive shell sandboxing remains
        # disabled globally; an unavailable platform boundary is a refusal.
        from openprogram.backend.local import _invocation

        policy = _sandbox.resolve_policy(required=True)
        assert policy is not None
        args, use_shell, env, _sandboxed = _invocation(
            command,
            str(self.stage_dir),
            policy=policy,
            force_sandbox=True,
        )
        result = subprocess.run(
            args,
            cwd=self.stage_dir,
            shell=use_shell,
            text=True,
            capture_output=True,
            timeout=120,
            env=env,
        )
        changed = self._workspace_fingerprint() != before
        if result.returncode != 0 and changed:
            self._refresh_stage()
        elif result.returncode == 0 and changed:
            self.commit_edits(
                before_units, before_block_ids, before_topics, before_sources
            )
        return result

    def baseline(self) -> tuple[list[Any], set[str], dict[str, str], str]:
        """Snapshot the staged tree so an edit can be committed against it."""
        units = parse_topic_tree(self.stage_dir / "topics")
        block_ids = {unit.memory_id for unit in units}
        return (
            units,
            block_ids,
            self._topic_fingerprints(self.stage_dir / "topics"),
            self._tree_fingerprint(self.stage_dir / "sources"),
        )

    def commit_edits(
        self,
        before_units: list[Any],
        before_block_ids: set[str],
        before_topics: dict[str, str],
        before_sources: str,
    ) -> None:
        """Assign IDs, validate, rebuild derived views, and install.

        Called per shell command, and once after an agent turn that edited
        the stage through the built-in file tools. Any failure discards the
        staged edits and re-raises.
        """
        self.last_changed_topics = []
        self.last_created_blocks = 0
        try:
            if self._allowed_new_source_refs is not None:
                if self._tree_fingerprint(
                    self.stage_dir / "sources"
                ) != before_sources:
                    raise ValueError("Source Memory is append-only")
                self._restore_staged_sources()
                before_sources = self._tree_fingerprint(
                    self.stage_dir / "sources"
                )
            if self._tree_fingerprint(self.stage_dir / "sources") != before_sources:
                raise ValueError("Source Memory is append-only")
            self._normalize_topic_edits(before_block_ids)
            self._validate_topic_contract(before_units, before_block_ids)
            selected_refs = self._allowed_new_source_refs
            if selected_refs is not None:
                before_refs = {
                    ref for unit in before_units for ref in unit.source_refs
                }
                after_refs = {
                    ref
                    for unit in parse_topic_tree(self.stage_dir / "topics")
                    for ref in unit.source_refs
                }
                removed = before_refs - after_refs
                if removed:
                    raise ValueError(
                        "source reference cannot be removed during restricted "
                        f"write: {sorted(removed)[0]}"
                    )
                # The batch the Runtime selected is this commit's evidence.
                # _synchronize checks it per block, which a workspace-wide set
                # difference cannot: a new paragraph would otherwise pass by
                # reusing a reference some other Topic already cites.
                self._transaction_source_refs = frozenset(selected_refs)
            try:
                self._synchronize()
            finally:
                self._transaction_source_refs = None
            after_units = parse_topic_tree(self.stage_dir / "topics")
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

    def stage_is_dirty(self) -> bool:
        """Whether the stage differs from what is committed on disk."""
        staged = {
            path.relative_to(self.stage_dir).as_posix(): path.read_bytes()
            for path in sorted(self.stage_dir.rglob("*"))
            if path.is_file()
            and not is_internal_path(path.relative_to(self.stage_dir))
            and not (
                self._allowed_new_source_refs is not None
                and path.relative_to(self.stage_dir).parts[:1] == ("sources",)
            )
        }
        committed = {
            path.relative_to(self.memory_dir).as_posix(): path.read_bytes()
            for path in sorted(self.memory_dir.rglob("*"))
            if path.is_file()
            and not is_internal_path(path.relative_to(self.memory_dir))
            and not (
                self._allowed_new_source_refs is not None
                and path.relative_to(self.memory_dir).parts[:1] == ("sources",)
            )
        }
        return staged != committed

    def revision(self) -> str:
        """Fingerprint of the committed workspace, for optimistic concurrency."""
        return workspace_revision(self.memory_dir)

    def update(
        self,
        *,
        base_revision: str,
        patch: str,
        sources: Any = None,
        commit_message: str | None = None,
        git_commit: str = "auto",
        limits: TransactionLimits | None = None,
        append_only: bool = False,
        provenance: SourceProvenance | None = None,
    ) -> TransactionResult:
        """Apply sources and a topic patch as one atomic transaction.

        Sources are archived into the stage rather than ``memory_dir`` so that
        evidence and the topics citing it become visible in the same install.
        ``provenance`` is the Runtime's own record of who the sources came
        from; creating sources without it fails closed.
        """
        if git_commit not in ("auto", "on", "off"):
            raise TransactionError(
                "INVALID_ARGUMENT", "git_commit must be auto, on or off"
            )
        limits = limits or TransactionLimits()
        if len(patch.encode("utf-8")) > limits.max_patch_bytes:
            raise TransactionError(
                "INVALID_ARGUMENT",
                f"patch exceeds {limits.max_patch_bytes} bytes",
            )
        message = (commit_message or "Update memory").replace("\n", " ").strip()
        message = "".join(
            char for char in message if char.isprintable()
        )[:limits.max_commit_message_chars] or "Update memory"
        parsed_sources = parse_sources(sources, limits)
        if parsed_sources and provenance is None:
            raise TransactionError(
                "WRITER_PRECONDITION_FAILED",
                "creating a source requires complete persisted authority",
            )

        with workspace_write_lock(self.memory_dir):
            current = workspace_revision(self.memory_dir)
            if base_revision != current:
                raise TransactionError(
                    "CONCURRENT_UPDATE",
                    "workspace changed since base_revision was read",
                    details={"base_revision": base_revision, "revision": current},
                )
            self._refresh_stage()
            before_files = self._committed_files()
            before_units, before_block_ids = committed_baseline(self)
            try:
                records = source_records(parsed_sources, provenance)
                mapping = {
                    item.label: record.source_id
                    for item, record in zip(parsed_sources, records)
                }
                if records:
                    self.archive_source_records(records, root=self.stage_dir)
                # Evidence this transaction archived. A reference a block did
                # not already carry must come from here, so a patch cannot
                # attach an unrelated Source — trusted or not — to new prose.
                self._transaction_source_refs = frozenset(mapping.values())
                resolved = resolve_source_labels(patch, mapping)
                changed = apply_patch(self.stage_dir, resolved)
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
                install_state(self, before_units, before_block_ids)
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
                source_ids=mapping,
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
            lines.append(relative.as_posix())
            if path.suffix != ".md" or topics not in path.parents:
                continue
            heading = ""
            for line in path.read_text(encoding="utf-8").splitlines():
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
