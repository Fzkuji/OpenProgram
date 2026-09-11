"""Bounded data and local draft IO for report Workflows; no network operations."""
from __future__ import annotations

import json
from pathlib import Path
import re
import uuid

from openprogram.programs.tools.files.write import execute as write_file
from openprogram.worktree.path_resolve import resolve_path


def decode_object(value: str) -> dict:
    """Read a bounded JSON object, rejecting ambiguous duplicate keys."""
    if not isinstance(value, str) or len(value.encode('utf-8')) > 300_000:
        raise ValueError('Report input exceeds 300000 bytes')

    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError('Duplicate key: ' + key)
            result[key] = item
        return result

    result = json.loads(value, object_pairs_hook=unique)
    if not isinstance(result, dict):
        raise ValueError('Report input must be a JSON object')
    return result


def encode(value) -> str:
    """Serialize Workflow state and model context without evaluating input."""
    return json.dumps(value, ensure_ascii=False, indent=2)


def save_report(week: str, output_dir: str, summary: str, reminder: str,
                coverage: dict, sources: list) -> Path:
    """Save a unique draft directory through the existing checked write tool."""
    if not re.fullmatch(r'\d{4}-W(?:0[1-9]|[1-4]\d|5[0-3])', week):
        raise ValueError('Expected week YYYY-Www')
    resolved, _ = resolve_path(output_dir or 'reports/group-weekly')
    target = Path(resolved) / week / uuid.uuid4().hex
    # Do not mkdir before the public file tool has checked write permission.
    payloads = {'summary.md': summary, 'sources.json': encode(sources)}
    if reminder:
        payloads['reminder.md'] = reminder
    payloads['coverage.json'] = encode(coverage)
    for name, content in payloads.items():
        outcome = write_file(str(target / name), content)
        if not isinstance(outcome, str) or not outcome.startswith('Wrote '):
            raise OSError(str(outcome))
    return target
