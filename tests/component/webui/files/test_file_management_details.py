"""Public file management query regression tests."""
from .test_project_files import _run
from . import test_project_files as _test_project_files
from openprogram.interfaces.server.ws_actions import files as ws_files
import os
import time
import pytest

# Shared pytest fixtures, bound at module level so pytest collects them here.
project_root = _test_project_files.project_root


def _measured(cmd):
    """A whole folder-size sample: a busy machine can outlast one measuring
    budget, and the sample then goes on from its token."""
    sample = _run(ws_files.handle_project_folder_size, {**cmd, 'operation': 'start'})['data']
    while sample['state'] == 'partial':
        sample = _run(ws_files.handle_project_folder_size, {**cmd, 'operation': 'continue', 'token': sample['token']})['data']
    return sample


def test_sort_before_pagination(project_root):
    for n in range(12):
        (project_root / f'file{n}.txt').write_bytes(b'x' * (n + 1))
    cmd = {'project_id': 'p1', 'path': '', 'page_size': 3,
           'sort': 'name:asc:folders:hidden:ignored'}
    page = _run(ws_files.handle_project_file_tree, cmd)['data']
    assert not page.get('error'), page
    names = [e['name'] for e in page['entries']]
    while page['next_cursor']:
        page = _run(ws_files.handle_project_file_tree, {**cmd, 'cursor': page['next_cursor']})['data']
        names.extend(e['name'] for e in page['entries'])
    assert names.index('file2.txt') < names.index('file10.txt')
    assert len(names) == len(set(names))


def test_metadata_and_folder_size(project_root):
    cmd = {'project_id': 'p1', 'path': 'src'}
    info = _run(ws_files.handle_project_file_info, cmd)['data']
    assert info['type'] == 'dir'
    size = _run(ws_files.handle_project_folder_size, {**cmd, 'operation': 'start'})['data']
    while size['state'] == 'partial':
        size = _run(ws_files.handle_project_folder_size, {**cmd, 'operation': 'continue', 'token': size['token']})['data']
    assert size['state'] == 'complete'
    assert size['bytes'] == len(b"print('hi')\n")
    assert _run(ws_files.handle_project_file_info, {**cmd, 'path': '../outside'})['data']['error_code']


def test_size_cancel_and_resume_are_bound_to_directory(project_root, monkeypatch):
    from openprogram.interfaces.server.ws_actions.files import metadata as metadata
    monkeypatch.setattr(metadata, '_BATCH', 1)
    for n in range(3):
        (project_root / 'src' / f'{n}.txt').write_bytes(b'123')
    cmd = {'project_id': 'p1', 'path': 'src'}
    page = _run(ws_files.handle_project_folder_size, {**cmd, 'operation': 'start'})['data']
    assert page['state'] == 'partial'
    try:
        bad = _run(ws_files.handle_project_folder_size, {**cmd, 'path': '', 'operation': 'continue', 'token': page['token']})['data']
        assert bad['error_code'] == 'INVALID_REQUEST'
        cancelled = _run(ws_files.handle_project_folder_size, {**cmd, 'operation': 'cancel', 'token': page['token']})['data']
        assert cancelled['state'] == 'cancelled'
        assert _run(ws_files.handle_project_folder_size, {**cmd, 'operation': 'continue', 'token': page['token']})['data']['error_code']
    finally:
        with metadata._LOCK:
            for token in list(metadata._SCANS):
                metadata._drop(token)


def _handles_under(root):
    """Open descriptors that point inside ``root``.

    Counting every descriptor of the process also counts files other
    threads open meanwhile, which made the bound flaky under load.
    """
    import fcntl
    import sys
    prefix = os.path.realpath(root) + os.sep
    count = 0
    for name in os.listdir('/dev/fd'):
        try:
            fd = int(name)
            if sys.platform == 'darwin':
                path = fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(1024)).rstrip(b'\0').decode()
            else:
                path = os.readlink(f'/proc/self/fd/{fd}')
        except (OSError, ValueError):
            continue
        if (path + os.sep).startswith(prefix):
            count += 1
    return count


