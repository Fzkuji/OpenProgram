"""Which external transcripts were imported, into which session, how far.

``<state>/imports/external_sessions.json`` maps ``"<agent>:<source id>"``
to the session it became, the number of source turns written, the
fingerprint and content digest of the last one, and the time of the
source's last message. That is what lets a second import skip an
unchanged source, append the new turns of a grown one (finishing a turn
that was still running), and start a fresh session when the source was
rewound or the imported session moved on.
"""
from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any, Iterator, Optional

from openprogram.state.store.session.atomic_file import atomic_write_text
from openprogram.state.store.session.session_lock import registry_file_lock

_FILE = "external_sessions.json"


def default_root() -> Path:
    from openprogram.state.paths import get_state_dir
    return get_state_dir() / "imports"


def key(agent: str, source_id: str) -> str:
    return f"{agent}:{source_id}"


class Ledger:
    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root else default_root()
        self.path = self.root / _FILE
        self.entries: dict[str, dict[str, Any]] = {}

    def load(self) -> "Ledger":
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        entries = data.get("sources") if isinstance(data, dict) else None
        self.entries = entries if isinstance(entries, dict) else {}
        return self

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.path, json.dumps(
            {"version": 1, "sources": self.entries}, ensure_ascii=False, indent=1))

    def get(self, agent: str, source_id: str) -> Optional[dict[str, Any]]:
        return self.entries.get(key(agent, source_id))

    def put(self, agent: str, source_id: str, entry: dict[str, Any]) -> None:
        self.entries[key(agent, source_id)] = entry


_SCANS = "transcript_scans.json"


def load_scans(root: Optional[Path] = None) -> dict[str, dict[str, Any]]:
    """Where each transcript's summary scan stopped, keyed by path.

    A cache only: losing it costs one full rescan, so it is read and
    written without the ledger lock.
    """
    path = Path(root or default_root()) / _SCANS
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_scans(scans: dict[str, dict[str, Any]], root: Optional[Path] = None) -> None:
    root = Path(root or default_root())
    try:
        root.mkdir(parents=True, exist_ok=True)
        atomic_write_text(root / _SCANS, json.dumps(scans, ensure_ascii=False))
    except OSError:
        pass


@contextlib.contextmanager
def locked(root: Optional[Path] = None) -> Iterator[Ledger]:
    """The ledger, loaded under its lock and saved on a clean exit."""
    ledger = Ledger(root)
    ledger.root.mkdir(parents=True, exist_ok=True)
    with registry_file_lock(ledger.root, "external-imports"):
        ledger.load()
        yield ledger
        ledger.save()
