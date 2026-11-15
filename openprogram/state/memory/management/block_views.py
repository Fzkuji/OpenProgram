"""Block-based Topic view synchronization and atomic installation."""

import os
import re
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import quote

from openprogram.host._platform.filesystem import replace_file

from ..runtime.derived_views import (
    promote_legacy_core,
    rebuild_derived_views,
    render_core_block,
)
from ..runtime import memory_guide
from ..runtime.state import RuntimeStateStore
from ..markdown.syntax import BLOCK_TARGET_ID
from ..markdown import (
    definition_match,
    read_topic_tree,
    render_definition,
)
from .. import chat_refs
from ..workspace_layout import runtime_dir
from .transaction import TransactionError, snapshot_outside_edits

# Committed memory is owner-only, matching the profile root.
MEMORY_FILE_MODE = 0o600
MEMORY_DIR_MODE = 0o700


class BlockViewsMixin:
    def _synchronize(self) -> str:
        from ..policy import commit_guard
        with commit_guard(self.memory_dir):
            return self._synchronize_authorized()

    def _synchronize_authorized(self) -> str:
        # A workspace that still keeps its always-on content at the root
        # hands it to topics/ here, once, so the rest of this runs over
        # one kind of file.
        promote_legacy_core(self.stage_dir)
        return self._synchronize_block_topics(self._parse_stage())

    def _committed_units(self) -> list[Any]:
        """The committed tree, read the way memory is read."""
        return read_topic_tree(
            self.memory_dir / "topics",
            outside=getattr(self, "outside", frozenset()),
        ).units

    def _committed_refs_by_block(self) -> dict[str, set[str]]:
        """Source references each committed block already carries.

        Keyed by block ID rather than pooled workspace-wide: a new paragraph
        must not be able to borrow a reference merely because some other
        Topic already cites it. A paragraph in a file written outside
        vouches for nothing: what it cites is checked as new.
        """
        return {
            unit.memory_id: set(unit.source_refs)
            for unit in self._committed_units() if not unit.outside
        }

    def _automatic(self) -> bool:
        """Whether this write is an automatic writer's or the organizer's,
        not a person's: the author whose every paragraph must cite."""
        from ..policy import current, owner_editing

        if owner_editing():
            return False
        return (
            current() is not None
            or getattr(self, "_allowed_new_source_refs", None) is not None
        )

    def _validate_source_reference(self, ref: str, *, is_new: bool = False) -> None:
        """Check one Topic citation against the chat history.

        ``is_new`` marks a citation this transaction is adding: it must name
        a message the chat still holds and memory may rest a claim on.
        Prose already committed keeps citing what it cited, including a
        chat that was deleted since; re-checking it would make an unrelated
        edit fail on a neighbouring paragraph's history.
        """
        if chat_refs.split(ref) is None:
            raise ValueError(f"invalid source reference: {ref}")
        if not is_new:
            return
        lookup = getattr(self, "_chat_lookup", None)
        if lookup is None:
            lookup = self._chat_lookup = chat_refs.ChatLookup()
        if lookup.message(ref) is None:
            raise ValueError(f"missing source reference: {ref}")
        reason = lookup.refusal(ref)
        if reason is not None:
            raise TransactionError(
                "SOURCE_NOT_ALLOWED",
                "source reference is not a message memory may cite: "
                f"{chat_refs.link(ref)} "
                f"({reason}); cite a message a person or the assistant wrote "
                "in a conversation memory learns from",
                path=None,
            )

    def _uses_block_topic_format(self, units: list[Any]) -> bool:
        if any(unit.evidence for unit in units):
            return True
        suffix = re.compile(r"(?m)\s\^[A-Za-z0-9-]+\s*$")
        for root in (self.stage_dir / "topics", self.memory_dir / "topics"):
            if root.exists() and any(
                suffix.search(path.read_text(encoding="utf-8"))
                for path in root.rglob("*.md")
            ):
                return True
        return False

    def _rewrite_block_links(self, units: list[Any]) -> None:
        """Refresh relative block targets after Topic files move.

        Footnotes are rewritten too, into the canonical chat link: a writer
        may cite the bare reference it was shown.
        """
        topics = self.stage_dir / "topics"
        target_paths = {unit.memory_id: unit.topic_path for unit in units}
        evidence_by_path = {
            path: {
                annotation.citation_id: annotation
                for unit in units if unit.topic_path == path
                for annotation in unit.evidence
            }
            for path in {unit.topic_path for unit in units}
        }
        block_link = re.compile(
            r"(?P<prefix>\[[^]\n]+\]\()(?P<path>[^)\n#]*)"
            rf"#\^(?P<id>{BLOCK_TARGET_ID})(?P<suffix>\))"
        )
        outside = getattr(self, "is_outside", lambda _relative: False)
        for path in sorted(topics.rglob("*.md")):
            relative = path.relative_to(topics).as_posix()
            if outside(relative):
                # Not ours to rewrite, links and footnotes included.
                continue
            topic_path = Path("topics") / relative
            annotations = evidence_by_path.get(relative, {})
            rendered = []
            for line in path.read_text(encoding="utf-8").splitlines():
                match = definition_match(line)
                if match and match.group("id") in annotations:
                    annotation = annotations[match.group("id")]
                    labels = (
                        annotation.source_labels
                        if len(annotation.source_labels)
                        == len(annotation.source_refs)
                        else annotation.source_refs
                    )
                    line = render_definition(
                        match.group("id"),
                        annotation.when,
                        (
                            self._source_link(topic_path, ref, label)
                            for ref, label in zip(annotation.source_refs, labels)
                        ),
                    )

                def replace_block(match: re.Match[str]) -> str:
                    target = target_paths.get(match.group("id"))
                    if target is None:
                        return match.group(0)
                    relative_target = os.path.relpath(
                        Path("topics") / target, topic_path.parent
                    ).replace(os.sep, "/")
                    return (
                        match.group("prefix")
                        + quote(relative_target, safe="/._-")
                        + "#^"
                        + match.group("id")
                        + match.group("suffix")
                    )

                rendered.append(block_link.sub(replace_block, line))
            normalized = "\n".join(rendered).rstrip() + "\n"
            if normalized != path.read_text(encoding="utf-8"):
                path.write_text(normalized, encoding="utf-8")

    @staticmethod
    def _source_link(_topic_path: Path, ref: str, label: str | None = None) -> str:
        # A citation given without a label still needs readable link text.
        return f"[{label or 'chat'}]({chat_refs.link(ref)})"

    def _check_block_references(self, units: list[Any]) -> list[Any]:
        """Rewrite block links after moves, then check every link and the
        sources of every paragraph this write changed. Raises on the first
        one that does not hold.

        Sources are checked here, by author, and nowhere else: a paragraph
        an automatic writer or the organizer added or rewrote must cite
        the chat messages it rests on, from this execution and this batch;
        a person's own edit may cite nothing. Paragraphs nobody changed,
        and files written outside, are not this write's to answer for.
        """
        self._rewrite_block_links(units)
        units = self._parse_stage()
        ids = {unit.memory_id for unit in units}
        outside_ids = {unit.memory_id for unit in units if unit.outside}
        committed = {
            unit.memory_id: unit for unit in self._committed_units()
        }
        committed_refs = {
            memory_id: set(unit.source_refs)
            for memory_id, unit in committed.items() if not unit.outside
        }
        automatic = self._automatic()
        # The messages this write may newly cite: a writer's batch, or the
        # conversation a chat edit came from. None means "no restriction
        # beyond the chat history", which is what a hand edit gets.
        local_refs = getattr(self, "_transaction_source_refs", None)
        for unit in units:
            if unit.outside:
                continue
            previous = committed.get(unit.memory_id)
            missing_targets = set(unit.relation_targets) - ids
            if missing_targets:
                raise ValueError(
                    f"dangling block link: {sorted(missing_targets)[0]}"
                )
            added_links = set(unit.relation_targets) - (
                set(previous.relation_targets) if previous is not None else set()
            )
            into_outside = sorted(added_links & outside_ids)
            if into_outside:
                raise TransactionError(
                    "OUTSIDE_FILE_READ_ONLY",
                    f"^{into_outside[0]} is in a file written outside "
                    "OpenProgram, which memory reads but never links into",
                    path=f"topics/{unit.topic_path}",
                )
            # Moving a paragraph is not writing it: only new or reworded
            # text answers for its sources.
            if previous is not None and (
                previous.content, previous.evidence,
            ) == (unit.content, unit.evidence):
                continue
            if automatic and not unit.source_refs and not unit.derived_id:
                raise TransactionError(
                    "MISSING_SOURCE",
                    f"^{unit.memory_id} in topics/{unit.topic_path} cites "
                    "no chat message: every paragraph a writer adds or "
                    "rewrites ends with a footnote that links the message "
                    "it rests on",
                    path=f"topics/{unit.topic_path}",
                )
            already = committed_refs.get(unit.memory_id, set())
            for ref in unit.source_refs:
                is_new = ref not in already
                self._validate_source_reference(ref, is_new=is_new)
                if is_new and local_refs is not None and ref not in local_refs:
                    raise TransactionError(
                        "SOURCE_NOT_ALLOWED",
                        "source reference is not evidence of this "
                        f"transaction: {ref}",
                        path=f"topics/{unit.topic_path}",
                    )
        return units

    def _synchronize_block_topics(self, units: list[Any]) -> str:
        units = self._check_block_references(units)
        limit = self.config.recent_limit
        state_store = RuntimeStateStore(self.stage_dir)
        state = state_store.load()
        from ..runtime import first_seen

        derived = rebuild_derived_views(
            self.stage_dir,
            units,
            recent_limit=limit,
            creation_order=state.creation_order,
            first_seen=first_seen.dates(self.memory_dir, units),
        )
        state.creation_order = derived.creation_order
        state_store.save(state)
        # The core section of MEMORY.md is a view of one topic file,
        # rebuilt beside the others: an edit there is replaced here.
        if (
            not (self.stage_dir / "topics" / "core.md").is_file()
            and (self.memory_dir / "topics" / "core.md").is_file()
        ):
            (self.stage_dir / "core.md").unlink(missing_ok=True)
            if (self.stage_dir / memory_guide.GUIDE_FILE).is_file():
                memory_guide.write_core(self.stage_dir, "")
        self.last_core_block = render_core_block(
            self.stage_dir, budget_tokens=self.config.core_max_tokens
        )
        # The map is the memory agents' to keep; the Runtime only writes a
        # first one for a workspace that has none, and takes out the lines
        # of Topics this write removed.
        memory_guide.ensure(self.stage_dir)
        memory_guide.drop_removed(self.stage_dir)
        return self._install_staged_state(len(units), "blocks")

    def _install_staged_state(
        self,
        count: int,
        noun: str,
    ) -> str:
        before_install = getattr(self, "_before_install", None)
        self._before_install = None
        if before_install is not None:
            # The surface keeps its own history around this install.
            before_install()
        else:
            # Edits made outside this write get their own commit first, so
            # this write's commit holds only what it changed.
            snapshot_outside_edits(self.memory_dir)
        from . import ownership

        # Whose each committed file was, before this install replaces them.
        owned_before = ownership.snapshot(self.memory_dir / "topics")
        backup = self.memory_dir / f"{runtime_dir(self.memory_dir).name}-block-backup"
        if backup.exists():
            shutil.rmtree(backup)
        backup.mkdir()
        relatives = (
            Path("topics"),
            Path("timeline"),
            Path("recent_events.jsonl"),
            Path("commitments.jsonl"),
            Path("relations.json"),
            # The old root core.md: listed so installing removes it.
            Path("core.md"),
            Path(memory_guide.GUIDE_FILE),
            Path(runtime_dir(self.memory_dir).name) / "runtime.json",
        )
        moved: list[Path] = []
        installed: list[Path] = []
        self._stage_usable = False
        try:
            for relative in relatives:
                destination = self.memory_dir / relative
                if destination.exists():
                    saved = backup / relative
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    replace_file(destination, saved)
                    moved.append(relative)
            for relative in relatives:
                source = self.stage_dir / relative
                if not source.exists():
                    continue
                destination = self.memory_dir / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                replace_file(source, destination)
                # Owner-only, because installing is where every memory file
                # gets its committed mode.
                if destination.is_file():
                    destination.chmod(MEMORY_FILE_MODE)
                elif destination.is_dir():
                    destination.chmod(MEMORY_DIR_MODE)
                    for path in destination.rglob("*"):
                        if path.is_symlink():
                            continue
                        path.chmod(
                            MEMORY_DIR_MODE if path.is_dir() else MEMORY_FILE_MODE
                        )
                installed.append(relative)
        except BaseException as primary:
            rollback_errors: list[BaseException] = []
            for relative in reversed(installed):
                destination = self.memory_dir / relative
                try:
                    if destination.is_dir():
                        shutil.rmtree(destination)
                    elif destination.exists():
                        destination.unlink()
                except BaseException as exc:
                    rollback_errors.append(exc)
            for relative in reversed(moved):
                destination = self.memory_dir / relative
                try:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    replace_file(backup / relative, destination)
                except BaseException as exc:
                    rollback_errors.append(exc)
            if not rollback_errors:
                try:
                    shutil.rmtree(backup, ignore_errors=True)
                except BaseException as exc:
                    rollback_errors.append(exc)
            for error in rollback_errors:
                primary.add_note(f"memory install rollback failed: {error}")
            raise
        shutil.rmtree(backup, ignore_errors=True)
        ownership.record_install(self.memory_dir, owned_before)
        # The map's list of files written outside is redrawn at installs
        # only, so the prompt's memory section moves once per write.
        memory_guide.refresh_outside_map(self.memory_dir)
        self.pending.clear()
        self.committed = True
        self._refresh_stage()
        return f"committed {count} {noun}"