@pytest.mark.skipif(not os.path.isdir('/dev/fd'), reason='needs /dev/fd to count open handles')
def test_paused_size_scan_holds_one_handle_per_level(project_root, monkeypatch):
    from openprogram.interfaces.server.ws_actions.files import metadata as metadata
    monkeypatch.setattr(metadata, '_BATCH', 1)
    deep = project_root / 'src'
    for level in range(6):
        deep = deep / f'd{level}'
        deep.mkdir()
    (deep / 'leaf.txt').write_bytes(b'x')
    cmd = {'project_id': 'p1', 'path': 'src'}
    held = 0
    page = _run(ws_files.handle_project_folder_size, {**cmd, 'operation': 'start'})['data']
    try:
        while page.get('token'):
            held = max(held, _handles_under(project_root))
            page = _run(ws_files.handle_project_folder_size, {**cmd, 'operation': 'continue', 'token': page['token']})['data']
    finally:
        with metadata._LOCK:
            for token in list(metadata._SCANS):
                metadata._drop(token)
    assert page['state'] == 'complete'
    # src plus six nested levels; a scandir handle per level would double it.
    assert 0 < held <= 7, held


def test_size_skips_links_and_counts_dependency_directories(project_root):
    (project_root / 'src' / 'outside').symlink_to(project_root.parent)
    (project_root / 'src' / 'node_modules').mkdir()
    (project_root / 'src' / 'node_modules' / 'large').write_bytes(b'x' * 10000)
    result = _measured({'project_id': 'p1', 'path': 'src'})
    assert result['state'] == 'incomplete'
    assert result['skipped'] == 1
    assert result['bytes'] == 10000 + len(b"print('hi')\n")
    link = _run(ws_files.handle_project_file_info, {'project_id': 'p1', 'path': 'src/outside'})['data']
    assert link['type'] == 'symlink'
    assert 'link_target' not in link
    assert _run(ws_files.handle_project_file_info, {'project_id': 'p1', 'path': 'src/outside/anything'})['data']['error_code']


def test_size_sort_uses_complete_totals_with_unknown_last(project_root):
    for name, size in [('small', 2), ('large', 40)]:
        (project_root / name).mkdir()
        (project_root / name / 'file').write_bytes(b'x' * size)
        size_cmd = {'project_id': 'p1', 'path': name}
        result = _run(ws_files.handle_project_folder_size, {**size_cmd, 'operation': 'start'})['data']
        # A scan works in 50 ms slices; on a loaded machine one slice may not finish.
        while result['state'] == 'partial':
            result = _run(ws_files.handle_project_folder_size, {**size_cmd, 'operation': 'continue', 'token': result['token']})['data']
        assert result['state'] == 'complete'
    cmd = {'project_id': 'p1', 'path': '', 'sort': 'size:desc:folders:hidden:ignored'}
    result = _run(ws_files.handle_project_file_tree, cmd)['data']
    dirs = [entry for entry in result['entries'] if entry['type'] == 'dir']
    assert [e['name'] for e in dirs[:2]] == ['large', 'small']
    assert all(e['size'] is None for e in dirs[2:])
    (project_root / 'large' / 'new').write_bytes(b'changed')
    result = _run(ws_files.handle_project_file_tree, cmd)['data']
    assert next(e for e in result['entries'] if e['name'] == 'large')['size'] is None


def test_sort_conditions_are_part_of_cursor_identity(project_root):
    cmd = {'project_id': 'p1', 'path': '', 'page_size': 1, 'sort': 'name:asc:folders:hidden:ignored'}
    page = _run(ws_files.handle_project_file_tree, cmd)['data']
    assert page['next_cursor']
    result = _run(ws_files.handle_project_file_tree, {**cmd, 'cursor': page['next_cursor'], 'sort': 'name:desc:folders:hidden:ignored'})['data']
    assert result['error_code']
    result = _run(ws_files.handle_project_file_tree, {**cmd, 'page_size': 200, 'sort': 'name:asc:folders:visible:ignored'})['data']
    assert all(not entry['name'].startswith('.') for entry in result['entries'])


def test_metadata_rejects_malformed_requests(project_root):
    for cmd in ({'project_id': [], 'path': ''}, {'project_id': 'p1', 'path': ['src']}, {'project_id': 'p1', 'path': '../outside'}):
        assert _run(ws_files.handle_project_file_info, cmd)['data']['error_code'] == 'INVALID_REQUEST'
    result = _run(ws_files.handle_project_folder_size, {'project_id': 'p1', 'path': '', 'operation': []})['data']
    assert result['error_code'] == 'INVALID_REQUEST'


