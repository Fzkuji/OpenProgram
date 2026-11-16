"""Where a Topic file sits: folders that follow meaning.

The rule (``docs/reference/design/memory/overview.html``, "Filing"):

- a real-world thing with more than one note is a folder named after it,
  holding ``overview.md`` and one file per aspect;
- things of one kind sit under a folder named for the kind, grouped again
  where the notes group them;
- no directory holds more than ``FLAT_RUN_LIMIT`` notes directly;
- a note never sits beside a folder of the same name.

Which note is about which thing, and what kind of thing that is, is a
judgment the memory agents make. Everything else is here and needs no
model: finding what breaks the rule, moving files, and telling the commit
what moved so links, the map and the ownership record follow.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any
from urllib.parse import quote

from openprogram.host._platform.filesystem import replace_file

from ..workspace_layout import FLAT_RUN_LIMIT, TEMPORARY_PREFIX

OVERVIEW = "overview.md"
_TOPICS = "topics/"


def _notes(directory: Path) -> list[Path]:
    """The Topic files directly in ``directory``."""
    try:
        return sorted(
            path for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() == ".md"
        )
    except OSError:
        return []


def _holds_notes(folder: Path) -> bool:
    """Whether ``folder`` is a directory with a Topic file somewhere in it."""
    return folder.is_dir() and any(
        path.is_file() for path in folder.rglob("*.md")
    )


def _directories(topics: Path) -> list[Path]:
    if not topics.is_dir():
        return []
    return [topics, *sorted(path for path in topics.rglob("*") if path.is_dir())]


def flat_runs(topics: Path) -> list[tuple[str, int]]:
    """Directories (workspace-relative) holding more than the limit of
    notes directly, with the count. ``overview.md`` and ``topics/core.md``
    describe the directory itself and do not count."""
    topics = Path(topics)
    found: list[tuple[str, int]] = []
    for directory in _directories(topics):
        count = sum(
            1 for path in _notes(directory)
            if path.name != OVERVIEW and path != topics / "core.md"
        )
        if count > FLAT_RUN_LIMIT:
            found.append((_relative(topics, directory), count))
    return found


def run_notes(topics: Path, directory: str) -> list[str]:
    """The notes (workspace-relative) directly in a flat-run directory."""
    topics = Path(topics)
    target = topics.parent / directory
    return [
        _relative(topics, path) for path in _notes(target)
        if path.name != OVERVIEW and path != topics / "core.md"
    ]


def shadowed_notes(topics: Path) -> dict[str, str]:
    """``x.md`` beside ``x/``, mapped to ``x/overview.md``: the moves that
    end it. A folder that already has an overview is left for an agent to
    merge, and is reported by ``problems``."""
    topics = Path(topics)
    moves: dict[str, str] = {}
    for directory in _directories(topics):
        for note in _notes(directory):
            folder = note.with_suffix("")
            if _holds_notes(folder) and not (folder / OVERVIEW).exists():
                moves[_relative(topics, note)] = _relative(topics, folder / OVERVIEW)
    return moves


def _relative(topics: Path, path: Path) -> str:
    return (Path("topics") / path.relative_to(topics)).as_posix()


def problems(topics: Path) -> list[str]:
    """What breaks the filing rule, one line each, as the workspace
    structure shows it to a memory agent."""
    topics = Path(topics)
    lines: list[str] = []
    for directory, count in flat_runs(topics):
        lines.append(
            f"{directory}/ holds {count} notes directly (limit "
            f"{FLAT_RUN_LIMIT}): give each thing its own folder, and put "
            "folders of one kind under a folder named for the kind"
        )
    for directory in _directories(topics):
        for note in _notes(directory):
            folder = note.with_suffix("")
            if not _holds_notes(folder):
                continue
            target = _relative(topics, folder / OVERVIEW)
            lines.append(
                f"{_relative(topics, note)} sits beside the folder "
                f"{_relative(topics, folder)}/: "
                + (
                    f"merge it into {target}"
                    if (folder / OVERVIEW).exists()
                    else f"move it to {target}"
                )
            )
    return lines


def advice(topics: Path, relative: str) -> str:
    """What to tell a writer that has just saved ``relative`` (a path
    under ``topics/``) where the rule says it should not sit. Empty when
    the file is filed well."""
    topics = Path(topics)
    path = topics / relative
    if not path.is_file() or path == topics / "core.md":
        return ""
    folder = path.with_suffix("")
    if _holds_notes(folder):
        return (
            f"topics/{relative} now sits beside the folder "
            f"{_relative(topics, folder)}/. A note and a folder of the same "
            f"name never coexist: move the note to "
            f"{_relative(topics, folder / OVERVIEW)} with the Move tool."
        )
    if path.name == OVERVIEW:
        return ""
    count = len([note for note in _notes(path.parent) if note.name != OVERVIEW])
    if count > FLAT_RUN_LIMIT:
        return (
            f"{_relative(topics, path.parent)}/ now holds {count} notes "
            f"directly (limit {FLAT_RUN_LIMIT}). File this note inside the "
            "folder of the thing it is about, under a folder named for "
            "that kind of thing, with the Move tool."
        )
    return ""


def _topic_path(raw: Any) -> str:
    """A move endpoint as a path under ``topics/``."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("a move needs a from and a to path")
    posix = Path(raw.strip()).as_posix()
    if not posix.startswith(_TOPICS):
        raise ValueError(f"{raw}: a move stays inside topics/")
    relative = Path(posix[len(_TOPICS):])
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError(f"{raw}: a move stays inside topics/")
    if relative.suffix.lower() != ".md":
        raise ValueError(f"{raw}: only Topic .md files are moved")
    return relative.as_posix()


