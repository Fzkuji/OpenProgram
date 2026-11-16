"""Collect the profile's shared ``tool_results/`` folder.

A bounded tool result names the file that holds its whole text, and that
name is part of the bytes every later request sends again, so the file is
a record of the conversation, not a cache. A result made inside a
conversation keeps its file in the conversation's own folder and goes when
the conversation goes. One made outside any conversation (an
``openprogram --print`` run without a session, an older result) keeps it
in the profile's shared ``tool_results/``; this module removes those that
no conversation names any more.

A file is removed only when it is older than ``GRACE_SECONDS`` (a run that
just wrote it may not have recorded its node yet) and no node of any
conversation, on any branch, archived or not, names it. The names are read
from every conversation's record files; when any of them cannot be read
the set is unknown and nothing is removed (Hermes collects its persisted
results the same way: by reference, failing closed).
"""
from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path
from typing import Iterable

_log = logging.getLogger(__name__)

#: A file younger than this is kept whatever names it.
GRACE_SECONDS = 24 * 3600
#: How often the Worker collects.
INTERVAL_SECONDS = 24 * 3600
#: The folder a bounded tool result's whole text is kept in.
TOOL_RESULTS_DIRNAME = "tool_results"

# ``tool_results`` then the file name, with either separator, however many
# times a JSON record escaped a backslash.
_REFERENCE = re.compile(r"tool_results[/\\]+([A-Za-z0-9._-]+)")


def referenced_names(session_dirs: Iterable[Path]) -> set[str] | None:
    """File names under ``tool_results/`` that the record files of
    ``session_dirs`` mention, or None when one of them cannot be read."""
    names: set[str] = set()
    for directory in session_dirs:
        try:
            files = [path for path in Path(directory).iterdir()
                     if path.is_file() and path.suffix in {".jsonl", ".json"}]
        except FileNotFoundError:
            continue
        except OSError:
            _log.warning("tool output collection: cannot list %s", directory, exc_info=True)
            return None
        for path in files:
            try:
                text = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                continue
            except (OSError, UnicodeDecodeError):
                _log.warning("tool output collection: cannot read %s", path, exc_info=True)
                return None
            names.update(_REFERENCE.findall(text))
    return names


def session_dirs(store) -> list[Path] | None:
    """Every conversation folder ``store`` knows, archived ones too, and
    every folder under its root that looks like one; None when the
    conversations cannot be listed."""
    try:
        rows = store.list_sessions(include_archived=True, limit=10 ** 9)
        dirs = {Path(store._session_dir(row["id"])) for row in rows if row.get("id")}  # noqa: SLF001
        root = Path(store.root_path)
        dirs.update(path for path in root.glob("*/chats/*") if path.is_dir())
    except Exception:  # noqa: BLE001 - unknown means nothing is removed
        _log.warning("tool output collection: conversations not listed", exc_info=True)
        return None
    return sorted(dirs)


def collect(results_dir: Path, session_folders: Iterable[Path] | None, *,
            now: float | None = None) -> list[Path]:
    """Remove the files in ``results_dir`` older than ``GRACE_SECONDS`` that
    no folder of ``session_folders`` names; return what was removed. With
    ``session_folders`` None (unknown), or names that cannot all be read,
    nothing is removed."""
    if session_folders is None or not results_dir.is_dir():
        return []
    now = time.time() if now is None else now
    try:
        old = [path for path in results_dir.iterdir()
               if path.is_file() and now - path.stat().st_mtime > GRACE_SECONDS]
    except OSError:
        return []
    if not old:
        return []
    named = referenced_names(session_folders)
    if named is None:
        return []
    removed: list[Path] = []
    for path in old:
        if path.name in named:
            continue
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError:
            # A file another process holds open (Windows) is tried again
            # on the next pass.
            _log.info("tool output collection: %s kept for now", path, exc_info=True)
            continue
        removed.append(path)
    return removed


def collect_profile() -> list[Path]:
    """Collect the profile's shared ``tool_results/`` against its conversations."""
    from openprogram.state.paths import get_state_dir
    from openprogram.state.store.current import current_store

    store = current_store()
    return collect(get_state_dir() / TOOL_RESULTS_DIRNAME, session_dirs(store))


def start_daily(*, first_delay: float = 600.0) -> threading.Thread:
    """Collect once a day in the background, the first time after
    ``first_delay`` seconds."""
    def run() -> None:
        time.sleep(first_delay)
        while True:
            try:
                removed = collect_profile()
                if removed:
                    _log.info("tool output collection removed %d files", len(removed))
            except Exception:  # noqa: BLE001 - the next pass tries again
                _log.warning("tool output collection failed", exc_info=True)
            time.sleep(INTERVAL_SECONDS)

    thread = threading.Thread(target=run, daemon=True, name="tool-output-collection")
    thread.start()
    return thread


__all__ = ["GRACE_SECONDS", "INTERVAL_SECONDS", "TOOL_RESULTS_DIRNAME", "collect", "collect_profile",
           "referenced_names", "session_dirs", "start_daily"]
