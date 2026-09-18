"""Live manifest adapters: install/enable/update/remove take effect without reload."""
from urllib.parse import quote

from ..registry import ResourceProvider


def installed_providers() -> list[str]:
    from openprogram.programs._applications.catalog import installed
    return ['application.' + app['id'] for app in installed() if app.get('enabled')]


def application_provider(app_id: str) -> ResourceProvider:
    from openprogram.programs._applications.catalog import get
    app = get(app_id)
    text = {'type': 'string', 'minLength': 1}
    identity = {'instance_id': text, 'digest': {'const': app['digest']}}
    def schema(fields, required=()):
        return {'type': 'object', 'properties': fields, 'required': list(required), 'additionalProperties': False}
    exact = ['instance_id', 'digest']
    actions = {
        'list': schema({}),
        'open': schema({'project_id': {'type': 'string'}, 'session_id': text}),
        'observe': schema(identity, exact),
        'release': schema({**identity, 'session_id': text}, [*exact, 'session_id']),
        'status': schema({**identity, 'run_id': text, 'after': {'type': 'integer', 'minimum': 0}}, [*exact, 'run_id']),
        'cancel': schema({**identity, 'run_id': text}, [*exact, 'run_id']),
        'answer': schema({**identity, 'run_id': text, 'request_id': text, 'answer': {}}, [*exact, 'run_id', 'request_id', 'answer']),
    }
    variants = []
    for name, operation in app['operations'].items():
        if operation.get('agent'):
            variants.append(schema({**identity, 'operation': {'const': name},
                'input': operation.get('input', {}), 'request_key': {**text, 'maxLength': 128}},
                [*exact, 'operation', 'input', 'request_key']))
    if variants:
        actions['act'] = {'oneOf': variants}
    if 'storage.app' in app['capabilities']:
        actions['save'] = schema({**identity, 'value': {}, 'version': {'type': 'integer', 'minimum': 0}}, [*exact, 'value', 'version'])
    def invoke(action, arguments):
        from openprogram.framework.authority import require_owner
        from openprogram.processes import current_owner
        from openprogram.programs.application_client import request
        require_owner()
        if action in {'open', 'release'}:
            session, _, _ = current_owner()
            if session:
                arguments = {**arguments, 'session_id': session}
        return request('/api/resources/' + quote('application.' + app_id, safe='') + '/' + action,
                       method='POST', body=arguments)
    return ResourceProvider('application.' + app_id, app.get('display_title') or app['title'], actions, invoke)
