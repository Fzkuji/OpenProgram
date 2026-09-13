"""Bounded metadata and resumable directory size queries; never read file content."""
from __future__ import annotations
import os
import secrets
import stat
import threading
import time
from collections import OrderedDict

from openprogram._compat import directory_close
from .query import _query_path
from .query import _open_query_dir
from .query import _open_child_dir
from .query import _project_info
from .query import _fs_query_failure

_LOCK = threading.RLock()
_SCANS: OrderedDict = OrderedDict()
_CACHE: OrderedDict = OrderedDict()
_MAX_SCANS = 16
_TTL = 60
_BATCH = 500


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
    """Yield leaf sizes without following links; cap open directory handles."""
    stack = []
    try:
        fd = _open_query_dir(project_id, path)
        stack.append((fd, os.scandir(fd), _stat(fd, "")))
        while stack:
            fd, iterator, initial = stack[-1]
            try:
                entry = next(iterator)
            except StopIteration:
                changed = _stat(fd, "").st_mtime_ns != initial.st_mtime_ns
                iterator.close(); directory_close(fd); stack.pop()
                if changed:
                    yield 0, 1
                continue
            try:
                value = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(value.st_mode):
                    if len(stack) >= 64:
                        yield 0, 1
                    else:
                        child = _open_child_dir(fd, entry.name)
                        try:
                            children = os.scandir(child)
                        except OSError:
                            directory_close(child)
                            raise
                        stack.append((child, children, _stat(child, "")))
                        yield 0, 0
                elif stat.S_ISREG(value.st_mode):
                    yield value.st_size, 0
                else:
                    yield 0, 1
            except OSError:
                yield 0, 1
    finally:
        for fd, iterator, _initial in reversed(stack):
            iterator.close(); directory_close(fd)


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


def cached_size(project_id, path):
    """Use only recent complete samples for a new size-sorted snapshot."""
    info = file_info(project_id, path)
    with _LOCK:
        cached = _CACHE.get((project_id, path))
        if cached and cached['revision'] == info.get('revision') and time.time() - cached['updated_at'] < _TTL:
            return cached['bytes']
    return None


def folder_size(project_id, path, operation='peek', token=None):
    if not isinstance(operation, str) or operation not in {'peek', 'start', 'continue', 'cancel'}:
        return _error(ValueError('unsupported size operation'))
    info = file_info(project_id, path)
    if info.get('error_code'):
        return info
    if info['type'] != 'dir':
        return _error(ValueError('size scan requires a directory'))
    key = (project_id, path)
    now = time.monotonic()
    with _LOCK:
        for old, scan in list(_SCANS.items()):
            if now - scan['used'] > _TTL:
                _drop(old)
        cached = _CACHE.get(key)
        if cached and cached['revision'][:2] != info['revision'][:2]:
            _CACHE.pop(key, None)
            cached = None
        if operation == 'peek':
            return {**cached, 'state': 'cached'} if cached else {'state': 'unknown', 'bytes': None}
        if operation == 'start':
            while len(_SCANS) >= _MAX_SCANS:
                _drop(next(iter(_SCANS)))
            token = secrets.token_urlsafe(24)
            _SCANS[token] = {'key': key, 'revision': info['revision'], 'iterator': _walk(project_id, path),
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
                  'token': None if done else token, 'updated_at': time.time(), 'revision': scan['revision']}
        if done:
            _drop(token)
            if not scan['skipped']:
                _CACHE[key] = result
                _CACHE.move_to_end(key)
                while len(_CACHE) > 256:
                    _CACHE.popitem(last=False)
        return result
