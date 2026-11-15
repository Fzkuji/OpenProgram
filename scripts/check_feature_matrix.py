#!/usr/bin/env python3
"""Validate feature-matrix values derived from its canonical table."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
import re
from typing import Sequence


SYMBOL_SCORES = {"●": 1.0, "◐": 0.5, "○": 0.5, "·": 0.0}
REFERENCE_NAMES = (
    "claude",
    "codex",
    "openclaw",
    "opencode",
    "hermes",
    "pi-mono",
    "pi-ai",
    "weclaw",
    "cc内",
    "cx内",
    "gm内",
    "buddy",
)
REFERENCE_NAMES_EN = (*REFERENCE_NAMES[:8], "cc internal", "cx internal", "gm internal", "buddy")
HEADERS = ("功能", "OpenProgram", "其他项目")
HEADERS_EN = ("Feature", "OpenProgram", "Confirmed elsewhere")
FRAMEWORK_NAMES = ("我们", *REFERENCE_NAMES)
FRAMEWORK_NAMES_EN = ("Ours", *REFERENCE_NAMES_EN)
EXPECTED_SNAPSHOT = "2fb471b3"
EXPECTED_INTEGRATION_SNAPSHOT = "d91c3e66"
JSON_SCHEMA_ROW = "按 JSON schema 约束输出"
EXPECTED_SSRF_SNAPSHOT = "be5eaa3c"
SSRF_ROW = "私网访问与 SSRF 防护"


class MatrixError(ValueError):
    pass


@dataclass(frozen=True)
class FeatureRow:
    name: str
    evidence: str
    category: str
    cells: tuple[str, ...]


@dataclass(frozen=True)
class MatrixResult:
    feature_count: int
    openprogram_score: float
    openprogram_gaps: int
    openprogram_only: int
    json_schema_status: str
    snapshot: str


def _is_hidden(attributes: dict[str, str | None]) -> bool:
    style = (attributes.get("style") or "").replace(" ", "").lower()
    return (
        "hidden" in attributes
        or attributes.get("aria-hidden") == "true"
        or "display:none" in style
        or "visibility:hidden" in style
    )


def _reference_cells(name: str, text: str) -> tuple[str, ...]:
    """Expand "● claude, codex · ◐ buddy" into one status per reference column."""
    aliases = {
        alias: index
        for names in (REFERENCE_NAMES, REFERENCE_NAMES_EN)
        for index, alias in enumerate(names)
    }
    cells = ["·"] * len(REFERENCE_NAMES)
    seen: set[int] = set()
    if text in {"None confirmed", "无确认"}:
        return tuple(cells)
    for group in text.split(" · "):
        status, _, members = group.partition(" ")
        if status not in {"●", "◐", "○"} or not members:
            raise MatrixError(f"{name!r} has invalid status {group!r}")
        for member in members.split(", "):
            index = aliases.get(member)
            if index is None:
                raise MatrixError(f"{name!r} has invalid status {member!r}")
            if index in seen:
                raise MatrixError(f"{name!r} lists {member!r} twice")
            seen.add(index)
            cells[index] = status
    return tuple(cells)


class _MatrixParser(HTMLParser):
    """Read the feature rows: one folded table per category under ``#matrix``."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.section_count = 0
        self.in_section = False
        self.in_group = False
        self.in_group_summary = False
        self.group_summary: list[str] = []
        self.category = ""
        self.declared_count = 0
        self.group_rows = 0
        self.in_table = False
        self.in_row = False
        self.in_cell = False
        self.cell_scope: str | None = None
        self.in_evidence = False
        self.in_evidence_summary = False
        self.cell_name: list[str] = []
        self.cell_evidence: list[str] = []
        self.cells: list[tuple[str, str, str]] = []
        self.outside_row_header = False
        self.headers: list[tuple[str, ...]] = []
        self.rows: list[FeatureRow] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "h2" and attributes.get("id") == "matrix":
            self.section_count += 1
            if self.section_count > 1:
                raise MatrixError("duplicate canonical matrix table")
            self.in_section = True
            return
        if tag == "h3" and attributes.get("id") == "cover":
            self.in_section = False
            return
        if not self.in_section:
            if tag == "th" and attributes.get("scope") == "row":
                self.outside_row_header = True
            elif tag == "details" and self.outside_row_header:
                raise MatrixError("feature row exists outside the canonical matrix table")
            return
        if tag in {"details", "table", "tbody", "tr"} and _is_hidden(attributes):
            raise MatrixError("canonical matrix table must be visible")
        if tag == "details":
            if self.in_cell:
                self.in_evidence = True
            elif self.in_group or self.in_table:
                raise MatrixError("category tables must not nest")
            else:
                self.in_group = True
                self.category = ""
                self.declared_count = 0
                self.group_rows = 0
        elif tag == "summary":
            if self.in_evidence:
                self.in_evidence_summary = True
            elif self.in_group:
                self.in_group_summary = True
                self.group_summary = []
        elif tag == "table" and self.in_group:
            if not self.category:
                raise MatrixError("feature row appears before a category")
            self.in_table = True
        elif tag == "tr" and self.in_table:
            self.in_row = True
            self.cells = []
        elif self.in_row and tag in {"td", "th"}:
            self.in_cell = True
            self.cell_scope = attributes.get("scope")
            self.cell_name = []
            self.cell_evidence = []

    def handle_data(self, data: str) -> None:
        if self.in_group_summary:
            self.group_summary.append(data)
        elif self.in_evidence_summary or not self.in_cell:
            return
        elif self.in_evidence:
            self.cell_evidence.append(data)
        else:
            self.cell_name.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "th":
            self.outside_row_header = False
        if not self.in_section:
            return
        if tag == "summary":
            if self.in_group_summary:
                self.in_group_summary = False
                heading = " ".join("".join(self.group_summary).split())
                match = re.fullmatch(r"\d+\s+(.+?)\s+(\d+)\s*(?:项|features)", heading)
                if not match:
                    raise MatrixError("category heading is malformed")
                self.category = match.group(1)
                self.declared_count = int(match.group(2))
            self.in_evidence_summary = False
        elif tag == "details":
            if self.in_evidence:
                self.in_evidence = False
            elif self.in_group:
                self.in_group = False
                if self.group_rows != self.declared_count:
                    raise MatrixError(
                        f"category heading count for {self.category} does not match its rows"
                    )
        elif tag in {"td", "th"} and self.in_cell:
            name = " ".join("".join(self.cell_name).split())
            evidence = " ".join("".join(self.cell_evidence).split())
            self.cells.append((self.cell_scope or "", name, evidence))
            self.in_cell = False
        elif tag == "tr" and self.in_row:
            self.in_row = False
            if all(scope == "col" for scope, _name, _evidence in self.cells):
                self.headers.append(tuple(cell[1] for cell in self.cells))
                return
            if len(self.cells) != 3 or self.cells[0][0] != "row":
                raise MatrixError("feature row does not have status cells")
            name = self.cells[0][1]
            self.rows.append(
                FeatureRow(
                    name=name,
                    evidence=self.cells[0][2],
                    category=self.category,
                    cells=(self.cells[1][1], *_reference_cells(name, self.cells[2][1])),
                )
            )
            self.group_rows += 1
        elif tag == "table":
            self.in_table = False


