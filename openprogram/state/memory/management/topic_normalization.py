"""Deterministic normalization and validation of Topic edits."""

import hashlib
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from ..markdown import (
    BLOCK_ID_LENGTH,
    MEMORY_ID,
    TopicFormatError,
    definition_match,
    paragraph_spans,
    render_definition,
)
from ..markdown.syntax import (
    BLOCK_SUFFIX,
    BLOCK_TARGET_ID,
    LINK,
    SINGLE_CITATION,
)
from .. import chat_refs

# Footnote labels the writer supplies, e.g. [^e1]. Stable IDs the Runtime
# assigns look like e-1f4c7a2b90, so the digit-only suffix separates them.
# ``new-evidence-<label>`` is the older placeholder form, still accepted.
LOCAL_EVIDENCE_LABEL = re.compile(r"e\d+|new-evidence-[A-Za-z0-9-]+")
CITATION = re.compile(r"\[\^([A-Za-z0-9_-]+)\]")


def _is_local_label(value: str) -> bool:
    return bool(LOCAL_EVIDENCE_LABEL.fullmatch(value))


def drop_leftover_definitions(text: str) -> str:
    """Repair two mechanical slips instead of rejecting the edit.

    A definition line written twice keeps one copy. A Runtime definition
    (``[^e-…]``) that nothing in its file cites any more is left over from
    a paragraph that was deleted or moved, and goes. A writer's own unused
    ``[^eN]`` stays: it may be evidence whose citation the writer forgot,
    and only the writer can say where it belongs.
    """
    lines = text.splitlines()
    cited = {
        label
        for line in lines
        if not definition_match(line)
        for label in CITATION.findall(line)
    }
    kept: list[str] = []
    seen: set[str] = set()
    for line in lines:
        match = definition_match(line)
        if match:
            label = match.group("id")
            if line.strip() in seen or (
                label not in cited and not _is_local_label(label)
            ):
                continue
            seen.add(line.strip())
        kept.append(line)
    if len(kept) == len(lines):
        return text
    return "\n".join(kept) + ("\n" if text.endswith("\n") else "")


def _source_identity_seed(value: str) -> str:
    links = LINK.findall(value)
    if links:
        return "|".join(target.strip() for _label, target in links)
    return "|".join(
        item.strip()
        for item in re.split(r"\s*(?:,|·)\s*", value)
        if item.strip()
    )


def holds_no_paragraph(text: str) -> bool:
    """Whether a Topic's text is headings only, which pruning removes.

    Such a Topic and no Topic are the same state of memory.
    """
    return next(paragraph_spans(text.splitlines()), None) is None


def prune_empty_topic_file(path: Path) -> None:
    """Remove record-free sections and delete a Topic with no records.

    Every paragraph of a file normalization has passed carries a block ID,
    so a record is any paragraph at all; what goes is headings with
    nothing under them.
    """
    if not path.is_file():
        return
    original = path.read_text(encoding="utf-8")
    lines = original.splitlines()
    defined = {
        match.group("id")
        for line in lines
        if (match := definition_match(line))
    }
    record_paths: list[tuple[str, ...]] = []
    referenced: set[str] = set()
    for start, end, headings in paragraph_spans(lines):
        paragraph = "\n".join(lines[start:end])
        citations = set(SINGLE_CITATION.findall(paragraph))
        if BLOCK_SUFFIX.search(paragraph) or citations & defined:
            record_paths.append(headings)
            referenced.update(citations)
        else:
            # Empty-topic pruning cannot acknowledge discarded substantive text.
            # Use the same prose/record grammar as strict Topic validation.
            raise TopicFormatError(f"memory source footnote required: {path.name}")
    if not record_paths:
        path.unlink()
        return
    kept: list[str] = []
    active = True
    headings: list[str] = []
    for line in lines:
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            level = len(match.group(1))
            headings = headings[: level - 1] + [match.group(2)]
            current = tuple(headings)
            active = any(
                record[: len(current)] == current for record in record_paths
            )
        definition = definition_match(line)
        if active or (
            definition is not None and definition.group("id") in referenced
        ):
            kept.append(line)
    pruned = "\n".join(kept).rstrip() + "\n"
    # Every Topic passes through here on every write; rewriting an
    # unchanged file would stamp the whole tree as just modified.
    if pruned != original:
        path.write_text(pruned, encoding="utf-8")


