"""Public writing and maintenance operations."""

from pathlib import Path
from typing import Any

from .config import MemoryConfig, load_memory_config
from .agent import _run_agent, render_conversation
from ..prompts import ORGANIZE_MEMORY, WRITE_MEMORY


def render_writer_task(
    sessions: list[dict[str, Any]],
    *,
    memory_dir: str | Path | None = None,
) -> str:
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

    Files written outside OpenProgram are left off the list: the organizer
    reads them but never rewrites them (see ``ownership``).
    """
    from . import ownership

    config = config or load_memory_config()
    root = Path(memory_dir) / "topics"
    outside = {
        f"topics/{relative}" for relative in ownership.outside_paths(Path(memory_dir))
    }
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
    paths = [
        path for path in paths
        if not ownership.contains(outside, path)
    ]
    if not paths:
        return []
    return _run_agent(
        memory_dir,
        agent=agent,
        task=ORGANIZE_MEMORY.format(topic_paths="\n".join(paths)),
        usage_logger=usage_logger,
        config=config,
        allowed_new_source_refs=set(),
    )