def test_size_counts_search_ignored_cache_directories(project_root):
    cache = project_root / '.cache'
    (cache / 'node_modules').mkdir(parents=True)
    (cache / 'a').write_bytes(b'abc')
    (cache / 'node_modules' / 'b').write_bytes(b'12345')
    cmd = {'project_id': 'p1', 'path': '.cache'}
    info = _run(ws_files.handle_project_file_info, cmd)['data']
    assert info.get('type') == 'dir', info
    size = _run(ws_files.handle_project_folder_size, {**cmd, 'operation': 'start'})['data']
    while size.get('token'):
        size = _run(ws_files.handle_project_folder_size, {**cmd, 'operation': 'continue', 'token': size['token']})['data']
    assert size.get('bytes') == 8, size
    assert size['state'] == 'complete'


def test_size_sample_survives_worker_cache_reload(project_root, monkeypatch):
    from openprogram.interfaces.server.ws_actions.files import metadata
    cmd = {'project_id': 'p1', 'path': 'src'}
    sample = _measured(cmd)
    assert sample['state'] == 'complete'
    metadata._CACHE.clear()
    monkeypatch.setattr(metadata, '_CACHE_FILE', None, raising=False)
    def no_walk(*args):
        raise AssertionError('cached size must not recursively read the directory')
    monkeypatch.setattr(metadata, '_walk', no_walk)
    cached = _run(ws_files.handle_project_folder_size, cmd)['data']
    assert cached['state'] == 'cached'
    assert cached['bytes'] == sample['bytes']
    assert cached['updated_at'] == sample['updated_at']
    assert cached['complete'] is True


def test_size_sample_survives_a_fresh_worker_process(project_root):
    import subprocess
    import sys
    from pathlib import Path
    cmd = {'project_id': 'p1', 'path': 'src'}
    sample = _measured(cmd)
    code = """
import asyncio, json, sys, types
from openprogram.state.store.project import project_store
from openprogram.state.store.project.identity import capture_directory_identity
from openprogram.interfaces.server.ws_actions import files
from openprogram.interfaces.server.ws_actions.files import metadata
root = sys.argv[1]
project_store.get_project = lambda _: types.SimpleNamespace(id='p1', path=root, **capture_directory_identity(root))
def no_walk(*args):
    raise AssertionError('cache lookup must not walk')
metadata._walk = no_walk
class WS:
    async def send_text(self, value): print(value)
asyncio.run(files.handle_project_folder_size(WS(), {'project_id': 'p1', 'path': 'src'}))
"""
    output = subprocess.run([sys.executable, '-c', code, str(project_root)],
                            cwd=Path(__file__).resolve().parents[4], capture_output=True, text=True, check=True)
    import json
    cached = json.loads(output.stdout)['data']
    assert cached['state'] == 'cached'
    assert cached['bytes'] == sample['bytes']
    assert cached['complete'] is True


def test_persistent_incomplete_sample_preserves_skipped_entries(project_root, monkeypatch):
    from openprogram.interfaces.server.ws_actions.files import metadata
    (project_root / 'src' / 'link').symlink_to(project_root.parent)
    cmd = {'project_id': 'p1', 'path': 'src'}
    sample = _measured(cmd)
    assert sample['state'] == 'incomplete'
    metadata._CACHE.clear()
    monkeypatch.setattr(metadata, '_CACHE_FILE', None)
    cached = _run(ws_files.handle_project_folder_size, cmd)['data']
    assert cached['state'] == 'cached'
    assert cached['complete'] is False
    assert cached['bytes'] == sample['bytes']
    assert cached['skipped'] == 1
    assert cached['token'] is None
    assert metadata.cached_size('p1', 'src') is None


def test_size_cache_unavailable_or_corrupt_still_allows_queries(project_root, monkeypatch, tmp_path):
    from openprogram.state import paths
    state = tmp_path / 'state'
    state.mkdir()
    monkeypatch.setattr(paths, 'get_state_dir', lambda: state)
    (state / 'folder-sizes.json').write_text('{invalid JSON')
    cmd = {'project_id': 'p1', 'path': 'src'}
    assert _run(ws_files.handle_project_folder_size, cmd)['data']['state'] == 'unknown'
    from openprogram.state.store.session.files import atomic_file
    def unavailable(*args):
        raise OSError('read-only storage')
    monkeypatch.setattr(atomic_file, 'atomic_write_text', unavailable)
    sample = _measured(cmd)
    assert sample['state'] == 'complete'
    assert _run(ws_files.handle_project_folder_size, cmd)['data']['bytes'] == sample['bytes']


