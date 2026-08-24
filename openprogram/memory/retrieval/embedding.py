"""Read-only event-level embedding retrieval over the memory workspace."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from .bm25 import (
    MemoryEvent,
    _event_overlaps_window,
    _indexable_files,
    _query_time_window,
    _normalize_path_prefix,
    event_matches_path_prefix,
    parse_source_file,
    parse_topic_file,
    prefer_v2_source_events,
    resolve_topic_trust,
)
from .embedding_model import (
    MODEL_FILES,
    MODEL_ID,
    default_model_is_cached,
    install_default_model,
)
_default_encoder: Any | None = None
_default_encoder_lock = threading.RLock()


def load_default_encoder(*, local_files_only: bool = True) -> Any:
    """Load the fixed encoder once without downloading runtime assets."""
    global _default_encoder
    if _default_encoder is None:
        with _default_encoder_lock:
            if _default_encoder is None:
                try:
                    from sentence_transformers import SentenceTransformer
                except ImportError as exc:
                    raise ImportError(
                        "semantic memory is unavailable because "
                        "sentence-transformers is not installed"
                    ) from exc

                _default_encoder = SentenceTransformer(
                    MODEL_ID, local_files_only=local_files_only,
                )
    return _default_encoder


def default_model_is_available() -> bool:
    try:
        load_default_encoder(local_files_only=True)
    except Exception:
        return False
    return True


class MemoryEmbeddingIndex:
    """Rebuild an in-memory embedding index from Topic and Source files."""

    def __init__(
        self,
        memory_dir: str | Path,
        *,
        encoder: Any | None = None,
        files: list[Path] | tuple[Path, ...] | None = None,
    ):
        self.memory_dir = Path(memory_dir).resolve()
        self.topics_dir = self.memory_dir / "topics"
        self.sources_dir = self.memory_dir / "sources"
        self._visible_files = None if files is None else tuple(files)
        self._encoder = encoder
        self._events_cache: list[MemoryEvent] | None = None
        self._document_vectors: Any | None = None
        self._lock = threading.RLock()

    @property
    def encoder(self) -> Any:
        if self._encoder is None:
            with self._lock:
                if self._encoder is None:
                    self._encoder = load_default_encoder(local_files_only=True)
        return self._encoder

    def _events(self) -> list[MemoryEvent]:
        if self._events_cache is not None:
            return self._events_cache
        with self._lock:
            if self._events_cache is not None:
                return self._events_cache
            events = []
            source_lookup: dict[Path, dict[str, str]] = {}
            for relative, path in sorted(
                _indexable_files(
                    self.memory_dir, self._visible_files
                ).items()
            ):
                if relative.startswith("sources/"):
                    events.extend(parse_source_file(path, self.sources_dir))
                else:
                    events.extend(parse_topic_file(
                        path,
                        self.topics_dir,
                        source_lookup=source_lookup,
                    ))
            self._events_cache = resolve_topic_trust(
                prefer_v2_source_events(events)
            )
        return self._events_cache

    @staticmethod
    def _search_text(event: MemoryEvent) -> str:
        headings = " ".join(event.headings)
        return f"{event.content} {event.path} {headings} {event.date}"

    def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        date_from: str | None = None,
        date_to: str | None = None,
        path_prefix: str | None = None,
    ) -> list[dict[str, Any]]:
        query = str(query).strip()
        events = self._events()
        if not query or not events:
            return []

        import numpy as np

        if self._document_vectors is None:
            with self._lock:
                if self._document_vectors is None:
                    documents = [self._search_text(event) for event in events]
                    self._document_vectors = np.asarray(
                        self.encoder.encode(documents), dtype=float
                    )
        time_window = _query_time_window(date_from, date_to)
        candidate_indices = [
            index
            for index, event in enumerate(events)
            if _event_overlaps_window(event, time_window)
            and (
                not path_prefix
                or event_matches_path_prefix(
                    event.path, _normalize_path_prefix(path_prefix),
                )
            )
        ]
        if not candidate_indices:
            return []
        candidate_events = [events[index] for index in candidate_indices]
        document_vectors = self._document_vectors[candidate_indices]
        with self._lock:
            query_vector = np.asarray(
                self.encoder.encode([query]), dtype=float
            )[0]
        document_norms = np.linalg.norm(document_vectors, axis=1)
        query_norm = np.linalg.norm(query_vector)
        denominators = document_norms * query_norm
        similarities = np.divide(
            document_vectors @ query_vector,
            denominators,
            out=np.zeros(len(candidate_events), dtype=float),
            where=denominators != 0,
        )

        results = [
            {
                "event": event.event_id,
                "path": event.path,
                "line": event.line,
                "date": event.date,
                "content": event.content,
                "refs": event.refs,
                "trust_state": event.trust_state,
                "speaker_kind": event.speaker_kind,
                "speaker_id": event.speaker_id,
                "speaker_display": event.speaker_display,
                "principal_id": event.principal_id,
                "authority_tier": event.authority_tier,
                "similarity": float(similarity),
            }
            for event, similarity in zip(candidate_events, similarities)
        ]
        results.sort(
            key=lambda row: (
                -row["similarity"], row["path"], row["line"], row["event"]
            )
        )
        return results[: max(1, min(int(top_k), 50))]


def render_search_results(results: list[dict[str, Any]]) -> str:
    if not results:
        return "No embedding matches."
    return "\n".join(
        f"{rank}. {row['path']}:{row['line']} "
        f"[date={row['date'] or 'unknown'}; "
        f"similarity={row['similarity']:.4f}]\n"
        f"   {row['content']}\n"
        f"   refs: {', '.join(row['refs'])}"
        for rank, row in enumerate(results, start=1)
    )
