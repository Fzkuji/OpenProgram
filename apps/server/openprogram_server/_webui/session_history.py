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
