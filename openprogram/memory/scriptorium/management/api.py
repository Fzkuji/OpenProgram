"""Public writing and maintenance operations."""

import hashlib
import json
from pathlib import Path
from typing import Any

from .config import MemoryConfig
from .agent import _run_agent, render_conversation
from ..prompts import ORGANIZE_MEMORY, SYSTEM_PROMPT, WRITE_MEMORY
from .tools import TOOLS


def render_writer_task(sessions: list[dict[str, Any]]) -> str:
    """Render one writer batch.

    A batch is however much source text fits the input budget. Each part
    carries its own observation date, because relative expressions like
    "yesterday" resolve against the date of the text they appear in.
    """
    rendered = []
    for session in sessions:
        rendered.append(
            f"## Observed {session['observation_date']}\n\n"
            f"{render_conversation(session['turns'], session['refs'])}"
        )
    return WRITE_MEMORY.format(sessions="\n\n".join(rendered))


def render_writer_input(sessions: list[dict[str, Any]]) -> str:
    """Render all fixed and session-specific text sent to the Writer."""
    return f"{SYSTEM_PROMPT}\n\n{render_writer_task(sessions)}"


def writer_protocol_sha256() -> str:
    payload = json.dumps(
        {
            "system": SYSTEM_PROMPT,
            "write_memory": WRITE_MEMORY,
            "tools": TOOLS[:1],
            "runtime": "claude-agent-sdk",
            "contract": "topic-core-v3-runtime-ids",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_sessions(
    memory_dir: str | Path,
    *,
    agent: Any,
    sessions: list[dict[str, Any]],
    usage_logger: Any | None = None,
    config: MemoryConfig | None = None,
) -> list[dict[str, Any]]:
    task = render_writer_task(sessions)
    return _run_agent(
        memory_dir,
        agent=agent,
        task=task,
        source_sessions=sessions,
        usage_logger=usage_logger,
        config=config,
    )


def organize_topics(
    memory_dir: str | Path,
    *,
    agent: Any,
    touched: set[str] | None = None,
    usage_logger: Any | None = None,
    config: MemoryConfig | None = None,
) -> list[dict[str, Any]]:
    """Reorganize Topic files. ``touched`` limits the pass to those files.

    Passing None organizes every Topic file, which is the end-of-build pass.
    """
    config = config or MemoryConfig()
    root = Path(memory_dir) / "topics"
    if touched is None:
        paths = sorted(
            path.relative_to(memory_dir).as_posix()
            for path in root.rglob("*.md")
        )
    else:
        paths = sorted({
            Path(path).as_posix()
            for path in touched
            if Path(path).as_posix().startswith("topics/")
        })
    if not paths:
        return []
    return _run_agent(
        memory_dir,
        agent=agent,
        task=ORGANIZE_MEMORY.format(topic_paths="\n".join(paths)),
        usage_logger=usage_logger,
        config=config,
    )


