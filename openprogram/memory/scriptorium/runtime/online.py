"""Online orchestration for incremental Scriptorium maintenance."""

from __future__ import annotations

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

    def pending(self, records: list[SourceRecord]) -> list[SourceRecord]:
        state = self.store.load()
        return sorted(
            [
                record for record in records
                if record.ordinal > int(
                    state.cursors.get(record.thread_id, {}).get("ordinal", -1)
                )
            ],
            key=lambda record: (record.thread_id, record.ordinal),
        )

    def process(
        self,
        records: list[SourceRecord],
        writer,
        *,
        local_manager=None,
        global_manager=None,
        now: datetime | None = None,
        force: bool = False,
    ) -> bool:
        now = now or datetime.now(timezone.utc)
        batch = tuple(self.pending(records))
        if not batch:
            return False
        token_count = sum(self.token_counter(record.content) for record in batch)
        last_timestamp = max(
            (
                datetime.fromisoformat(record.timestamp)
                for record in batch if record.timestamp
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

        workspace = MemoryWorkspace(
            self.memory_dir,
            config=self.memory_config,
        )
        workspace.archive_source_records(list(batch))
        writer(workspace, batch)

        state = self.store.load()
        for record in batch:
            state.advance_cursor(
                record.thread_id, record.message_id, ordinal=record.ordinal
            )
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
        last_global = (
            datetime.fromisoformat(state.last_global_at)
            if state.last_global_at else None
        )
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