def _published_rows(source: str, start_marker: str, end_marker: str, cells: int) -> list[tuple[str, ...]]:
    """The body rows of a published summary table, as plain text."""
    start = source.find(start_marker)
    end = source.find(end_marker, start)
    if start < 0 or end < 0:
        raise MatrixError(f"missing published section {start_marker}")
    rows: list[tuple[str, ...]] = []
    for body in re.findall(r"<tr[^>]*>(.*?)</tr>", source[start:end], re.DOTALL):
        if 'scope="col"' in body:
            continue
        values = tuple(
            " ".join(unescape(re.sub(r"<[^>]+>", "", value)).split())
            for value in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", body, re.DOTALL)
        )
        if len(values) != cells:
            raise MatrixError(f"published row in {start_marker} is malformed")
        rows.append(values)
    return rows


def _published_integer(source: str, pattern: str, label: str) -> int:
    raw_matches = re.findall(pattern, source)
    matches = [
        int(next(value for value in item if value))
        if isinstance(item, tuple)
        else int(item)
        for item in raw_matches
    ]
    if not matches:
        raise MatrixError(f"missing published {label}")
    if len(set(matches)) != 1:
        raise MatrixError(f"published {label} values disagree: {matches}")
    return matches[0]


def _visible_detail_rows(
    source: str,
    start_marker: str,
    end_marker: str,
    *,
    include_category: bool,
    label: str,
) -> list[tuple[str, ...]]:
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    rows: list[tuple[str, ...]] = []
    for attrs, body in re.findall(
        r"<tr([^>]*)>(.*?)</tr>", source[start:end], re.DOTALL
    ):
        if re.search(
            r"(?:^|\s)(?:hidden|aria-hidden\s*=)", attrs, re.IGNORECASE
        ) or re.search(
            r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", attrs, re.IGNORECASE
        ):
            raise MatrixError(f"hidden {label} row")
        name = re.search(r'<td class="name">(.*?)</td>', body, re.DOTALL)
        if not name:
            continue

        def plain(fragment: str) -> str:
            return " ".join(unescape(re.sub(r"<[^>]+>", "", fragment)).split())

        values = [plain(name.group(1))]
        if include_category:
            category = re.search(r'<td class="sz">(.*?)</td>', body, re.DOTALL)
            if not category:
                raise MatrixError(f"{label} row is missing its category")
            values.append(plain(category.group(1)))
        rows.append(tuple(values))
    return rows


