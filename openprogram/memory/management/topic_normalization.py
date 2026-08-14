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
    definition_match,
    paragraph_spans,
    parse_topic_tree,
    render_definition,
)
from ..markdown.syntax import BLOCK_SUFFIX, BLOCK_TARGET_ID, SINGLE_CITATION

# Footnote labels the writer supplies, e.g. [^e1]. Stable IDs the Runtime
# assigns look like e-1f4c7a2b90, so the digit-only suffix separates them.
# ``new-evidence-<label>`` is the older placeholder form, still accepted.
LOCAL_EVIDENCE_LABEL = re.compile(r"e\d+|new-evidence-[A-Za-z0-9-]+")
CITATION = re.compile(r"\[\^([A-Za-z0-9_-]+)\]")


def _is_local_label(value: str) -> bool:
    return bool(LOCAL_EVIDENCE_LABEL.fullmatch(value))


def prune_empty_topic_file(path: Path) -> None:
    """Remove record-free sections and delete a Topic with no records."""
    if not path.is_file():
        return
    lines = path.read_text(encoding="utf-8").splitlines()
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
    path.write_text("\n".join(kept).rstrip() + "\n", encoding="utf-8")


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

    def _normalize_topic_edits(self, existing_block_ids: set[str]) -> None:
        # Callers that need to report assigned IDs read these afterwards.
        self.last_block_id_map: dict[str, str] = {}
        self.last_evidence_id_map: dict[str, str] = {}
        topics = self.stage_dir / "topics"
        if not topics.exists():
            return
        paths = sorted(topics.rglob("*.md"))
        texts = {path: path.read_text(encoding="utf-8") for path in paths}
        # What is on disk now. `texts` is rewritten in place below, so the
        # final write needs its own record of the original to compare against.
        on_disk = dict(texts)

        # Evidence labels are content-addressed, so the same claim keeps its
        # footnote ID no matter what else the edit touched.
        used_evidence = {
            match.group(1)
            for text in texts.values()
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
                        match.group("when"), match.group("sources").strip()
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
            for text in texts.values()
            for match in re.finditer(r"(?m)\^([A-Za-z0-9-]+)\s*$", text)
        }
        assigned: dict[Path, dict[int, str]] = {}
        for path, text in texts.items():
            lines = text.splitlines()
            for start, end in self._paragraph_spans(text):
                last = lines[end - 1]
                if re.search(r"\s\^[A-Za-z0-9-]+\s*$", last):
                    continue
                body = " ".join(
                    " ".join(lines[start:end]).split()
                )
                # Only evidence-bearing prose is a memory. Topic intros
                # carry no footnote and stay unidentified.
                if not body or "[^" not in body:
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
                    linked_sources = re.findall(
                        r"\[[^]]+\]\([^)]+\)", raw_sources
                    )
                    values = linked_sources or re.split(
                        r"\s*(?:,|·)\s*", raw_sources
                    )
                    sources = []
                    for value in values:
                        value = value.strip()
                        if re.fullmatch(r"\[[^]]+\]\([^)]+\)", value):
                            sources.append(value)
                        elif value:
                            sources.append(
                                self._source_link(topic_path, value)
                            )
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
        for path in sorted(topics.rglob("*.md")):
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
        units = parse_topic_tree(self.stage_dir / "topics")
        if before_block_ids:
            lost = before_block_ids - {unit.memory_id for unit in units}
            if lost:
                raise ValueError(
                    "block ID must not be removed: "
                    + ", ".join(sorted(lost)[:5])
                )
        for unit in units:
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
