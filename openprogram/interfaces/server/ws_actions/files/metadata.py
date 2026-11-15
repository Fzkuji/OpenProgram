"""Bounded metadata and resumable directory size queries; never read file content."""
from __future__ import annotations
import os
import json
import logging
import secrets
import stat
import threading
import time
from collections import OrderedDict

from openprogram.host._platform.filesystem import directory_close
from .query import _query_path
from .query import _open_query_dir
from .query import _open_child_dir
from .query import _project_info
from .query import _fs_query_failure

_LOCK = threading.RLock()
_SCANS: OrderedDict = OrderedDict()
_CACHE: OrderedDict = OrderedDict()
_CACHE_FILE = None
_MAX_CACHE = 256
_LOG = logging.getLogger(__name__)
_MAX_SCANS = 16
_TTL = 60
_BATCH = 500
# One automatic ``measure`` request walks for at most this long, then
# saves what it counted; Details can continue from the returned token.
_MEASURE_BUDGET = 1.0


def _target(project_id, path):
    if not isinstance(project_id, str) or not project_id:
        raise ValueError("project_id must be a nonempty string")
    canonical, error = _query_path(path)
    if error:
        raise ValueError(error)
    parent, _, name = (canonical or '').rpartition('/')
    fd = _open_query_dir(project_id, parent if name else '')
    return canonical or '', fd, name


def _stat(fd, name):
    if not name:
        return os.stat(fd) if isinstance(fd, str) else os.fstat(fd)
    return os.stat(os.path.join(fd, name), follow_symlinks=False) if isinstance(fd, str) else os.stat(name, dir_fd=fd, follow_symlinks=False)


def _error(exc):
    code, message = ('INVALID_REQUEST', str(exc)) if isinstance(exc, ValueError) else _fs_query_failure(exc)
    return {'error_code': code, 'error': message, 'status': 'error'}


def file_info(project_id, path):
    try:
        canonical, fd, name = _target(project_id, path)
        try:
            value = _stat(fd, name)
            link = {}
            if stat.S_ISLNK(value.st_mode):
                target = os.readlink(os.path.join(fd, name)) if isinstance(fd, str) else os.readlink(name, dir_fd=fd)
                root, _, _ = _project_info(project_id)
                absolute = os.path.abspath(os.path.join(root, os.path.dirname(canonical), target))
                link = {'link_status': 'Target outside project or unavailable'}
                if os.path.commonpath([root, absolute]) == root:
                    relative = os.path.relpath(absolute, root)
                    try:
                        _, target_fd, target_name = _target(project_id, '' if relative == '.' else relative)
                        try:
                            target_stat = _stat(target_fd, target_name)
                        finally:
                            directory_close(target_fd)
                        if not stat.S_ISLNK(target_stat.st_mode):
                            link = {'link_target': relative, 'link_status': 'Available'}
                    except FileNotFoundError:
                        link = {'link_status': 'Broken link'}
                    except (OSError, ValueError):
                        pass
        finally:
            directory_close(fd)
        root, _, _ = _project_info(project_id)
        kind = 'symlink' if stat.S_ISLNK(value.st_mode) else 'dir' if stat.S_ISDIR(value.st_mode) else 'file'
        return {**link, 'status': 'ready', 'type': kind, 'name': name or os.path.basename(root),
                'absolute_path': os.path.join(root, canonical), 'size': value.st_size if kind != 'dir' else None,
                'mtime': value.st_mtime, 'created_at': getattr(value, 'st_birthtime', None),
                'permissions': stat.filemode(value.st_mode), 'revision': [value.st_dev, value.st_ino, value.st_mtime_ns]}
    except (OSError, ValueError) as exc:
        return _error(exc)


def _walk(project_id, path):
    """Yield leaf sizes without following links; cap open directory handles.

    Each level keeps one handle: its names are read up front so the
    scandir handle (a dup of the directory's) closes right away, and a
    paused scan parked under a token holds one handle per depth.
    """
    stack = []
    try:
        fd = _open_query_dir(project_id, path)
        stack.append((fd, iter(_names(fd)), _stat(fd, "")))
        while stack:
            fd, names, initial = stack[-1]
            name = next(names, None)
            if name is None:
                changed = _stat(fd, "").st_mtime_ns != initial.st_mtime_ns
                directory_close(fd); stack.pop()
                if changed:
                    yield 0, 1
                continue
            try:
                value = _stat(fd, name)
                if stat.S_ISDIR(value.st_mode):
                    if len(stack) >= 64:
                        yield 0, 1
                    else:
                        child = _open_child_dir(fd, name)
                        try:
                            children = _names(child)
                            initial_child = _stat(child, "")
                        except OSError:
                            directory_close(child)
                            raise
                        stack.append((child, iter(children), initial_child))
                        yield 0, 0
                elif stat.S_ISREG(value.st_mode):
                    yield value.st_size, 0
                else:
                    yield 0, 1
            except OSError:
                yield 0, 1
    finally:
        for fd, _names_left, _initial in reversed(stack):
            directory_close(fd)