class TopicNormalizationMixin:
    @staticmethod
    def _topic_fingerprints(root: Path) -> dict[str, str]:
        if not root.exists():
            return {}
        return {
            path.relative_to(root).as_posix(): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in root.rglob("*.md")
        }

    @staticmethod
    def _stable_local_id(
        seed: str, used: set[str], *, prefix: str = ""
    ) -> str:
        counter = 0
        while True:
            value = prefix + hashlib.sha256(
                f"{seed}|{counter}".encode()
            ).hexdigest()[: 10 if prefix else BLOCK_ID_LENGTH]
            if value not in used:
                used.add(value)
                return value
            counter += 1

    @staticmethod
    def _paragraph_spans(text: str) -> list[tuple[int, int]]:
        return [
            (start, end)
            for start, end, _headings in paragraph_spans(text.splitlines())
        ]

    def _normalize_topic_edits(
        self,
        existing_block_ids: set[str],
        previous_units: list[Any] | None = None,
    ) -> None:
        """Give the files this write wrote their IDs and canonical form.

        Only those files: a file the write left alone keeps its bytes,
        whatever they hold, and a file written outside OpenProgram is
        never rewritten (see ``ownership``). Every paragraph of a written
        file gets a block ID, cited or not; whether a paragraph must cite
        depends on who wrote it, and the commit checks that.
        """
        # Callers that need to report assigned IDs read these afterwards.
        self.last_block_id_map: dict[str, str] = {}
        self.last_evidence_id_map: dict[str, str] = {}
        topics = self.stage_dir / "topics"
        if not topics.exists():
            return
        written = getattr(self, "_written_paths", None)
        written = written() if written is not None else None
        outside = getattr(self, "is_outside", lambda _relative: False)
        all_paths = sorted(
            path for path in topics.rglob("*.md")
            if not outside(path.relative_to(topics).as_posix())
        )
        # IDs already used anywhere in memory's own files stay taken.
        every_text = {
            path: path.read_text(encoding="utf-8", errors="replace")
            for path in all_paths
        }
        paths = [
            path for path in all_paths
            if written is None or path.relative_to(topics).as_posix() in written
        ]
        texts = {path: every_text[path] for path in paths}
        # What is on disk now. `texts` is rewritten in place below, so the
        # final write needs its own record of the original to compare against.
        on_disk = dict(texts)

        # Evidence labels are content-addressed, so the same claim keeps its
        # footnote ID no matter what else the edit touched.
        used_evidence = {
            match.group(1)
            for text in every_text.values()
            for match in re.finditer(r"(?m)^\[\^([A-Za-z0-9_-]+)\]:", text)
            if not _is_local_label(match.group(1))
        }
        evidence_ids: dict[str, str] = {}
        for text in texts.values():
            for line in text.splitlines():
                match = definition_match(line)
                if match is None or not _is_local_label(match.group("id")):
                    continue
                label = match.group("id")
                if label in evidence_ids:
                    continue
                evidence_ids[label] = self._stable_local_id(
                    "evidence|{}|{}".format(
                        match.group("when"),
                        _source_identity_seed(match.group("sources")),
                    ),
                    used_evidence,
                    prefix="e-",
                )

        # Appending to a paragraph, a writer often rewrites the citation it
        # already carries as a fresh `[^eN]` without moving the definition.
        # The definition is still in the file and evidence IDs are derived
        # from its content, so the citation is recoverable: bind the orphan
        # back to the definition that shares its paragraph. Discarding the
        # turn instead would throw away correct new prose over a label.
        for path, text in list(texts.items()):
            lines = text.splitlines()
            defined = {
                match.group("id")
                for line in lines
                if (match := definition_match(line))
            }
            replaced = False
            for start, end in self._paragraph_spans(text):
                block = "\n".join(lines[start:end])
                orphans = [
                    label
                    for label in CITATION.findall(block)
                    if _is_local_label(label) and label not in defined
                ]
                if not orphans:
                    continue
                # The definitions that follow this paragraph, in order.
                nearby = [
                    match.group("id")
                    for line in lines[end:]
                    if (match := definition_match(line))
                ][: len(orphans)]
                for orphan, target in zip(orphans, nearby):
                    if orphan == target:
                        continue
                    lines[start:end] = [
                        line.replace(f"[^{orphan}]", f"[^{target}]")
                        for line in lines[start:end]
                    ]
                    replaced = True
            if replaced:
                texts[path] = "\n".join(lines) + (
                    "\n" if text.endswith("\n") else ""
                )

        for path, text in list(texts.items()):
            texts[path] = drop_leftover_definitions(text)

        # A writer may still invent a trailing ID out of habit. Anything that
        # was not already committed is stripped so the paragraph is treated as
        # new and the Runtime assigns the real ID.
        invented = re.compile(
            r"[ \t]*\^(?!(?:" + "|".join(
                re.escape(value) for value in sorted(existing_block_ids)
            ) + r")\b)[A-Za-z0-9-]+(?=\s*$)"
            if existing_block_ids
            else r"[ \t]*\^[A-Za-z0-9-]+(?=\s*$)",
            re.MULTILINE,
        )
        for path, text in list(texts.items()):
            stripped = invented.sub("", text)
            if stripped != text:
                texts[path] = stripped

        # Block IDs are assigned to paragraphs that carry none. An existing
        # ID is never rewritten: other views reach the paragraph through it.
        used_blocks = set(existing_block_ids) | {
            match.group(1)
            for text in every_text.values()
            for match in re.finditer(r"(?m)\^([A-Za-z0-9-]+)\s*$", text)
        }
        assigned: dict[Path, dict[int, str]] = {}
        from ..markdown.parser import without_front_matter

        for path, text in texts.items():
            lines = text.splitlines()
            # Front matter is metadata, not a paragraph.
            prose = without_front_matter(lines)
            for start, end, _headings in paragraph_spans(prose):
                last = lines[end - 1]
                if re.search(r"\s\^[A-Za-z0-9-]+\s*$", last):
                    continue
                body = " ".join(
                    " ".join(lines[start:end]).split()
                )
                # Every paragraph is a memory, cited or not: a person's
                # note gets its ID like a writer's claim.
                if not body:
                    continue
                citation_ids = CITATION.findall(body)
                if citation_ids and all(
                    re.fullmatch(MEMORY_ID, value) for value in citation_ids
                ):
                    continue
                assigned.setdefault(path, {})[end - 1] = (
                    self._stable_local_id(
                        f"block|{path.name}|{body}", used_blocks
                    )
                )

        # Keyed by the workspace-relative file and the assignment order
        # within it, so a caller can tell which paragraph got which ID.
        self.last_block_id_map = {
            "{}#{}".format(
                (Path("topics") / path.relative_to(topics)).as_posix(), index
            ): value
            for path, rows in assigned.items()
            for index, (_line, value) in enumerate(sorted(rows.items()))
        }
        self.last_evidence_id_map = dict(evidence_ids)
        for path, original in texts.items():
            text = original
            for label, stable in evidence_ids.items():
                text = re.sub(
                    rf"\[\^{re.escape(label)}\](?![A-Za-z0-9_-])",
                    f"[^{stable}]",
                    text,
                )
            rows = assigned.get(path)
            if rows:
                lines = text.splitlines()
                for line_number, value in rows.items():
                    if line_number < len(lines):
                        lines[line_number] = (
                            lines[line_number].rstrip() + f" ^{value}"
                        )
                text = "\n".join(lines)
            rendered = []
            topic_path = Path("topics") / path.relative_to(topics)
            for line in text.splitlines():
                match = definition_match(line)
                if match:
                    raw_sources = match.group("sources")
                    sources = []
                    linked_sources = LINK.findall(raw_sources)
                    # A writer may cite the bare reference it was shown, or
                    # the link form; both become the canonical chat link.
                    for label, target in linked_sources:
                        ref = chat_refs.from_target(target)
                        sources.append(
                            self._source_link(topic_path, ref, label)
                            if ref is not None
                            else f"[{label}]({target.strip()})"
                        )
                    if not linked_sources:
                        for value in re.split(r"\s*(?:,|·)\s*", raw_sources):
                            value = value.strip()
                            ref = chat_refs.from_target(value)
                            if ref is not None:
                                sources.append(self._source_link(topic_path, ref))
                            elif value:
                                sources.append(value)
                    line = render_definition(
                        match.group("id"),
                        None
                        if match.group("when") == "undated"
                        else match.group("when"),
                        sources,
                    )
                else:
                    line = re.sub(
                        r"[ \t]+\[\^([A-Za-z0-9_-]+)\]",
                        r"[^\1]",
                        line,
                    )
                    line = re.sub(
                        r"[ \t]*\^([A-Za-z0-9-]+)\s*$",
                        r" ^\1",
                        line,
                    )
                rendered.append(line)
            normalized = "\n".join(rendered).rstrip() + "\n"
            if normalized != on_disk[path]:
                path.write_text(normalized, encoding="utf-8")
        from ..policy import owner_editing

        # A person's save keeps what they wrote, headings included, and the
        # file with it; a writer's edit drops what it left empty.
        if not owner_editing():
            for path in paths:
                prune_empty_topic_file(path)

    def _validate_topic_contract(
        self,
        before_units: list[Any],
        before_block_ids: set[str] | None = None,
    ) -> None:
        """Reject edits that drop a block ID or break a Topic link.

        A block ID is how Timeline, Relations, and other paragraphs reach a
        memory. Content may change and paragraphs may move or merge, but an
        ID that existed before the edit must still be findable after it.
        """
        before = {unit.memory_id: unit for unit in before_units}
        units = self._parse_stage()
        if before_block_ids:
            lost = before_block_ids - {unit.memory_id for unit in units}
            if lost:
                raise ValueError(
                    "block ID must not be removed: "
                    + ", ".join(sorted(lost)[:5])
                )
        for unit in units:
            if unit.outside:
                continue
            previous = before.get(unit.memory_id)
            if previous is not None and (
                unit.content,
                unit.evidence,
            ) == (
                previous.content,
                previous.evidence,
            ):
                continue
            for target in re.findall(
                r"\[[^]\n]+\]\(([^)\n]+)\)", unit.content
            ):
                path, separator, fragment = target.partition("#")
                if (
                    not path
                    or Path(path).is_absolute()
                    or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", path)
                ):
                    continue
                relative = Path(os.path.normpath(
                    str(Path(unit.topic_path).parent / unquote(path))
                ))
                if (
                    relative.suffix.lower() == ".md"
                    and ".." not in relative.parts
                    and (
                        not separator
                        or re.fullmatch(rf"\^{BLOCK_TARGET_ID}", fragment) is None
                    )
                ):
                    raise ValueError(
                        "Topic-to-Topic link must target #^block-id: "
                        f"{unit.topic_path} -> {target}"
                    )

    @staticmethod
    def _tree_fingerprint(root: Path) -> str:
        digest = hashlib.sha256()
        if root.exists():
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    digest.update(
                        path.relative_to(root).as_posix().encode()
                    )
                    digest.update(path.read_bytes())
        return digest.hexdigest()
