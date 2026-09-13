"""Public file management query regression tests."""
from .test_project_files import project_root, _run
from openprogram.webui.ws_actions import files as ws_files


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
    from openprogram.webui.ws_actions.files import metadata as metadata
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


def test_size_skips_links_and_counts_dependency_directories(project_root):
    (project_root / 'src' / 'outside').symlink_to(project_root.parent)
    (project_root / 'src' / 'node_modules').mkdir()
    (project_root / 'src' / 'node_modules' / 'large').write_bytes(b'x' * 10000)
    result = _run(ws_files.handle_project_folder_size, {'project_id': 'p1', 'path': 'src', 'operation': 'start'})['data']
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
        result = _run(ws_files.handle_project_folder_size, {'project_id': 'p1', 'path': name, 'operation': 'start'})['data']
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