def _names(fd):
    with os.scandir(fd) as entries:
        return [entry.name for entry in entries]


def _drop(token):
    scan = _SCANS.pop(token, None)
    if scan:
        scan['timer'].cancel()
        scan['iterator'].close()
    return scan


def _expire(token):
    with _LOCK:
        scan = _SCANS.get(token)
        if scan is None:
            return
        remaining = _TTL - (time.monotonic() - scan['used'])
        if remaining <= 0:
            _drop(token)
        else:
            scan['timer'] = threading.Timer(remaining, _expire, (token,))
            scan['timer'].daemon = True
            scan['timer'].start()


def _load_cache():
    """Load bounded terminal samples once per profile, under _LOCK."""
    global _CACHE_FILE
    from openprogram.state.paths import get_state_dir
    cache_file = get_state_dir() / 'folder-sizes.json'
    if cache_file == _CACHE_FILE:
        return
    _CACHE.clear()
    _CACHE_FILE = cache_file
    try:
        with cache_file.open(encoding='utf-8') as source:
            samples = json.loads(source.read(1024 * 1024))
        if not isinstance(samples, dict) or samples.get('version') != 1:
            return
        rows = samples.get('samples')
        if not isinstance(rows, list):
            return
        for row in rows[-_MAX_CACHE:]:
            if not isinstance(row, list) or len(row) != 2:
                continue
            key, value = row
            if (not isinstance(key, str) or not isinstance(value, dict)
                    or value.get('state') not in {'complete', 'incomplete', 'partial'}
                    or any(type(value.get(field)) is not int or value[field] < 0
                           for field in ('bytes', 'entries', 'skipped'))
                    or not isinstance(value.get('updated_at'), (int, float))
                    or not isinstance(value.get('revision'), list)
                    or len(value['revision']) != 3
                    or any(type(part) is not int for part in value['revision'])):
                continue
            _CACHE[key] = {field: value[field] for field in
                           ('state', 'bytes', 'entries', 'skipped', 'updated_at', 'revision')}
            _CACHE[key]['complete'] = value['state'] == 'complete'
            _CACHE[key]['token'] = None
    except FileNotFoundError:
        return
    except (OSError, ValueError) as exc:
        _LOG.debug('Unable to load folder size cache: %s', exc)


def _save_cache():
    from openprogram.state.store.session.atomic_file import atomic_write_text
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(_CACHE_FILE, json.dumps({'version': 1, 'samples': list(_CACHE.items())}))
    except OSError as exc:
        # Metadata remains usable when profile storage is unavailable.
        _LOG.debug('Unable to save folder size cache: %s', exc)


def _store(key, result):
    """Keep one terminal or budget-limited sample per directory, under _LOCK."""
    _CACHE[key] = {**result, 'token': None}
    _CACHE.move_to_end(key)
    while len(_CACHE) > _MAX_CACHE:
        _CACHE.popitem(last=False)
    _save_cache()


def _cached_sample(info):
    _load_cache()
    key = info.get('absolute_path')
    cached = _CACHE.get(key)
    if cached and cached['revision'][:2] != info.get('revision', [])[:2]:
        _CACHE.pop(key, None)
        cached = None
    return cached


def invalidate_cached_sizes(project_id, paths):
    """Discard affected ancestor/subtree samples after a Files mutation."""
    root, _, error = _project_info(project_id)
    if error:
        return
    targets = []
    for path in paths:
        canonical, error = _query_path(path)
        if error:
            continue
        targets.append(os.path.normpath(os.path.join(root, canonical)))
    def affected(candidate):
        candidate = os.path.normpath(candidate)
        return any(os.path.commonpath([candidate, target]) in {candidate, target}
                   for target in targets)
    with _LOCK:
        _load_cache()
        changed = False
        for candidate in list(_CACHE):
            try:
                match = affected(candidate)
            except ValueError:
                match = False  # Different Windows drives cannot share ancestors.
            if match:
                _CACHE.pop(candidate)
                changed = True
        for token, scan in list(_SCANS.items()):
            try:
                match = affected(scan['absolute_path'])
            except ValueError:
                match = False
            if match:
                _drop(token)
        if changed:
            _save_cache()