def test_persisted_size_is_bound_to_directory_identity(project_root, monkeypatch):
    from openprogram.interfaces.server.ws_actions.files import metadata
    cmd = {'project_id': 'p1', 'path': 'src'}
    assert _run(ws_files.handle_project_folder_size, {**cmd, 'operation': 'start'})['data']['state'] == 'complete'
    (project_root / 'src').rename(project_root / 'old-src')
    (project_root / 'src').mkdir()
    metadata._CACHE.clear()
    monkeypatch.setattr(metadata, '_CACHE_FILE', None)
    assert _run(ws_files.handle_project_folder_size, cmd)['data']['state'] == 'unknown'


@pytest.mark.parametrize('action,extra', [
    ('delete', {}), ('write', {'content': 'updated contents'}),
    ('rename', {'new_path': 'moved.txt'}),
    ('copy', {'new_path': 'src/nested/copy.txt'}),
    ('create', {'kind': 'file'}),
])
def test_file_mutation_invalidates_persisted_ancestor_size(project_root, monkeypatch, action, extra):
    from openprogram.interfaces.server.ws_actions.files import metadata
    (project_root / 'src' / 'nested').mkdir()
    (project_root / 'src' / 'nested' / 'one').write_bytes(b'12345')
    cmd = {'project_id': 'p1', 'path': 'src'}
    sample = _measured(cmd)
    assert sample['state'] == 'complete'
    metadata._CACHE.clear()
    monkeypatch.setattr(metadata, '_CACHE_FILE', None)
    path = 'src/nested/new' if action == 'create' else 'src/nested/one'
    result = _run(getattr(ws_files, f'handle_project_file_{action}'),
                  {'project_id': 'p1', 'path': path, 'idempotency_key': f'cache-{action}', **extra})['data']
    assert result.get('ok'), result
    metadata._CACHE.clear()
    monkeypatch.setattr(metadata, '_CACHE_FILE', None)
    assert _run(ws_files.handle_project_folder_size, cmd)['data']['state'] == 'unknown'


def test_mutation_cancels_affected_scan_and_retains_unrelated_sample(project_root, monkeypatch):
    from openprogram.interfaces.server.ws_actions.files import metadata
    unrelated = {'project_id': 'p1', 'path': 'Alpha_dir'}
    assert _run(ws_files.handle_project_folder_size, {**unrelated, 'operation': 'start'})['data']['state'] == 'complete'
    monkeypatch.setattr(metadata, '_BATCH', 1)
    cmd = {'project_id': 'p1', 'path': 'src'}
    page = _run(ws_files.handle_project_folder_size, {**cmd, 'operation': 'start'})['data']
    assert page['state'] == 'partial'
    result = _run(ws_files.handle_project_file_write,
                  {'project_id': 'p1', 'path': 'src/x.py', 'content': 'new bytes'})['data']
    assert result.get('ok'), result
    resumed = _run(ws_files.handle_project_folder_size,
                   {**cmd, 'operation': 'continue', 'token': page['token']})['data']
    assert resumed['error_code'] == 'INVALID_REQUEST'
    assert _run(ws_files.handle_project_folder_size, unrelated)['data']['state'] == 'cached'


def test_mutation_invalidates_project_root_sample_and_scan(project_root, monkeypatch):
    from openprogram.interfaces.server.ws_actions.files import metadata
    cmd = {'project_id': 'p1', 'path': ''}
    page = _run(ws_files.handle_project_folder_size, {**cmd, 'operation': 'start'})['data']
    while page.get('token'):
        page = _run(ws_files.handle_project_folder_size, {**cmd, 'operation': 'continue', 'token': page['token']})['data']
    assert page['state'] in {'complete', 'incomplete'}
    monkeypatch.setattr(metadata, '_BATCH', 1)
    active = _run(ws_files.handle_project_folder_size, {**cmd, 'operation': 'start'})['data']
    assert active['state'] == 'partial'
    try:
        result = _run(ws_files.handle_project_file_write,
                      {'project_id': 'p1', 'path': 'src/x.py', 'content': 'root sample changes'})['data']
        assert result.get('ok'), result
        metadata._CACHE.clear()
        monkeypatch.setattr(metadata, '_CACHE_FILE', None)
        assert _run(ws_files.handle_project_folder_size, cmd)['data']['state'] == 'unknown'
        resumed = _run(ws_files.handle_project_folder_size,
                       {**cmd, 'operation': 'continue', 'token': active['token']})['data']
        assert resumed['error_code'] == 'INVALID_REQUEST'
        assert 'expired' in resumed['error']
    finally:
        with metadata._LOCK:
            metadata._drop(active['token'])


