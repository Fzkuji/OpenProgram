"""Session search uses canonical placement and literal query semantics."""
import shutil

import pytest

from openprogram.store import SessionStore
from openprogram.store.session import search


@pytest.fixture(params=['python', 'rg'])
def store(request, tmp_path, monkeypatch):
    if request.param == 'rg' and not shutil.which('rg'):
        pytest.skip('ripgrep is not installed')
    monkeypatch.setattr(search, '_have_rg', lambda: request.param == 'rg')
    result = SessionStore(tmp_path / 'sessions')
    result.create_session('search', 'main', title='Search title')
    result.append_message('search', {
        'id': 'node', 'role': 'user', 'content': 'Unique_text [literal --needle',
    })
    try:
        yield result
    finally:
        result.close()


@pytest.mark.parametrize('query', ['unique_TEXT', '[literal', '--needle'])
def test_literal_query_matches_with_either_engine(store, query):
    hits = store.search_messages(query, session_id='search')
    assert [hit['id'] for hit in hits] == ['node']
    assert hits[0]['session_title'] == 'Search title'
    assert hits[0]['session_agent_id'] == 'main'


def test_scoped_search_follows_relocated_session(store):
    source = store._session_dir('search')
    target = store.root_path / 'projects' / 'project' / 'search'
    target.parent.mkdir(parents=True)
    source.rename(target)
    store._record_location('search', target)
    assert [hit['id'] for hit in store.search_messages('Unique_text', session_id='search')] == ['node']
    assert not source.exists()


@pytest.mark.parametrize('limit', [0, -1])
def test_nonpositive_limit_returns_no_results(store, limit):
    assert store.search_messages('Unique_text', limit=limit) == []


@pytest.mark.parametrize('session_id', ['missing', '../search', ''])
def test_missing_or_invalid_scope_does_not_search_other_sessions(store, session_id):
    assert store.search_messages('Unique_text', session_id=session_id) == []
    assert not (store.root_path / 'missing').exists()


def test_deleted_scope_is_not_recreated(store):
    store.delete_session('search')
    assert store.search_messages('Unique_text', session_id='search') == []
    assert store.get_session('search') is None
