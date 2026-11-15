"""Approved local-file payloads for an exact, freshly observed file input."""
from __future__ import annotations

import mimetypes
from pathlib import Path

MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def file_payload(path):
    from openprogram.host.sandbox import validate_read_path
    if not isinstance(path, str) or not path.strip():
        return None, {'ok': False, 'reason_code': 'file_path_required',
                      'message': 'upload requires path to the user-approved local file.'}
    target = Path(path).expanduser().resolve()
    violation = validate_read_path(target)
    if violation:
        return None, {'ok': False, 'reason_code': 'file_read_denied', 'message': str(violation)}
    if not target.is_file():
        return None, {'ok': False, 'reason_code': 'file_not_found', 'message': 'The selected local file is unavailable.'}
    if target.stat().st_size > MAX_UPLOAD_BYTES:
        return None, {'ok': False, 'reason_code': 'file_too_large', 'message': 'File selection supports files up to 50 MiB.'}
    with target.open('rb') as stream:
        data = stream.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        return None, {'ok': False, 'reason_code': 'file_too_large', 'message': 'File selection supports files up to 50 MiB.'}
    return {'name': target.name, 'mimeType': mimetypes.guess_type(target.name)[0] or 'application/octet-stream',
            'buffer': data}, None