def _validate_detail_group_counts(
    source: str,
    start_marker: str,
    end_marker: str,
    *,
    label: str,
) -> None:
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    declared: dict[str, int] = {}
    actual: Counter[str] = Counter()
    current_group: str | None = None

    def plain(fragment: str) -> str:
        return " ".join(unescape(re.sub(r"<[^>]+>", "", fragment)).split())

    for attrs, body in re.findall(
        r"<tr([^>]*)>(.*?)</tr>", source[start:end], re.DOTALL
    ):
        if re.search(
            r"(?:^|\s)(?:hidden|aria-hidden\s*=)", attrs, re.IGNORECASE
        ) or re.search(
            r"(?:display\s*:\s*none|visibility\s*:\s*hidden)",
            attrs,
            re.IGNORECASE,
        ):
            raise MatrixError(f"hidden {label} row")
        classes = re.search(r'class="([^"]*)"', attrs)
        if classes and "grp" in classes.group(1).split():
            heading = re.search(
                r"<b[^>]*>(.*?)</b>\s*<span[^>]*>(\d+)\s*(?:项|features)</span>",
                body,
                re.DOTALL,
            )
            if not heading:
                raise MatrixError(f"published {label} group heading is malformed")
            current_group = plain(heading.group(1))
            if current_group in declared:
                raise MatrixError(f"duplicate published {label} group")
            declared[current_group] = int(heading.group(2))
        elif re.search(r'<td class="name">', body):
            if current_group is None:
                raise MatrixError(f"published {label} row appears before a group")
            actual[current_group] += 1

    if set(declared) != set(actual):
        raise MatrixError(f"published {label} groups are incomplete")
    for group, expected in declared.items():
        if actual[group] != expected:
            raise MatrixError(
                f"published {label} group count for {group} does not match its rows"
            )


def _detail_identity(value: str) -> str:
    return "".join(value.split())


