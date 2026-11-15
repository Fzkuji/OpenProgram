"""Local event-level BM25 retrieval over the memory workspace.

The index is deliberately lexical: it uses no embedding model or vector store.
Topic files are indexed; timeline files are excluded because they duplicate
topic events, and the conversations a Topic cites stay in the chat history.

Topic files are read by the one lenient reader, ``read_topic_file``; this
module only turns the units it returns into events. An event is one cited
claim of a block: a paragraph citing two things in two places is two
events, ``<block>:1`` and ``<block>:2``. A paragraph that cites nothing (a
person's note, a file another tool wrote) is one event under its own ID,
with no refs and its ``origin`` saying where it came from.
"""

from __future__ import annotations

import re
import threading
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from rank_bm25 import BM25Plus

from ..markdown.models import MemoryUnit
from ..markdown.parser import read_topic_file, settle_ids, topic_files
from ..workspace_layout import runtime_dir

_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
# Scripts written without spaces, where a whole run would otherwise become one
# token: CJK ideographs (plus extensions A/B and compatibility), hiragana,
# katakana and hangul syllables.
_UNSEGMENTED_RE = re.compile(
    r"[぀-ヿ㐀-䶿一-鿿豈-﫿가-힯]"
    r"|[\U00020000-\U0002a6df]"
)
_DATE_RE = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)")
_TEMPORAL_VALUE_RE = re.compile(r"\d{4}(?:-\d{2}(?:-\d{2})?)?")
_COMMENT_RE = re.compile(r"<!--.*?-->")
_MARKDOWN_LINK_RE = re.compile(r"\[([^]]+)\]\(([^)]+)\)")


@dataclass(frozen=True)
class MemoryEvent:
    event_id: str
    path: str
    line: int
    headings: list[str]
    date: str
    dates: list[str]
    content: str
    refs: list[str]
    # ``cited``, ``person`` or ``outside``: see ``MemoryUnit.origin``.
    origin: str = "cited"


def tokenize(text: str) -> list[str]:
    """Tokenize lexical text without language-model dependencies.

    Space-delimited scripts split on word boundaries. Scripts written without
    spaces are additionally indexed per character and as adjacent pairs: a run
    like "成本是怎么回事" is one word-boundary token, so a query for "成本"
    would never match it, making such memory findable only by repeating the
    whole run verbatim. Emitting the run, its characters and its bigrams keeps
    exact-run matches ranked highest while making substrings retrievable.
    """
    tokens = []
    for token in _WORD_RE.findall(text):
        folded = token.casefold()
        tokens.append(folded)
        characters = _UNSEGMENTED_RE.findall(folded)
        if len(characters) < 2 or len(characters) != len(folded):
            continue
        tokens.extend(characters)
        tokens.extend(
            first + second
            for first, second in zip(characters, characters[1:])
        )
    return tokens


def _clean_markdown(text: str) -> str:
    text = _MARKDOWN_LINK_RE.sub(r"\1", text)
    text = _COMMENT_RE.sub(" ", text)
    return " ".join(text.split())


def temporal_bounds(value: str) -> tuple[date, date]:
    """Expand a year, month, or day to a half-open calendar interval."""
    value = str(value).strip()
    if not _TEMPORAL_VALUE_RE.fullmatch(value):
        raise ValueError(f"invalid temporal value: {value}")
    parts = value.split("-")
    year = int(parts[0])
    try:
        if len(parts) == 1:
            return date(year, 1, 1), date(year + 1, 1, 1)
        month = int(parts[1])
        start = date(year, month, int(parts[2]) if len(parts) == 3 else 1)
        if len(parts) == 3:
            return start, start + timedelta(days=1)
        end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        return start, end
    except ValueError as error:
        raise ValueError(f"invalid temporal value: {value}") from error


def _normalize_path_prefix(path_prefix: str) -> str:
    """Resolve a search prefix against the indexed roots.

    A prefix may name the root ("topics"), a path under it
    ("topics/tooling"), or a path relative to topics ("tooling"). Compare the
    bare root name, not the slash-stripped string: stripping "topics/" down to
    "topics" and then testing for a "topics/" prefix fails, which silently
    rewrote the most common query into "topics/topics" and matched nothing.
    """
    normalized = path_prefix.strip().strip("/")
    if not normalized:
        return ""
    head = normalized.split("/", 1)[0]
    if head == "topics":
        return normalized
    return f"topics/{normalized}"


def event_matches_path_prefix(event_path: str, normalized: str) -> bool:
    return event_path.startswith(normalized)