def _pairs(moves: Any) -> list[tuple[str, str]]:
    if isinstance(moves, dict):
        items = list(moves.items())
    else:
        items = []
        for move in moves or ():
            if not isinstance(move, dict):
                raise ValueError("each move is an object with from and to")
            items.append((move.get("from"), move.get("to")))
    return [(_topic_path(source), _topic_path(target)) for source, target in items]


def stage_moves(topics: Path, moves: Any) -> list[tuple[str, str]]:
    """Move Topic files inside ``topics`` and remove the directories that
    leaves empty. Bytes are untouched. Returns the ``(from, to)`` pairs
    moved, workspace-relative.

    All or nothing: every source must exist, no target may be taken by a
    file that stays, and no two moves may share an endpoint. A target may
    be a path another move in the same call vacates.
    """
    topics = Path(topics)
    pairs = [(source, target) for source, target in _pairs(moves) if source != target]
    sources = [source for source, _target in pairs]
    targets = [target for _source, target in pairs]
    for label, names in (("from", sources), ("to", targets)):
        repeated = sorted({name for name in names if names.count(name) > 1})
        if repeated:
            raise ValueError(f"two moves share the {label} path topics/{repeated[0]}")
    for source, target in pairs:
        if source == "core.md":
            raise ValueError("topics/core.md stays where it is")
        if not (topics / source).is_file():
            raise ValueError(f"topics/{source} does not exist")
        taken = topics / target
        if os.path.lexists(taken) and target not in sources:
            raise ValueError(f"topics/{target} already exists")
        for parent in Path(target).parents:
            note = parent.as_posix() + ".md"
            if parent.parts and (topics / note).is_file() and note not in sources:
                raise ValueError(
                    f"topics/{note} is a note beside the folder "
                    f"topics/{parent.as_posix()}/: move it to "
                    f"topics/{parent.as_posix()}/{OVERVIEW} in the same call"
                )
    if not pairs:
        return []
    # Two steps, so one move may take the path another leaves.
    holding = topics.parent / ".filing-moves"
    shutil.rmtree(holding, ignore_errors=True)
    holding.mkdir()
    try:
        for index, (source, _target) in enumerate(pairs):
            replace_file(topics / source, holding / str(index))
        for index, (_source, target) in enumerate(pairs):
            destination = topics / target
            destination.parent.mkdir(parents=True, exist_ok=True)
            replace_file(holding / str(index), destination)
    finally:
        shutil.rmtree(holding, ignore_errors=True)
    for source, _target in pairs:
        for parent in (topics / source).parents:
            if parent == topics or topics not in parent.parents:
                break
            try:
                parent.rmdir()
            except OSError:
                break
    return [(_TOPICS + source, _TOPICS + target) for source, target in pairs]


def directory_renames(renames: dict[str, str], remaining: set[str]) -> dict[str, str]:
    """The directories that moved as a whole, old to new, given the file
    renames (old to new, any common root) and the files the tree holds
    now. A directory moved when none of its files stayed and every one of
    them kept its path below it."""
    moved: dict[str, str] = {}
    candidates = {
        parent.as_posix()
        for old in renames for parent in Path(old).parents
        if len(parent.parts) > 1
    }
    for directory in sorted(candidates):
        prefix = directory + "/"
        if any(path.startswith(prefix) for path in remaining):
            continue
        inside = {old: new for old, new in renames.items() if old.startswith(prefix)}
        roots = {
            new[: -len(old[len(prefix):])] if new.endswith("/" + old[len(prefix):]) else None
            for old, new in inside.items()
        }
        if len(roots) == 1 and None not in roots:
            moved[directory] = roots.pop().rstrip("/")
    return moved


