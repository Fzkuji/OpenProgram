"""Block-based Topic view synchronization and atomic installation."""

import os
import re
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..runtime.derived_views import (
    promote_legacy_core,
    rebuild_derived_views,
    render_core_block,
)
from ..runtime.state import RuntimeStateStore
from ..markdown import (
    definition_match,
    parse_topic_tree,
    render_definition,
)
from ..workspace_layout import runtime_dir


class BlockViewsMixin:
    def _synchronize(self) -> str:
        # A workspace that still keeps its always-on content at the root
        # hands it to topics/ here, once, so the rest of this runs over
        # one kind of file.
        promote_legacy_core(self.stage_dir)
        return self._synchronize_block_topics(
            parse_topic_tree(self.stage_dir / "topics")
        )

    def _committed_refs_by_block(self) -> dict[str, set[str]]:
        """Source references each committed block already carries.

        Keyed by block ID rather than pooled workspace-wide: a new paragraph
        must not be able to borrow a reference merely because some other
        Topic already cites it.
        """
        return {
            unit.memory_id: set(unit.source_refs)
            for unit in parse_topic_tree(self.memory_dir / "topics")
        }

    def _validate_source_reference(self, ref: str, *, is_new: bool = False) -> None:
        """Check one Topic Source reference against the staged archive.

        ``is_new`` marks a reference this transaction is adding. Only a new
        reference is held to the trust rule: prose already committed keeps
        citing what it cited, and re-validating it would make an unrelated
        edit fail on a neighbouring paragraph's history.
        """
        legacy = re.fullmatch(r"D(\d+):(\d+)", ref)
        if legacy:
            conversation, turn = legacy.groups()
            source = self.stage_dir / "sources" / f"D{conversation}.md"
            anchor = f'<a id="d{conversation}-{turn}"></a>'
        else:
            location, frame = self.resolve_v2_source(ref)
            if location is None:
                # Legacy non-v2 provider archives carry no trust metadata and
                # keep the documented strict compatibility policy.
                location = self._provider_source_location(ref)
                frame = None
            if location is None:
                raise ValueError(f"invalid source reference: {ref}")
            if is_new and frame is not None:
                trust = (frame.metadata or {}).get("trust_state")
                if trust != "trusted":
                    raise ValueError(
                        "source reference is not trusted; promote it first: "
                        f"{ref}"
                    )
            relative, source_anchor = location
            source = self.stage_dir / relative
            anchor = f'<a id="{source_anchor}"></a>'
        if not source.exists() or anchor not in source.read_text(encoding="utf-8"):
            raise ValueError(f"missing source reference: {ref}")

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
        """Refresh relative source and block targets after Topic files move."""
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
            r"#\^(?P<id>[A-Za-z0-9-]+)(?P<suffix>\))"
        )
        for path in sorted(topics.rglob("*.md")):
            relative = path.relative_to(topics).as_posix()
            topic_path = Path("topics") / relative
            annotations = evidence_by_path.get(relative, {})
            rendered = []
            for line in path.read_text(encoding="utf-8").splitlines():
                match = definition_match(line)
                if match and match.group("id") in annotations:
                    annotation = annotations[match.group("id")]
                    labels = re.findall(
                        r"\[([^]]+)\]\([^)]+\)", match.group("sources")
                    )
                    if len(labels) != len(annotation.source_refs):
                        labels = list(annotation.source_refs)
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

    def _synchronize_block_topics(self, units: list[Any]) -> str:
        self._rewrite_block_links(units)
        units = parse_topic_tree(self.stage_dir / "topics")
        ids = {unit.memory_id for unit in units}
        committed_refs = self._committed_refs_by_block()
        # Set only by the structured transaction: the evidence it archived in
        # this very install. None means "no transaction-local restriction",
        # which is what a hand edit or the experiment path gets.
        local_refs = getattr(self, "_transaction_source_refs", None)
        for unit in units:
            missing_targets = set(unit.relation_targets) - ids
            if missing_targets:
                raise ValueError(
                    f"dangling block link: {sorted(missing_targets)[0]}"
                )
            already = committed_refs.get(unit.memory_id, set())
            for ref in unit.source_refs:
                is_new = ref not in already
                self._validate_source_reference(ref, is_new=is_new)
                if is_new and local_refs is not None and ref not in local_refs:
                    raise ValueError(
                        "source reference is not evidence of this "
                        f"transaction: {ref}"
                    )

        limit = self.config.recent_limit
        state_store = RuntimeStateStore(self.stage_dir)
        state = state_store.load()
        derived = rebuild_derived_views(
            self.stage_dir,
            units,
            recent_limit=limit,
            creation_order=state.creation_order,
        )
        state.creation_order = derived.creation_order
        state_store.save(state)
        # The always-on block is a view of one topic file, rebuilt beside
        # the others. Nothing writes it: an edit there is replaced here.
        self.last_core_block = render_core_block(
            self.stage_dir, budget_tokens=self.config.core_max_tokens
        )
        return self._install_staged_state(len(units), "blocks")

    def _install_staged_state(
        self,
        count: int,
        noun: str,
    ) -> str:
        backup = self.memory_dir / f"{runtime_dir(self.memory_dir).name}-block-backup"
        if backup.exists():
            shutil.rmtree(backup)
        backup.mkdir()
        relatives = (
            # Sources are staged by the structured transaction so that new
            # evidence installs together with the topics citing it. The
            # experiment path stages an unmodified copy, so including it here
            # is a no-op there.
            Path("sources"),
            Path("topics"),
            Path("timeline"),
            Path("recent_events.jsonl"),
            Path("relations.json"),
            Path("core.md"),
            Path(runtime_dir(self.memory_dir).name) / "runtime.json",
        )
        moved: list[Path] = []
        installed: list[Path] = []
        try:
            for relative in relatives:
                destination = self.memory_dir / relative
                if destination.exists():
                    saved = backup / relative
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(destination, saved)
                    moved.append(relative)
            for relative in relatives:
                source = self.stage_dir / relative
                if not source.exists():
                    continue
                destination = self.memory_dir / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, destination)
                # Staged sources are read-only so the writer cannot edit them.
                # That guard belongs to the stage: the committed workspace is
                # an ordinary tree the Runtime keeps appending to.
                if destination.is_file():
                    destination.chmod(0o644)
                elif destination.is_dir():
                    for path in destination.rglob("*"):
                        if path.is_file():
                            path.chmod(0o644)
                installed.append(relative)
        except Exception:
            for relative in reversed(installed):
                destination = self.memory_dir / relative
                if destination.is_dir():
                    shutil.rmtree(destination)
                elif destination.exists():
                    destination.unlink()
            for relative in reversed(moved):
                destination = self.memory_dir / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(backup / relative, destination)
            raise
        finally:
            shutil.rmtree(backup, ignore_errors=True)
        self.pending.clear()
        self.committed = True
        self._refresh_stage()
        return f"committed {count} {noun}"