def _query_time_window(
    date_from: str | None, date_to: str | None
) -> tuple[date | None, date | None] | None:
    if not date_from and not date_to:
        return None
    try:
        start = temporal_bounds(date_from)[0] if date_from else None
    except ValueError as error:
        raise ValueError(f"invalid date_from: {date_from}") from error
    try:
        end = temporal_bounds(date_to)[1] if date_to else None
    except ValueError as error:
        raise ValueError(f"invalid date_to: {date_to}") from error
    if start is not None and end is not None and start >= end:
        raise ValueError("date_from must not be after date_to")
    return start, end


def _event_overlaps_window(
    event: MemoryEvent, window: tuple[date | None, date | None] | None
) -> bool:
    if window is None:
        return True
    query_start, query_end = window
    for value in event.dates:
        try:
            event_start, event_end = temporal_bounds(value)
        except ValueError:
            continue
        if (query_end is None or event_start < query_end) and (
            query_start is None or query_start < event_end
        ):
            return True
    return False


def unit_events(units: list[MemoryUnit]) -> list[MemoryEvent]:
    """Every paragraph of ``units`` as events: one per cited claim, or one
    for the whole paragraph when it cites nothing.

    Whatever a Topic file holds is memory and is found by search; whether
    it cites is said in the result, not decided by admission. A merged
    paragraph is one unit per absorbed ID; its events go under the last,
    its own.
    """
    own: dict[tuple[str, int], MemoryUnit] = {}
    for unit in units:
        own[(unit.topic_path, unit.created_order)] = unit
    events = []
    for unit in own.values():
        if not unit.source_refs:
            content = _clean_markdown(unit.content)
            if content:
                events.append(MemoryEvent(
                    event_id=unit.memory_id,
                    path=f"topics/{unit.topic_path}",
                    line=unit.line,
                    headings=list(unit.headings),
                    date="",
                    dates=[],
                    content=content,
                    refs=[],
                    origin=unit.origin,
                ))
            continue
        claims: dict[int, list] = {}
        for annotation in unit.evidence:
            claims.setdefault(annotation.claim, []).append(annotation)
        for claim, annotations in sorted(claims.items()):
            dates = list(dict.fromkeys(a.when for a in annotations if a.when))
            refs = list(dict.fromkeys(
                ref for a in annotations for ref in a.source_refs
            ))
            content = _clean_markdown(annotations[0].quote)
            if not content or not refs:
                continue
            if dates and not content.startswith("["):
                content = f"[{'; '.join(dates)}] {content}"
            events.append(MemoryEvent(
                event_id=f"{unit.memory_id}:{claim}",
                path=f"topics/{unit.topic_path}",
                line=unit.line,
                headings=list(unit.headings),
                date=dates[0] if dates else "",
                dates=dates,
                content=content,
                refs=refs,
            ))
    return events


def topic_events(memory_dir: Path, files: dict[str, Path]) -> list[MemoryEvent]:
    """Every event of the indexable ``files`` (keyed ``topics/...``)."""
    from ..management.ownership import outside_paths

    topics = Path(memory_dir).resolve() / "topics"
    try:
        outside = outside_paths(Path(memory_dir).resolve())
    except Exception:  # noqa: BLE001 - provenance never stops recall
        outside = set()
    read = [
        (
            relative.removeprefix("topics/"),
            read_topic_file(
                path, topics,
                outside=relative.removeprefix("topics/") in outside,
            )[0],
        )
        for relative, path in sorted(files.items())
    ]
    return unit_events(settle_ids(read).units)


def _indexable_files(
    memory_dir: Path,
    files: list[Path] | tuple[Path, ...] | None = None,
) -> dict[str, Path]:
    root = memory_dir.resolve()
    candidates = topic_files(root / "topics") if files is None else iter(files)

    result = {}
    for candidate in candidates:
        path = Path(candidate)
        path = (root / path).resolve() if not path.is_absolute() else path.resolve()
        try:
            relative = path.relative_to(root)
        except ValueError as error:
            raise ValueError("index path escapes memory workspace") from error
        if (
            len(relative.parts) >= 2
            and relative.parts[0] == "topics"
            and path.suffix.lower() == ".md"
            and path.is_file()
            and not path.is_symlink()
        ):
            result[relative.as_posix()] = path
    return result


