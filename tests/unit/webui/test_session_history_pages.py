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
