from openprogram.webui.session_history import history_page


def test_pages_keep_caller_rows_and_bound_bytes_without_losing_history():
    roots = [{'id': f'm{i}', 'role': 'assistant', 'content': '中' * 40000} for i in range(12)]
    child = {'id': 'tool', 'caller': 'm11', 'role': 'tool', 'content': 'result'}
    rows = [*roots, child]
    cursor = None
    found = []
    while True:
        page, next_cursor = history_page(rows, {r['id'] for r in roots}, cursor)
        found = page + found
        if any(r['id'] == 'm11' for r in page):
            assert child in page
        if next_cursor is None:
            break
        assert next_cursor != cursor
        cursor = next_cursor
    assert sorted(r['id'] for r in found) == sorted(r['id'] for r in rows)


def test_single_oversized_row_is_not_dropped_and_wire_copies_are_removed():
    row = {'id': 'a', 'content': 'x' * 700000, 'blocks': [{'result': 'preview'}],
           'extra': {'blocks': [{'result': 'original'}], 'custom': 3}}
    page, cursor = history_page([row], {'a'})
    assert page[0]['content'] == row['content']
    assert page[0]['extra'] == {'custom': 3}
    assert row['extra']['blocks'][0]['result'] == 'original'
    assert cursor is None


def test_indexed_snapshot_reads_only_selected_pages_and_supports_reverse_seek(tmp_path):
    from openprogram.webui.session_history import HistorySnapshot, close_snapshots, install_snapshot
    from types import SimpleNamespace
    import time
    count = 300_000
    rows = [{'id': f'm{i}', 'role': 'user', 'content': f'message {i}'} for i in range(count)]
    started = time.perf_counter()
    snapshot = HistorySnapshot('session', 'head', rows, {m['id'] for m in rows})
    indexed = time.perf_counter() - started
    ws = SimpleNamespace()
    install_snapshot(ws, snapshot)
    try:
        latest, meta = snapshot.page()
        assert len(latest) == 50 and meta['end'] == count
        started = time.perf_counter()
        earlier, prev = snapshot.page(before=meta['before'])
        assert earlier[-1]['id'] == f'm{count-51}'
        newer, following = snapshot.page(after=prev['after'])
        assert newer == latest
        middle, center = snapshot.page(around='m123456')
        assert any(m['id'] == 'm123456' for m in middle)
        assert center['before'] and center['after']
        print({'messages': count, 'index_seconds': indexed, 'three_page_reads_seconds': time.perf_counter()-started})
        # Bodies are fetched by indexed positions, never a full-table body scan.
        plan = snapshot._db.execute('EXPLAIN QUERY PLAN SELECT position,id,size FROM groups WHERE position>? ORDER BY position LIMIT 50', (150000,)).fetchall()
        assert any('SEARCH' in row[-1] for row in plan)
    finally:
        directory = snapshot._directory.name
        close_snapshots(ws)
    from pathlib import Path
    assert not Path(directory).exists()