def cached_size(project_id, path):
    """Use only recent complete samples for a new size-sorted snapshot."""
    info = file_info(project_id, path)
    with _LOCK:
        cached = _cached_sample(info)
        if cached and cached['complete'] and cached['revision'] == info.get('revision') and time.time() - cached['updated_at'] < _TTL:
            return cached['bytes']
    return None


def _measure(project_id, path, fresh):
    """Answer a visible folder in one request: the saved sample, else one
    bounded walk whose result (finished or not) is saved for the next view."""
    if not fresh:
        result = folder_size(project_id, path, 'peek')
        if result.get('error_code') or result.get('state') != 'unknown':
            return result
    result = folder_size(project_id, path, 'start')
    deadline = time.monotonic() + _MEASURE_BUDGET
    while result.get('token') and time.monotonic() < deadline:
        result = folder_size(project_id, path, 'continue', result['token'])
    if result.get('state') == 'partial':
        info = file_info(project_id, path)
        if not info.get('error_code') and info.get('revision') == result.get('revision'):
            with _LOCK:
                _load_cache()
                # A concurrent request may have saved a newer sample meanwhile.
                saved = _CACHE.get(info['absolute_path'])
                if not saved or saved['updated_at'] <= result['updated_at']:
                    _store(info['absolute_path'], result)
    return result


def folder_size(project_id, path, operation='peek', token=None, fresh=False):
    if not isinstance(operation, str) or operation not in {'peek', 'measure', 'start', 'continue', 'cancel'}:
        return _error(ValueError('unsupported size operation'))
    if operation == 'measure':
        return _measure(project_id, path, fresh is True)
    info = file_info(project_id, path)
    if info.get('error_code'):
        return info
    if info['type'] != 'dir':
        return _error(ValueError('size scan requires a directory'))
    canonical, _ = _query_path(path)
    key = (project_id, canonical)
    now = time.monotonic()
    with _LOCK:
        for old, scan in list(_SCANS.items()):
            if now - scan['used'] > _TTL:
                _drop(old)
        cached = _cached_sample(info)
        if operation == 'peek':
            return {**cached, 'state': 'cached'} if cached else {'state': 'unknown', 'bytes': None}
        if operation == 'start':
            while len(_SCANS) >= _MAX_SCANS:
                _drop(next(iter(_SCANS)))
            token = secrets.token_urlsafe(24)
            _SCANS[token] = {'key': key, 'revision': info['revision'], 'absolute_path': info['absolute_path'],
                             'iterator': _walk(project_id, canonical),
                             'bytes': 0, 'entries': 0, 'skipped': 0, 'used': now,
                             'timer': threading.Timer(_TTL, _expire, (token,))}
            _SCANS[token]['timer'].daemon = True
            _SCANS[token]['timer'].start()
        scan = _SCANS.get(token) if isinstance(token, str) else None
        if not scan or scan['key'] != key:
            return _error(ValueError('size scan expired; restart it'))
        if scan['revision'] != info['revision']:
            _drop(token)
            return _error(ValueError('directory changed; restart size scan'))
        if operation == 'cancel':
            _drop(token)
            return {'state': 'cancelled', 'bytes': scan['bytes'], 'entries': scan['entries'], 'skipped': scan['skipped']}
        scan['used'] = now
        done = False
        try:
            for _ in range(_BATCH):
                size, skipped = next(scan['iterator'])
                scan['bytes'] += size; scan['entries'] += 1; scan['skipped'] += skipped
                if time.monotonic() - now > .05:
                    break
        except StopIteration:
            done = True
        except (OSError, ValueError) as exc:
            _drop(token)
            return _error(exc)
        result = {'state': ('incomplete' if scan['skipped'] else 'complete') if done else 'partial',
                  'bytes': scan['bytes'], 'entries': scan['entries'], 'skipped': scan['skipped'],
                  'complete': done and not scan['skipped'],
                  'token': None if done else token, 'updated_at': time.time(), 'revision': scan['revision']}
        if done:
            _drop(token)
            _store(info['absolute_path'], result)
        return result