def _counting_walk(metadata, monkeypatch):
    walks = []
    real = metadata._walk

    def walk(*args):
        walks.append(args)
        return real(*args)
    monkeypatch.setattr(metadata, '_walk', walk)
    return walks


def test_measure_answers_a_folder_in_one_request_and_reuses_the_sample(project_root, monkeypatch):
    from openprogram.interfaces.server.ws_actions.files import metadata
    walks = _counting_walk(metadata, monkeypatch)
    cmd = {'project_id': 'p1', 'path': 'src', 'operation': 'measure'}
    first = _run(ws_files.handle_project_folder_size, cmd)['data']
    assert first['state'] == 'complete'
    assert first['bytes'] == len(b"print('hi')\n")
    again = _run(ws_files.handle_project_folder_size, cmd)['data']
    assert again['state'] == 'cached' and again['bytes'] == first['bytes']
    assert len(walks) == 1, 'a saved sample answers without walking the folder again'
    (project_root / 'src' / 'more.txt').write_bytes(b'12345')
    fresh = _run(ws_files.handle_project_folder_size, {**cmd, 'fresh': True})['data']
    assert fresh['state'] == 'complete' and fresh['bytes'] == first['bytes'] + 5
    assert len(walks) == 2, 'refresh walks once'


def test_measure_saves_a_budget_limited_sample_for_the_next_view(project_root, monkeypatch):
    from openprogram.interfaces.server.ws_actions.files import metadata
    monkeypatch.setattr(metadata, '_BATCH', 1)
    monkeypatch.setattr(metadata, '_MEASURE_BUDGET', 0)
    for n in range(5):
        (project_root / 'src' / f'{n}.txt').write_bytes(b'123')
    walks = _counting_walk(metadata, monkeypatch)
    cmd = {'project_id': 'p1', 'path': 'src', 'operation': 'measure'}
    try:
        partial = _run(ws_files.handle_project_folder_size, cmd)['data']
        assert partial['state'] == 'partial' and partial['token']
        metadata._CACHE.clear()
        monkeypatch.setattr(metadata, '_CACHE_FILE', None)
        cached = _run(ws_files.handle_project_folder_size, cmd)['data']
        assert cached['state'] == 'cached'
        assert cached['complete'] is False and cached['token'] is None
        assert cached['bytes'] == partial['bytes']
        assert len(walks) == 1
        assert metadata.cached_size('p1', 'src') is None, 'size sorting uses complete samples only'
        # Details can still continue the walk the measure left open.
        page = partial
        while page.get('token'):
            page = _run(ws_files.handle_project_folder_size, {'project_id': 'p1', 'path': 'src', 'operation': 'continue', 'token': page['token']})['data']
        assert page['state'] == 'complete'
    finally:
        with metadata._LOCK:
            for token in list(metadata._SCANS):
                metadata._drop(token)


def test_measure_partial_does_not_replace_a_newer_sample(project_root, monkeypatch):
    from openprogram.interfaces.server.ws_actions.files import metadata
    monkeypatch.setattr(metadata, '_BATCH', 1)
    monkeypatch.setattr(metadata, '_MEASURE_BUDGET', 0)
    for n in range(5):
        (project_root / 'src' / f'{n}.txt').write_bytes(b'123')
    real = metadata.folder_size
    newer = {}

    def folder_size(project_id, path, operation='peek', token=None, fresh=False):
        result = real(project_id, path, operation, token, fresh)
        if operation == 'start':
            # Another request finishes a full count while this one is walking.
            info = metadata.file_info(project_id, path)
            with metadata._LOCK:
                newer.update({'state': 'complete', 'bytes': 999, 'entries': 9, 'skipped': 0, 'complete': True,
                              'token': None, 'updated_at': time.time() + 60, 'revision': info['revision']})
                metadata._store(info['absolute_path'], dict(newer))
        return result
    monkeypatch.setattr(metadata, 'folder_size', folder_size)
    try:
        partial = _run(ws_files.handle_project_folder_size,
                       {'project_id': 'p1', 'path': 'src', 'operation': 'measure', 'fresh': True})['data']
        assert partial['state'] == 'partial'
        cached = real('p1', 'src', 'peek')
        assert cached['bytes'] == 999 and cached['complete'] is True
    finally:
        with metadata._LOCK:
            for token in list(metadata._SCANS):
                metadata._drop(token)
