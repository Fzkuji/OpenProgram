"""Owner application registrations, separate from callable Program sources."""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import uuid

from openprogram.paths import get_state_dir
from openprogram.programs import _programs

_lock = threading.Lock()


def path() -> Path:
    return get_state_dir() / 'applications' / 'catalog.json'


def _read() -> list[dict]:
    target = path()
    if not target.exists():
        return []
    document = json.loads(target.read_text())
    if not isinstance(document, dict) or document.get('version') != 1:
        raise ValueError('invalid application catalog version')
    rows = document.get('applications')
    if not isinstance(rows, list):
        raise ValueError('invalid application catalog')
    identities = set()
    for row in rows:
        definition = row.get('application') if isinstance(row, dict) else None
        app_id = definition.get('id') if isinstance(definition, dict) else None
        if not isinstance(app_id, str) or not app_id or app_id in identities:
            raise ValueError('invalid or duplicate application registration')
        identities.add(app_id)
    return rows


def write(rows: list[dict]) -> None:
    """Write while update holds the catalog lock; publish atomically."""
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f'.{target.name}.{uuid.uuid4().hex}.tmp')
    try:
        temporary.write_text(json.dumps({'version': 1, 'applications': rows}, indent=2))
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def update(mutate):
    """Lock order: application catalog, then legacy Program sources.

    Publish the new catalog before removing legacy rows. A retry after a
    partial publication accepts identical rows only, never overwrites state.
    """
    from openprogram.auth.credentials import _private_file_lock
    with _lock, _private_file_lock(path(), root=get_state_dir()):
        rows = _read()
        def migrate(legacy):
            applications = [row for row in legacy if row.get('kind') == 'application']
            if not applications:
                return
            merged = list(rows)
            for row in applications:
                definition = row.get('application')
                app_id = definition.get('id') if isinstance(definition, dict) else None
                if not isinstance(app_id, str) or not app_id:
                    raise ValueError('invalid legacy application registration; source retained')
                existing = next((r for r in merged if r['application']['id'] == app_id), None)
                if existing is not None and existing != row:
                    raise ValueError(f'application migration conflict for {app_id}; sources retained')
                if existing is None:
                    merged.append(row)
            write(merged)
            _programs._write_program_sources([row for row in legacy if row.get('kind') != 'application'])
            rows[:] = merged
        _programs._update_program_sources(migrate)
        return mutate(rows)


def read() -> list[dict]:
    return update(lambda rows: rows)
