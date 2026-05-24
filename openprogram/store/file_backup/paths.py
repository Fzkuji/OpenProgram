"""Path hashing + directory layout for the file-backup store.

The backup store mirrors Claude Code's fileHistory.ts in spirit: keep
original copies of files BEFORE an agent's first edit in a given turn,
keyed by ``(turn_id, original_path)``. Restoring a turn means walking
its directory and copying each backup back to its original location.

Layout::

    ~/.agentic/sessions-git/<session_id>/file_backups/
    └── <turn_id>/
        ├── manifest.json     # { backup_basename → original_abs_path }
        └── <hash>            # backup of one original file (no extension)

``<hash>`` is a short content-addressed-ish basename derived from the
original path (we want backups to be readable when humans poke around,
not collision-free across paths). The manifest is the source of truth
for "which backup belongs to which path".
"""
from __future__ import annotations

import hashlib
from pathlib import Path


def session_backup_root(session_dir: Path) -> Path:
    """Where this session's backups live. Lazily created on first write."""
    return session_dir / "file_backups"


def turn_backup_dir(session_dir: Path, turn_id: str) -> Path:
    """Per-turn directory under the session backup root."""
    return session_backup_root(session_dir) / turn_id


def turn_manifest_path(session_dir: Path, turn_id: str) -> Path:
    return turn_backup_dir(session_dir, turn_id) / "manifest.json"


def path_basename(original_path: str) -> str:
    """Stable basename for a backup file derived from its original path.

    Uses the last 12 chars of an sha1 plus the original filename tail
    for human readability — so when someone runs ``ls`` they can guess
    what each backup was for without needing the manifest.
    """
    h = hashlib.sha1(original_path.encode("utf-8")).hexdigest()[:12]
    tail = Path(original_path).name.replace("/", "_")[:40] or "file"
    return f"{h}_{tail}"
