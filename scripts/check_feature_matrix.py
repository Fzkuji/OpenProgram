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
HEADERS = (
    "功能",
    "我们",
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
FRAMEWORK_NAMES = HEADERS[1:]
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


class _MatrixParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.table_count = 0
        self.in_matrix = False
        self.in_row = False
        self.row_classes: set[str] = set()
        self.in_cell = False
        self.cell_classes: set[str] = set()
        self.cell_name: list[str] = []
        self.cell_full: list[str] = []
        self.cells: list[tuple[set[str], str, str]] = []
        self.source_span = False
        self.category = ""
        self.headers: list[tuple[str, ...]] = []
        self.rows: list[FeatureRow] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "table" and "fx" in classes:
            style = (attributes.get("style") or "").replace(" ", "").lower()
            if (
                "hidden" in attributes
                or attributes.get("aria-hidden") == "true"
                or "display:none" in style
                or "visibility:hidden" in style
            ):
                raise MatrixError("canonical matrix table must be visible")
            self.table_count += 1
            if self.in_matrix:
                raise MatrixError("duplicate canonical matrix table")
            self.in_matrix = True
        elif tag == "td" and "fname" in classes and not self.in_matrix:
            raise MatrixError("feature row exists outside the canonical matrix table")
        if not self.in_matrix:
            return
        if tag == "tr":
            self.in_row = True
            self.row_classes = classes
            self.cells = []
        elif self.in_row and tag in {"td", "th"}:
            self.in_cell = True
            self.cell_classes = classes
            self.cell_name = []
            self.cell_full = []
        elif self.in_cell and tag == "span" and "src" in classes:
            self.source_span = True

    def handle_data(self, data: str) -> None:
        if not self.in_cell:
            return
        self.cell_full.append(data)
        if not self.source_span:
            self.cell_name.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self.in_matrix:
            return
        if tag == "span" and self.source_span:
            self.source_span = False
        elif tag in {"td", "th"} and self.in_cell:
            name = " ".join("".join(self.cell_name).split())
            full = " ".join("".join(self.cell_full).split())
            self.cells.append((self.cell_classes, name, full))
            self.in_cell = False
        elif tag == "tr" and self.in_row:
            if self.cells:
                if "grp" in self.row_classes:
                    match = re.search(r"\d+\s+(.+?)\s+\d+\s*项", self.cells[0][1])
                    if not match:
                        raise MatrixError("category heading is malformed")
                    self.category = match.group(1)
                elif (
                    all("hc" not in classes for classes, _name, _full in self.cells)
                    and self.cells[0][1] == "功能"
                ):
                    self.headers.append(tuple(cell[1] for cell in self.cells))
                elif all(
                    "hc" not in classes for classes, _name, _full in self.cells
                ) and self.cells[0][0] == {"fname"}:
                    if not self.category:
                        raise MatrixError("feature row appears before a category")
                    self.rows.append(
                        FeatureRow(
                            name=self.cells[0][1],
                            evidence=self.cells[0][2],
                            category=self.category,
                            cells=tuple(cell[1] for cell in self.cells[1:]),
                        )
                    )
            self.in_row = False
        elif tag == "table":
            self.in_matrix = False


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
                r"<b[^>]*>(.*?)</b>\s*<span[^>]*>(\d+)\s*项</span>",
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
    if parser.table_count != 1:
        raise MatrixError(
            f"expected one canonical matrix table, found {parser.table_count}"
        )
    if parser.headers != [HEADERS]:
        raise MatrixError("framework headers are stale")
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

    json_rows = [row for row in parser.rows if row.name == JSON_SCHEMA_ROW]
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
    if "runtime llm(response_format=…) 的 JSON schema 结构化输出" in source:
        raise MatrixError("old snapshot still claims complete JSON Schema support")

    ssrf_rows = [row for row in parser.rows if row.name == SSRF_ROW]
    if len(ssrf_rows) != 1:
        raise MatrixError("SSRF row is missing or duplicated")
    ssrf_row = ssrf_rows[0]
    if ssrf_row.cells[0] != "◐":
        raise MatrixError("SSRF status must match the integrated evidence gate")
    if EXPECTED_SSRF_SNAPSHOT not in ssrf_row.evidence:
        raise MatrixError("SSRF snapshot is stale")

    score_svg = re.search(r'<svg viewBox="0 0 960 330".*?</svg>', source, re.DOTALL)
    if not score_svg:
        raise MatrixError("missing published score SVG")
    score_rows = re.findall(
        r'<g transform="translate\(0,[^)]+\)"><text[^>]*>([^<]+)</text>'
        r'<rect[^>]+width="([0-9.]+)".*?<text[^>]*>([^<]+)</text></g>',
        score_svg.group(),
        re.DOTALL,
    )
    published_scores: dict[str, tuple[float, float]] = {}
    for name, width, label in score_rows:
        visible = re.match(r"[0-9.]+", label)
        if not visible:
            raise MatrixError(f"published score for {name} is not numeric")
        published_scores[name] = (float(width), float(visible.group()))
    if set(published_scores) != set(FRAMEWORK_NAMES):
        raise MatrixError("published framework score rows are stale")
    for name, expected in zip(FRAMEWORK_NAMES, scores):
        width, visible = published_scores[name]
        if visible != expected or width != max(2, expected * 4):
            raise MatrixError(
                f"published score for {name} does not match the canonical table"
            )
    aria = re.search(r"OpenProgram为([0-9.]+)分", source)
    if not aria or float(aria.group(1)) != scores[0]:
        raise MatrixError("published score ARIA does not match the canonical table")

    published_gaps = _published_integer(
        source,
        r"(?:未确认的|参考列已确认的|这)\s*(\d+)\s*项|功能矩阵、[0-9.]+\s*分、(\d+)\s*个候选差异",
        "gaps",
    )
    if published_gaps != gaps:
        raise MatrixError("published gaps do not match the canonical table")
    published_only = _published_integer(
        source,
        r"仅 OpenProgram 确认的\s*(\d+)\s*项|功能矩阵、[0-9.]+\s*分、\d+\s*个候选差异、(\d+)\s*个快照差异",
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

    category_svg = re.search(r'<svg viewBox="0 0 960 400".*?</svg>', source, re.DOTALL)
    if not category_svg:
        raise MatrixError("missing published category SVG")
    category_points: dict[
        str, tuple[int, float, float, float, float, float, float, str]
    ] = {}
    for name, count, body in re.findall(
        r'<g transform="translate\(0,[^)]+\)"><text[^>]*>([^<]+?)\s+(\d+)</text>'
        r"(.*?)</g>",
        category_svg.group(),
        re.DOTALL,
    ):
        line = re.search(r'<line x1="([0-9.]+)"[^>]+x2="([0-9.]+)"', body)
        markers = re.findall(
            r'<line x1="([0-9.]+)"[^>]+x2="\1"[^>]*/>', body
        )
        openprogram = re.search(
            r'<circle cx="([0-9.]+)"[^>]+fill="#[0-9a-fA-F]{6}"', body
        )
        if not line or len(markers) != 3 or not openprogram:
            raise MatrixError(f"published category graphics for {name} are incomplete")
        category_points[name] = (
            int(count),
            float(line.group(1)),
            float(line.group(2)),
            float(markers[0]),
            float(markers[1]),
            float(markers[2]),
            float(openprogram.group(1)),
            " ".join(unescape(re.sub(r"<[^>]+>", "", body)).split()),
        )
    categories = {row.category for row in parser.rows}
    if set(category_points) != categories:
        raise MatrixError("published category rows are stale")
    for category in categories:
        category_rows = [row for row in parser.rows if row.category == category]
        framework_scores = [
            sum(SYMBOL_SCORES[row.cells[index]] for row in category_rows)
            for index in range(13)
        ]
        (
            count,
            line_x1,
            line_x2,
            marker_min_x,
            marker_max_x,
            median_x,
            openprogram_x,
            visible_label,
        ) = category_points[category]
        expected_openprogram = round(
            200 + 600 * framework_scores[0] / len(category_rows)
        )
        reference_scores = [
            score
            for index, score in enumerate(framework_scores[1:], start=1)
            if index not in {7, 8}
        ]
        expected_reference_min = round(
            200 + 600 * min(reference_scores) / len(category_rows)
        )
        expected_reference_max = round(
            200 + 600 * max(reference_scores) / len(category_rows)
        )
        ordered_references = sorted(reference_scores)
        expected_reference_median = round(
            200
            + 600
            * (
                ordered_references[len(ordered_references) // 2 - 1]
                + ordered_references[len(ordered_references) // 2]
            )
            / 2
            / len(category_rows)
        )
        expected_openprogram_percent = round(100 * framework_scores[0] / len(category_rows))
        expected_reference_percent = round(100 * max(reference_scores) / len(category_rows))
        expected_reference_names = {
            FRAMEWORK_NAMES[index]
            for index, score in enumerate(framework_scores[1:], start=1)
            if index not in {7, 8} and score == max(reference_scores)
        }
        if (
            count != len(category_rows)
            or openprogram_x != expected_openprogram
            or (line_x1, line_x2)
            != (expected_reference_min, expected_reference_max)
            or (marker_min_x, marker_max_x)
            != (expected_reference_min, expected_reference_max)
            or median_x != expected_reference_median
            or f"{expected_reference_percent}%" not in visible_label
            or not any(name in visible_label for name in expected_reference_names)
            or (
                framework_scores[0] > max(reference_scores)
                and f"我们 {expected_openprogram_percent}%" not in visible_label
            )
        ):
            raise MatrixError(f"published category point for {category} is stale")

    legend_start = source.index('<div class="card legend">', category_svg.end())
    legend_end = source.index("</div>", legend_start)
    legend = " ".join(
        unescape(re.sub(r"<[^>]+>", "", source[legend_start:legend_end])).split()
    )
    leaders: list[str] = []
    gaps_by_category: list[tuple[float, str, int, int, int]] = []
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
        reference_min = round(100 * min(references) / len(category_rows))
        reference_max = round(100 * max(references) / len(category_rows))
        if framework_scores[0] > max(references):
            leaders.append(category)
        gaps_by_category.append(
            (
                reference_max - op_percent,
                category,
                op_percent,
                reference_min,
                reference_max,
            )
        )
    for category in leaders:
        if category not in legend:
            raise MatrixError("published category legend is stale")
    for _gap, category, op_percent, reference_min, reference_max in sorted(
        gaps_by_category, reverse=True
    )[:3]:
        if (
            category not in legend
            or f"{op_percent}%" not in legend
            or f"{reference_min}%–{reference_max}%" not in legend
        ):
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
