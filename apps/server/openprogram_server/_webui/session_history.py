"""Byte-budgeted transcript pages anchored to the existing branch snapshot."""
from __future__ import annotations

import json

PAGE_MESSAGES = 50
PAGE_BYTES = 512 * 1024


def wire_message(message: dict) -> dict:
    """Remove only exact duplicates already promoted out of legacy extra."""
    extra = message.get('extra')
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except (TypeError, ValueError):
            return message
    if not isinstance(extra, dict):
        return message
    remaining = {k: v for k, v in extra.items() if k not in message or message[k] != v}
    # blocks/tool_calls may have been truncated on the wire. Their originals
    # remain persisted and are read by the existing full-output endpoint.
    for key in ('blocks', 'tool_calls'):
        if key in message:
            remaining.pop(key, None)
    copied = dict(message)
    if remaining:
        copied['extra'] = remaining
    else:
        copied.pop('extra', None)
    return copied


def history_page(messages: list[dict], roots: set[str], before: str | None = None):
    """Keep caller descendants with their display root, preserving row order."""
    rows = [wire_message(row) for row in messages]
    by_id = {row.get('id'): row for row in rows}
    groups: dict[str, list[dict]] = {}
    for row in rows:
        key = str(row.get('id') or '')
        seen = set()
        while key not in roots and key in by_id and key not in seen:
            seen.add(key)
            parent = by_id[key].get('caller')
            if not parent or parent not in by_id:
                break
            key = parent
        groups.setdefault(key, []).append(row)
    keys = list(groups)
    if before is not None and before not in groups:
        raise ValueError('History cursor is no longer available. Reload the conversation.')
    stop = keys.index(before) if before is not None else len(keys)
    start = stop
    size = 0
    while start > 0 and stop - start < PAGE_MESSAGES:
        candidate = groups[keys[start - 1]]
        cost = len(json.dumps(candidate, ensure_ascii=False, default=str).encode('utf-8'))
        if start < stop and size + cost > PAGE_BYTES:
            break
        start -= 1
        size += cost
    selected = {id(row) for key in keys[start:stop] for row in groups[key]}
    return [row for row in rows if id(row) in selected], (keys[start] if start > 0 else None)


class HistorySnapshot:
    """Connection-owned, indexed transcript. Bodies stay on disk between pages."""

    def __init__(self, session_id: str, head_id: str | None, messages: list[dict], roots: set[str]):
        import sqlite3
        import tempfile
        import threading
        import uuid
        self.session_id = session_id
        self.head_id = head_id
        self.token = uuid.uuid4().hex
        self._lock = threading.RLock()
        self._directory = tempfile.TemporaryDirectory(prefix='openprogram-history-')
        self._db = sqlite3.connect(self._directory.name + '/history.sqlite', check_same_thread=False)
        self._db.execute('CREATE TABLE groups (position INTEGER PRIMARY KEY, id TEXT UNIQUE, body BLOB, size INTEGER)')
        by_id = {row.get('id'): row for row in messages}
        groups: dict[str, list[dict]] = {}
        owners: dict[str, str] = {}
        for row in messages:
            key = str(row.get('id') or '')
            path: list[str] = []
            seen: set[str] = set()
            while key not in roots and key in by_id and key not in seen:
                if key in owners:
                    key = owners[key]
                    break
                seen.add(key)
                path.append(key)
                parent = by_id[key].get('caller')
                if not parent or parent not in by_id:
                    break
                key = parent
            for child in path:
                owners[child] = key
            groups.setdefault(key, []).append(wire_message(row))
        try:
            with self._db:
                for position, (key, rows) in enumerate(groups.items()):
                    body = json.dumps(rows, ensure_ascii=False, default=str).encode('utf-8')
                    self._db.execute('INSERT INTO groups VALUES (?, ?, ?, ?)', (position, key, body, len(body)))
            self.total = len(groups)
        except BaseException:
            self.close()
            raise

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def close(self):
        with self._lock:
            self._db.close()
            self._directory.cleanup()

    def page(self, *, before=None, after=None, around=None):
        """Read at most one page, using indexed message IDs in either direction."""
        with self._lock:
            cursor = before if before is not None else after if after is not None else around
            position = None
            if cursor is not None:
                found = self._db.execute('SELECT position FROM groups WHERE id=?', (cursor,)).fetchone()
                if not found:
                    raise ValueError('History cursor is unavailable')
                position = found[0]
            if after is not None:
                candidates = self._db.execute('SELECT position,id,size FROM groups WHERE position>? ORDER BY position LIMIT ?', (position, PAGE_MESSAGES)).fetchall()
            elif around is not None:
                start = max(0, position - PAGE_MESSAGES // 2)
                candidates = self._db.execute('SELECT position,id,size FROM groups WHERE position>=? ORDER BY position LIMIT ?', (start, PAGE_MESSAGES)).fetchall()
                # Anchor must fit even when adjacent turns exceed the byte budget.
                while candidates and candidates[0][0] < position and sum(r[2] for r in candidates if r[0] <= position) > PAGE_BYTES:
                    candidates.pop(0)
            else:
                stop = self.total if position is None else position
                candidates = self._db.execute('SELECT position,id,size FROM groups WHERE position<? ORDER BY position DESC LIMIT ?', (stop, PAGE_MESSAGES)).fetchall()
            chosen = []
            size = 0
            for row in candidates:
                if chosen and size + row[2] > PAGE_BYTES:
                    break
                chosen.append(row)
                size += row[2]
            chosen.sort()
            start = chosen[0][0] if chosen else (position + 1 if after is not None else 0)
            end = chosen[-1][0] + 1 if chosen else start
            messages = []
            for pos, _, _ in chosen:
                body = self._db.execute('SELECT body FROM groups WHERE position=?', (pos,)).fetchone()[0]
                messages.extend(json.loads(body))
            return messages, {
                'head_id': self.head_id, 'snapshot': self.token, 'start': start, 'end': end, 'total': self.total,
                'before': chosen[0][1] if chosen and start > 0 else None,
                'after': chosen[-1][1] if chosen and end < self.total else None,
            }


def install_snapshot(ws, snapshot):
    """Keep at most two snapshots per connection; replacement releases disk files."""
    from collections import OrderedDict
    snapshots = getattr(ws, '_history_snapshots', None)
    if snapshots is None:
        snapshots = OrderedDict()
        ws._history_snapshots = snapshots
    old = snapshots.pop(snapshot.session_id, None)
    if old is not None:
        old.close()
    snapshots[snapshot.session_id] = snapshot
    while len(snapshots) > 2:
        snapshots.popitem(last=False)[1].close()


def close_snapshots(ws):
    snapshots = getattr(ws, '_history_snapshots', {})
    for snapshot in list(snapshots.values()):
        snapshot.close()
    snapshots.clear()