def check_matrix(path: Path) -> MatrixResult:
    source = path.read_text(encoding="utf-8")
    parser = _MatrixParser()
    parser.feed(source)
    if parser.section_count != 1:
        raise MatrixError(
            f"expected one canonical matrix table, found {parser.section_count}"
        )
    if not parser.headers or len(set(parser.headers)) != 1 or parser.headers[0] not in (HEADERS, HEADERS_EN):
        raise MatrixError("framework headers are stale")
    framework_names = FRAMEWORK_NAMES_EN if parser.headers[0] == HEADERS_EN else FRAMEWORK_NAMES
    if len({row.name for row in parser.rows}) != len(parser.rows):
        raise MatrixError("feature row is duplicated")
    if len(parser.rows) != 160:
        raise MatrixError(f"feature row count is stale: {len(parser.rows)}")

    scores = [0.0] * 13
    gaps = 0
    only = 0
    for row in parser.rows:
        if len(row.cells) != 13:
            raise MatrixError(f"{row.name!r} does not have 13 framework cells")
        for index, cell in enumerate(row.cells):
            if cell not in SYMBOL_SCORES:
                raise MatrixError(f"{row.name!r} has invalid status {cell!r}")
            scores[index] += SYMBOL_SCORES[cell]
        gaps += row.cells[0] == "·" and any(cell != "·" for cell in row.cells[1:])
        only += row.cells[0] != "·" and all(cell == "·" for cell in row.cells[1:])

    json_rows = [row for row in parser.rows if row.name in {JSON_SCHEMA_ROW, "JSON-schema-constrained output"}]
    if len(json_rows) != 1:
        raise MatrixError("JSON Schema row is missing or duplicated")
    json_row = json_rows[0]
    if json_row.cells[0] != "●":
        raise MatrixError(
            "JSON Schema status must remain at the user-approved matrix value"
        )
    if EXPECTED_SNAPSHOT not in json_row.evidence:
        raise MatrixError("JSON Schema snapshot is stale")
    if source.count(EXPECTED_SNAPSHOT) < 4 or "deadbeef" in source:
        raise MatrixError("published snapshot evidence is stale")
    if source.count(f"integrated candidate@{EXPECTED_INTEGRATION_SNAPSHOT}") < 3:
        raise MatrixError("integration snapshot evidence is stale")
    if ("runtime llm(response_format=…) 的 JSON schema 结构化输出" in source
            or "runtime llm(response_format=…) JSON schema structured output" in source):
        raise MatrixError("old snapshot still claims complete JSON Schema support")

    ssrf_rows = [row for row in parser.rows if row.name in {SSRF_ROW, "Private-network access and SSRF protection"}]
    if len(ssrf_rows) != 1:
        raise MatrixError("SSRF row is missing or duplicated")
    ssrf_row = ssrf_rows[0]
    if ssrf_row.cells[0] != "◐":
        raise MatrixError("SSRF status must match the integrated evidence gate")
    if EXPECTED_SSRF_SNAPSHOT not in ssrf_row.evidence:
        raise MatrixError("SSRF snapshot is stale")

    score_rows = _published_rows(source, '<h3 id="cover"', '<h3 id="bycat"', 2)
    published_scores: dict[str, float] = {}
    for name, label in score_rows:
        if not re.fullmatch(r"[0-9.]+", label):
            raise MatrixError(f"published score for {name} is not numeric")
        published_scores[name] = float(label)
    if len(score_rows) != len(framework_names) or set(published_scores) != set(framework_names):
        raise MatrixError("published framework score rows are stale")
    for name, expected in zip(framework_names, scores):
        if published_scores[name] != expected:
            raise MatrixError(
                f"published score for {name} does not match the canonical table"
            )
    summary = re.findall(r"(?:Feature matrix, |功能矩阵、)([0-9.]+)(?: score| 分)", source)
    if not summary or any(float(value) != scores[0] for value in summary):
        raise MatrixError("published score summary does not match the canonical table")

    published_gaps = _published_integer(
        source,
        r"(?:未确认的|参考列已确认的|这)\s*(\d+)\s*项|功能矩阵、[0-9.]+\s*分、(\d+)\s*个候选差异"
        r"|(\d+) features unconfirmed in OpenProgram|Feature matrix, [0-9.]+ score, (\d+) candidate differences",
        "gaps",
    )
    if published_gaps != gaps:
        raise MatrixError("published gaps do not match the canonical table")
    published_only = _published_integer(
        source,
        r"仅 OpenProgram 确认的\s*(\d+)\s*项|功能矩阵、[0-9.]+\s*分、\d+\s*个候选差异、(\d+)\s*个快照差异"
        r"|(\d+) features (?:currently )?confirmed only in OpenProgram"
        r"|Feature matrix, [0-9.]+ score, \d+ candidate differences, (\d+) version-evidence differences",
        "OpenProgram-only count",
    )
    if published_only != only:
        raise MatrixError(
            "published OpenProgram-only count does not match the canonical table"
        )

    expected_gap_details = Counter(
        (_detail_identity(row.name), row.category)
        for row in parser.rows
        if row.cells[0] == "·" and any(cell != "·" for cell in row.cells[1:])
    )
    published_gap_details = Counter(
        (_detail_identity(name), category)
        for name, category in _visible_detail_rows(
            source,
            '<h2 id="gaps"',
            '<h3 id="newfound"',
            include_category=True,
            label="gap detail",
        )
    )
    if published_gap_details != expected_gap_details:
        raise MatrixError("published gap detail rows do not match the canonical table")
    _validate_detail_group_counts(
        source,
        '<h2 id="gaps"',
        '<h3 id="newfound"',
        label="gap detail",
    )

    expected_only_details = Counter(
        (_detail_identity(row.name),)
        for row in parser.rows
        if row.cells[0] != "·" and all(cell == "·" for cell in row.cells[1:])
    )
    published_only_details = Counter(
        (_detail_identity(name),)
        for (name,) in _visible_detail_rows(
            source,
            '<h2 id="ours"',
            '<h2 id="plan"',
            include_category=False,
            label="OpenProgram-only detail",
        )
    )
    if published_only_details != expected_only_details:
        raise MatrixError(
            "published OpenProgram-only detail rows do not match the canonical table"
        )

    category_rows_published = _published_rows(source, '<h3 id="bycat"', '<h2 id="gaps"', 3)
    published_categories = {row[0]: row[1:] for row in category_rows_published}
    categories = {row.category for row in parser.rows}
    if len(category_rows_published) != len(categories) or set(published_categories) != categories:
        raise MatrixError("published category rows are stale")
    for category in categories:
        category_rows = [row for row in parser.rows if row.category == category]
        framework_scores = [
            sum(SYMBOL_SCORES[row.cells[index]] for row in category_rows)
            for index in range(13)
        ]
        reference_scores = sorted(
            score
            for index, score in enumerate(framework_scores[1:], start=1)
            if index not in {7, 8}
        )
        median = (
            reference_scores[len(reference_scores) // 2 - 1]
            + reference_scores[len(reference_scores) // 2]
        ) / 2
        percent = lambda value: round(100 * value / len(category_rows))
        expected_ours = f"{percent(framework_scores[0])}%"
        expected_range = (
            f"{percent(reference_scores[0])}%–{percent(reference_scores[-1])}%, "
            f"(?:median|中位数) {percent(median)}%"
        )
        ours, reference_range = published_categories[category]
        if ours != expected_ours or not re.fullmatch(expected_range, reference_range):
            raise MatrixError(f"published category point for {category} is stale")

    legend_start = source.index('<h3 id="bycat"')
    legend_start = source.index('<p class="note">', source.index("</figure>", legend_start))
    legend_end = source.index("</p>", legend_start)
    legend = " ".join(
        unescape(re.sub(r"<[^>]+>", "", source[legend_start:legend_end])).split()
    )
    leaders: list[str] = []
    gaps_by_category: list[tuple[int, str]] = []
    for category in categories:
        category_rows = [row for row in parser.rows if row.category == category]
        framework_scores = [
            sum(SYMBOL_SCORES[row.cells[index]] for row in category_rows)
            for index in range(13)
        ]
        references = [
            score
            for index, score in enumerate(framework_scores[1:], start=1)
            if index not in {7, 8}
        ]
        op_percent = round(100 * framework_scores[0] / len(category_rows))
        reference_max = round(100 * max(references) / len(category_rows))
        if framework_scores[0] > max(references):
            leaders.append(category)
        gaps_by_category.append((reference_max - op_percent, category))
    for category in leaders:
        if category.casefold() not in legend.casefold():
            raise MatrixError("published category legend is stale")
    for _gap, category in sorted(gaps_by_category, reverse=True)[:3]:
        if category.casefold() not in legend.casefold():
            raise MatrixError("published category legend is stale")

    return MatrixResult(
        feature_count=len(parser.rows),
        openprogram_score=scores[0],
        openprogram_gaps=gaps,
        openprogram_only=only,
        json_schema_status=json_row.cells[0],
        snapshot=EXPECTED_SNAPSHOT,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=Path("docs/reference/design/feature-matrix.html"),
    )
    args = parser.parse_args(argv)
    try:
        result = check_matrix(args.path)
    except (OSError, MatrixError) as exc:
        parser.exit(1, f"feature-matrix: {exc}\n")
    print(
        "feature-matrix: "
        f"features={result.feature_count} "
        f"openprogram_score={result.openprogram_score:g} "
        f"openprogram_gaps={result.openprogram_gaps} "
        f"openprogram_only={result.openprogram_only}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
