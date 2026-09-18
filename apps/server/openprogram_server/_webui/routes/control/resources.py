"""Resource HTTP entries reuse the worker-owned application service."""
from fastapi.responses import JSONResponse
from openprogram.programs._applications import catalog, state
from openprogram.resources.application_bindings import bind, release
from openprogram.resources.providers.applications import application_provider


async def invoke(service, app_id, action, arguments):
    from jsonschema import Draft202012Validator
    provider = application_provider(app_id)
    schema = provider.actions.get(action)
    if schema is None:
        raise ValueError('unsupported_resource_action')
    Draft202012Validator(schema).validate(arguments)
    definition = catalog.get(app_id)
    if action == 'list':
        from openprogram.agent.authority import owner_principal_id
        with state.connect() as db:
            return {'instances': [dict(r) for r in db.execute('SELECT * FROM instances WHERE app_id=? AND owner=?', (app_id, owner_principal_id()))]}
    if action == 'open':
        instance = state.instance(definition, arguments.get('project_id', ''))
        if arguments.get('session_id'):
            bind(arguments['session_id'], instance['id'])
        return {'instance_id': instance['id'], 'digest': definition['digest'], 'application_id': app_id}
    instance = state.get_instance(arguments['instance_id'])
    if instance['app_id'] != app_id or arguments['digest'] != definition['digest']:
        raise ValueError('application_instance_or_version_changed')
    key = instance['id']
    if action == 'release':
        release(arguments['session_id'], key)
        return {'ok': True}
    if action == 'act':
        return await service.submit(key, arguments['operation'], arguments['input'], arguments['request_key'], agent=True)
    if action == 'save':
        return state.data(key, arguments['value'], expected_version=arguments['version'])
    if action == 'observe':
        with state.connect() as db:
            runs = [r[0] for r in db.execute('SELECT id FROM operations WHERE instance_id=? ORDER BY rowid DESC LIMIT 100', (key,))]
        return {'instance_id': key, 'digest': definition['digest'],
                'state': state.data(key) if 'storage.app' in definition['capabilities'] else None,
                'runs': [service.describe(run) for run in runs]}
    run_id = arguments['run_id']
    result = service.describe(run_id)
    if result['instance_id'] != key:
        raise ValueError('run_belongs_to_another_instance')
    if action == 'cancel':
        return await service.cancel(run_id)
    if action == 'answer':
        return await service.answer(run_id, arguments['request_id'], arguments['answer'])
    import json
    with state.connect() as db:
        events = db.execute('SELECT sequence,payload FROM events WHERE run_id=? AND sequence>? ORDER BY sequence LIMIT 200',
                            (run_id, arguments.get('after', 0))).fetchall()
    return {**result, 'events': [{'sequence': r[0], **json.loads(r[1])} for r in events]}


def register(app):
    @app.post('/api/applications/scaffold')
    def scaffold(body: dict):
        from openprogram.programs._applications.scaffold import create
        try:
            return create(body['path'], body['app_id'], body['title'])
        except (ValueError, KeyError, OSError) as exc:
            return JSONResponse({'error': str(exc)}, status_code=400)

    @app.get('/api/resources/providers')
    def providers():
        from openprogram.resources import registry
        return registry.describe()

    @app.post('/api/resources/{provider}/{action}')
    async def resource(provider: str, action: str, body: dict):
        try:
            if not provider.startswith('application.'):
                raise ValueError('use_native_resource_tool_for_web_and_terminal')
            return await invoke(app.state.applications, provider.removeprefix('application.'), action, body)
        except FileNotFoundError as exc:
            return JSONResponse({'error': str(exc)}, status_code=404)
        except (ValueError, KeyError, TypeError) as exc:
            return JSONResponse({'error': str(exc)}, status_code=400)
        except Exception as exc:
            from jsonschema import ValidationError
            if isinstance(exc, ValidationError):
                return JSONResponse({'error': exc.message}, status_code=400)
            raise
