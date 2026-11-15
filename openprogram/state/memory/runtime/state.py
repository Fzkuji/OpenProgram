"""Persistent deterministic maintenance counters and the chat turns a write reads."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timedelta
from pathlib import Path

from openprogram.host.security.identity_header import normalize_identity_header_part

from ..workspace_layout import runtime_dir
from openprogram.host._platform.filesystem import replace_file


@dataclass(frozen=True)
class ChatTurn:
    """One message of a conversation, as a write reads it from the chat."""

    session_id: str
    message_id: str
    ordinal: int
    role: str
    content: str
    timestamp: str | None = None
    speaker_id: str | None = None
    speaker_display: str | None = None
    from_owner: bool = True

    @property
    def ref(self) -> str:
        """The chat reference a Topic cites this message by."""
        from ..chat_refs import ref

        return ref(self.session_id, self.message_id)

    @property
    def speaker_label(self) -> str:
        display = normalize_identity_header_part(
            "" if self.speaker_display is None else str(self.speaker_display)
        )
        speaker_id = normalize_identity_header_part(
            "" if self.speaker_id is None else str(self.speaker_id)
        )
        if display and speaker_id and display != speaker_id:
            return f"{display} ({speaker_id})"
        return display or speaker_id or self.role


@dataclass
class RuntimeState:
    creation_order: dict[str, int] = field(default_factory=dict)
    write_commits_since_global: int = 0
    local_batches: int = 0
    local_tokens: int = 0
    last_global_at: str | None = None


class RuntimeStateStore:
    def __init__(self, memory_dir: Path):
        self.memory_dir = Path(memory_dir)
        self.path = runtime_dir(self.memory_dir) / "runtime.json"

    def load(self) -> RuntimeState:
        if not self.path.exists():
            return RuntimeState()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return RuntimeState()
            known = {item.name for item in fields(RuntimeState)}
            return RuntimeState(**{
                key: value for key, value in payload.items() if key in known
            })
        except (OSError, TypeError, ValueError):
            return RuntimeState()

    def save(self, state: RuntimeState) -> None:
        """Replace the state file, through a temporary name of this write's own.

        One shared ``runtime.json.tmp`` makes two writers overwrite each
        other's half-written bytes and rename the survivor into place, and
        the loser's rename fails on a file that is no longer there. The
        write lock keeps that from happening today, which is a property of
        the caller rather than of this function. A private temporary name
        is the property this function can hold on its own.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(
            prefix=f"{self.path.stem}-{os.getpid()}-",
            suffix=".json.tmp",
            dir=self.path.parent,
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as file:
                file.write(
                    json.dumps(asdict(state), ensure_ascii=False, indent=2) + "\n"
                )
            # mkstemp opens at 0600 on POSIX; the published state file is read
            # like the rest of the workspace. Windows access is governed by
            # inherited ACLs, so POSIX mode bits are neither useful nor
            # intentionally emulated there.
            if os.name != "nt":
                os.chmod(temporary, 0o644)
            replace_file(temporary, self.path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    def git_commit(
        self, message: str, *, paths: tuple[str, ...] | None = None,
    ) -> str | None:
        """Commit memory's tracked files, or only ``paths`` among them."""
        try:
            subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=self.memory_dir,
                check=True,
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
            )
        except subprocess.CalledProcessError:
            return None
        candidates = [
            value for value in (
                "core.md", "MEMORY.md", "recent_events.jsonl",
                "commitments.jsonl",
                "relations.json", "sources", "topics", "timeline",
            )
            # A removed file is still staged so its deletion is committed
            # (the old root core.md, now part of MEMORY.md).
            if (paths is None or value in paths)
            and ((self.memory_dir / value).exists() or _tracked(self.memory_dir, value))
        ]
        if not candidates:
            return None
        subprocess.run(
            ["git", "add", "-A", "--", *candidates],
            cwd=self.memory_dir,
            check=True,
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
        )
        changed = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=self.memory_dir,
        ).returncode != 0
        if not changed:
            return None
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=self.memory_dir,
            check=True,
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
        )
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.memory_dir, text=True, encoding="utf-8", errors="replace"
        ).strip()


def _tracked(memory_dir: Path, path: str) -> bool:
    return subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", path],
        cwd=memory_dir, capture_output=True,
    ).returncode == 0


def should_incremental_write(
    unprocessed_tokens: int,
    last_message_at: datetime,
    now: datetime,
    *,
    token_threshold: int,
    idle_after: timedelta = timedelta(hours=1),
) -> bool:
    return unprocessed_tokens >= token_threshold or (
        unprocessed_tokens > 0 and now - last_message_at >= idle_after
    )


def should_local_reorganize(
    new_batches: int,
    new_tokens: int,
    *,
    batch_threshold: int,
    token_threshold: int,
) -> bool:
    return (
        batch_threshold > 0 and new_batches >= batch_threshold
    ) or (
        token_threshold > 0 and new_tokens >= token_threshold
    )


def should_global_manage(
    last_global_at: datetime | None,
    now: datetime,
    *,
    new_write_commits: int,
) -> bool:
    if new_write_commits <= 0:
        return False
    return last_global_at is None or now - last_global_at >= timedelta(days=1)
