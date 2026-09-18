"""Session associations for durable application instances; views do not own runs."""
from openprogram.programs._applications import state, catalog


def _table(db):
    db.execute('''CREATE TABLE IF NOT EXISTS resource_bindings (
        session_id TEXT NOT NULL, instance_id TEXT NOT NULL,
        PRIMARY KEY(session_id, instance_id))''')


def bind(session_id: str, instance_id: str) -> None:
    if not isinstance(session_id, str) or not session_id or len(session_id) > 256:
        raise ValueError('invalid session_id')
    state.get_instance(instance_id)
    with state.connect() as db:
        _table(db)
        db.execute('INSERT OR IGNORE INTO resource_bindings VALUES (?,?)', (session_id, instance_id))


def release(session_id: str, instance_id: str) -> None:
    with state.connect() as db:
        _table(db)
        db.execute('DELETE FROM resource_bindings WHERE session_id=? AND instance_id=?', (session_id, instance_id))


def list_bindings(session_id: str) -> list[dict]:
    with state.connect() as db:
        _table(db)
        ids = [r[0] for r in db.execute('SELECT instance_id FROM resource_bindings WHERE session_id=?', (session_id,))]
    result = []
    for key in ids:
        try:
            instance = state.get_instance(key)
            app = catalog.get(instance['app_id'])
        except FileNotFoundError:
            continue
        result.append({'id': 'application:' + key, 'source': 'application', 'kind': 'application',
            'session_id': session_id, 'title': app.get('display_title') or app['title'],
            'target': app['id'], 'status': 'attached', 'application_id': app['id'],
            'application_instance_id': key, 'application_digest': app['digest']})
    return result
