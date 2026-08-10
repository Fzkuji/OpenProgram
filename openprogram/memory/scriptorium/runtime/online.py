"""Online orchestration for incremental Scriptorium maintenance."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..management import MemoryConfig, MemoryWorkspace
from .state import (
    RuntimeStateStore,
    SourceRecord,
    should_global_manage,
    should_incremental_write,
    should_local_reorganize,
)


def unwritten_turns(
    records: list[SourceRecord], marked_ids: set[str] | frozenset[str],
) -> list[SourceRecord]:
    """Unmarked branch suffix after the newest marked source record."""
    pending: list[SourceRecord] = []
    for record in reversed(records):
        if record.message_id in marked_ids:
            break
        pending.append(record)
    pending.reverse()
    return pending


def _parse(value: str | None) -> datetime | None:
    """An ISO 8601 stamp as an aware datetime, or None if it is not one.

    A date-only stamp — what a written record carries — parses naive,
    and subtracting a naive datetime from an aware ``now`` raises, so
    anything without an offset is read as UTC.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class OnlineMemoryRuntime:
    def __init__(
        self,
        memory_dir: Path,
        *,
        token_counter: Callable[[str], int],
        token_threshold: int = 8_000,
        local_batch_threshold: int = 5,
        local_token_threshold: int = 40_000,
        memory_config: MemoryConfig | None = None,
    ):
        self.memory_dir = Path(memory_dir)
        self.store = RuntimeStateStore(self.memory_dir)
        self.token_counter = token_counter
        self.token_threshold = token_threshold
        self.local_batch_threshold = local_batch_threshold
        self.local_token_threshold = local_token_threshold
        self.memory_config = memory_config or MemoryConfig()

    def pending(
        self,
        records: list[SourceRecord],
        marked_ids: set[str] | frozenset[str] = frozenset(),
    ) -> list[SourceRecord]:
        return unwritten_turns(records, marked_ids)

    def process(
        self,
        records: list[SourceRecord],
        writer,
        *,
        marked_ids: set[str] | frozenset[str] = frozenset(),
        mark=None,
        local_manager=None,
        global_manager=None,
        now: datetime | None = None,
        force: bool = False,
    ) -> bool:
        now = now or datetime.now(timezone.utc)
        batch = tuple(self.pending(records, marked_ids))
        if not batch:
            return False
        token_count = sum(self.token_counter(record.content) for record in batch)
        last_timestamp = max(
            (
                stamp for stamp in
                (_parse(record.timestamp) for record in batch)
                if stamp is not None
            ),
            default=now,
        )
        if not force and not should_incremental_write(
            token_count,
            last_timestamp,
            now,
            token_threshold=self.token_threshold,
        ):
            return False

        # The workspace stages a copy of memory under the temp directory,
        # so it is closed however this batch ends.
        with closing(MemoryWorkspace(
            self.memory_dir,
            config=self.memory_config,
        )) as workspace:
            workspace.archive_source_records(list(batch))
            changed_files = writer(workspace, batch)
            if not changed_files:
                return False

            if mark is not None:
                mark(batch)

            state = self.store.load()
            state.local_batches += 1
            state.local_tokens += token_count
            state.write_commits_since_global += 1
            if local_manager and should_local_reorganize(
                state.local_batches,
                state.local_tokens,
                batch_threshold=self.local_batch_threshold,
                token_threshold=self.local_token_threshold,
            ):
                local_manager(workspace)
                state.local_batches = 0
                state.local_tokens = 0
            last_global = _parse(state.last_global_at)
            if global_manager and should_global_manage(
                last_global,
                now,
                new_write_commits=state.write_commits_since_global,
            ):
                global_manager(workspace)
                state.last_global_at = now.isoformat()
                state.write_commits_since_global = 0
        state.creation_order = self.store.load().creation_order
        self.store.git_commit("Scriptorium: incremental memory transaction")
        self.store.save(state)
        return True