class MemoryBM25Index:
    """Incrementally parsed local Topic BM25 index."""

    def __init__(
        self,
        memory_dir: str | Path,
        *,
        files: list[Path] | tuple[Path, ...] | None = None,
    ):
        self.memory_dir = Path(memory_dir).resolve()
        self.topics_dir = self.memory_dir / "topics"
        self._visible_files = None if files is None else tuple(files)
        self._lock = threading.RLock()
        self.events: list[MemoryEvent] = []
        # A per-file cache from before the one reader; reading the whole
        # tree costs tens of milliseconds, and ``inspect`` keeps the index
        # until the workspace changes.
        stale = self.memory_dir / f"{runtime_dir(self.memory_dir).name}-bm25.json"
        if files is None and stale.is_file():
            try:
                stale.unlink()
            except OSError:
                pass
        self.refresh()

    def refresh(self) -> None:
        with self._lock:
            self.events = topic_events(
                self.memory_dir,
                _indexable_files(self.memory_dir, self._visible_files),
            )

    @staticmethod
    def _search_text(event: MemoryEvent) -> str:
        path = event.path.removeprefix("topics/").replace("/", " ").replace("_", " ")
        headings = " ".join(event.headings)
        return f"{event.content} {path} {headings} {' '.join(event.dates)}"

    @staticmethod
    def _rule_adjustment(event: MemoryEvent, query: str, tokens: list[str]) -> tuple[float, list[str]]:
        adjustment = 0.0
        reasons: list[str] = []
        content_tokens = set(tokenize(event.content))
        path_tokens = set(tokenize(event.path + " " + " ".join(event.headings)))
        significant = {token for token in tokens if len(token) >= 3}

        entity_hits = significant & content_tokens
        if entity_hits:
            bonus = min(0.18, 0.03 * len(entity_hits))
            adjustment += bonus
            reasons.append("exact_terms=" + ",".join(sorted(entity_hits)[:5]))

        path_hits = significant & path_tokens
        if path_hits:
            bonus = min(0.15, 0.05 * len(path_hits))
            adjustment += bonus
            reasons.append("path_heading=" + ",".join(sorted(path_hits)[:4]))

        query_dates = set(_DATE_RE.findall(query))
        if query_dates and event.date in query_dates:
            adjustment += 0.12
            reasons.append("exact_date")

        if event.refs:
            adjustment += 0.02
            reasons.append("source_grounded")
        return adjustment, reasons

    def search(
        self,
        query: str,
        *,
        top_k: int = 8,
        path_prefix: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        rerank: bool = True,
        refresh_index: bool = True,
    ) -> list[dict[str, Any]]:
        if refresh_index:
            self.refresh()
        query_tokens = tokenize(query)
        if not query_tokens or not self.events:
            return []

        time_window = _query_time_window(date_from, date_to)
        candidates = []
        for event in self.events:
            if path_prefix:
                normalized = _normalize_path_prefix(path_prefix)
                if not event_matches_path_prefix(event.path, normalized):
                    continue
            if not _event_overlaps_window(event, time_window):
                continue
            candidates.append(event)
        if not candidates:
            return []

        corpus = [tokenize(self._search_text(event)) for event in candidates]
        scores = BM25Plus(corpus).get_scores(query_tokens)
        max_positive = max((float(score) for score in scores if score > 0), default=0.0)
        results = []
        for event, raw_score in zip(candidates, scores):
            raw = float(raw_score)
            lexical = raw / max_positive if max_positive else 0.0
            adjustment, reasons = self._rule_adjustment(event, query, query_tokens) if rerank else (0.0, [])
            final = lexical + adjustment
            if lexical <= 0 and adjustment <= 0.02:
                continue
            results.append({
                **asdict(event),
                "bm25_score": round(raw, 6),
                "lexical_score": round(lexical, 6),
                "rule_adjustment": round(adjustment, 6),
                "final_score": round(final, 6),
                "rule_features": reasons,
            })

        results.sort(key=lambda row: (-row["final_score"], row["path"], row["line"], row["event_id"]))
        return results[: max(1, min(int(top_k), 50))]


#: What a result that cites nothing says instead of its refs.
SOURCE_LINES = {
    "outside": "source: outside file",
    "person": "source: added by person",
}


def source_line(row: dict[str, Any]) -> str:
    """``refs: …`` for a cited result, ``source: …`` for one citing nothing."""
    origin = SOURCE_LINES.get(str(row.get("origin") or ""))
    if origin and not row.get("refs"):
        return f"   {origin}"
    return f"   refs: {', '.join(row.get('refs') or [])}"


def render_search_results(results: list[dict[str, Any]]) -> str:
    if not results:
        return "No BM25 matches."
    blocks = []
    for rank, row in enumerate(results, start=1):
        features = ", ".join(row["rule_features"]) or "none"
        blocks.append(
            f"{rank}. {row['path']}:{row['line']} "
            f"[date={','.join(row['dates']) or 'unknown'}; score={row['final_score']:.4f}; rules={features}]\n"
            f"   {row['content']}\n"
            + source_line(row)
        )
    return "\n".join(blocks)