def rewrite_paths(text: str, renames: dict[str, str]) -> str:
    """``text`` with every old workspace-relative path replaced by its new
    one, as written and as a URL-quoted link target. Directories are
    matched with their trailing slash."""
    for old, new in sorted(renames.items(), key=lambda item: -len(item[0])):
        for before, after in {(old, new), (quote(old, safe="/._-"), quote(new, safe="/._-"))}:
            if before.endswith(".md"):
                pattern = r"(?<![\w/.%-])" + re.escape(before) + r"(?![\w%-])"
            else:
                pattern = r"(?<![\w/.%-])" + re.escape(before) + r"(?=/)"
            text = re.sub(pattern, lambda _match, value=after: value, text)
    return text


def apply_moves(
    memory_dir: str | Path,
    moves: Any,
    *,
    message: str = "memory: regroup topics",
) -> list[tuple[str, str]]:
    """Move Topic files as one memory transaction and record it in history.

    The commit rewrites every relative link that crosses a move, rewrites
    the moved paths in ``MEMORY.md``, rebuilds the derived views and keeps
    each file's origin. No paragraph, block ID or footnote changes.
    """
    from .config import load_memory_config
    from .transaction import git_commit_state, workspace_write_lock
    from .workspace import MemoryWorkspace

    root = Path(memory_dir)
    with workspace_write_lock(root), closing(MemoryWorkspace(
        root, config=load_memory_config(), allowed_new_source_refs=set(),
    )) as space:
        baseline = space.baseline()
        moved = stage_moves(space.stage_dir / "topics", moves)
        if not moved:
            return []
        space.commit_edits(*baseline)
        git_commit_state(root, message)
    return moved


def regroup(
    memory_dir: str | Path,
    plan: Any = None,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """File a workspace's notes by the rule, as far as code alone can.

    Without ``plan``, every note beside a same-named folder becomes that
    folder's overview. With one (a ``{from: to}`` map or a list of
    ``{"from", "to"}``), those moves are applied too; a move whose source
    is gone or whose target is taken is skipped and reported, so a plan
    drawn up earlier still applies to a workspace that has moved on.
    """
    root = Path(memory_dir)
    topics = root / "topics"
    wanted = dict(_pairs(plan)) if plan else {}
    moves: dict[str, str] = {}
    skipped: list[dict[str, str]] = []
    for source, target in wanted.items():
        if source == target:
            continue
        if not (topics / source).is_file():
            skipped.append({"from": _TOPICS + source, "to": _TOPICS + target,
                            "reason": "source is not there"})
        elif os.path.lexists(topics / target) and target not in wanted:
            skipped.append({"from": _TOPICS + source, "to": _TOPICS + target,
                            "reason": "target already exists"})
        else:
            moves[source] = target
    # The mechanical pass looks at the tree as the plan leaves it, on a
    # copy, and the two are applied as one transaction.
    with tempfile.TemporaryDirectory(prefix=f"{TEMPORARY_PREFIX}filing-") as scratch:
        copy = Path(scratch) / "topics"
        if topics.is_dir():
            shutil.copytree(topics, copy)
        else:
            copy.mkdir()
        stage_moves(copy, {_TOPICS + s: _TOPICS + t for s, t in moves.items()})
        after = {
            source[len(_TOPICS):]: target[len(_TOPICS):]
            for source, target in shadowed_notes(copy).items()
        }
    moves = {source: after.pop(target, target) for source, target in moves.items()}
    moves.update(after)
    pairs = [{"from": _TOPICS + s, "to": _TOPICS + t} for s, t in sorted(moves.items())]
    report: dict[str, Any] = {
        "moved": pairs, "skipped": skipped, "problems_before": problems(topics),
    }
    if dry_run or not pairs:
        report["applied"] = False
        report["problems_after"] = report["problems_before"]
        return report
    apply_moves(root, pairs)
    report["applied"] = True
    report["problems_after"] = problems(topics)
    return report


def load_plan(path: str | Path) -> Any:
    """A plan file: JSON, either ``{from: to}`` or ``[{"from", "to"}]``,
    or ``{"moves": …}`` around one of those."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "moves" in payload:
        payload = payload["moves"]
    return payload


__all__ = [
    "FLAT_RUN_LIMIT", "OVERVIEW", "advice", "apply_moves", "directory_renames",
    "flat_runs", "load_plan", "problems", "regroup", "rewrite_paths",
    "run_notes", "shadowed_notes", "stage_moves",
]
