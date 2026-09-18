"""Discover the registered product API instead of maintaining a second route list."""
import inspect

# Connection authentication, delivery receipts and approval decisions are not
# product operations an Agent may manufacture. Their dedicated paths remain.
INTERNAL_PREFIXES = ('/api/auth', '/api/terminal/claim', '/api/terminal/result', '/api/framework', '/api/execution/wait/answer')
INTERNAL_COMMANDS = {'webtab_register', 'webtab_result', 'webtab_closed', 'webtab_human_input',
                     'set_permission', 'add_permission_rule', 'remove_permission_rule',
                     'execution.wait.answer', 'revision.approve', 'follow_up_answer', 'framework_interface_register', 'framework_interface_result'}


def operations(app, query='', offset=0, limit=50):
    document = app.openapi()
    rows = []
    for path, methods in document.get('paths', {}).items():
        if '/revision/' in path and path.endswith('/approve'):
            continue
        if not path.startswith('/api/') or path.startswith(INTERNAL_PREFIXES):
            continue
        for method, operation in methods.items():
            if method not in {'get', 'post', 'put', 'patch', 'delete'}:
                continue
            key = method.upper() + ' ' + path
            if query.casefold() not in (key + ' ' + operation.get('summary', '')).casefold():
                continue
            rows.append({'id': key, 'path': path, 'method': method.upper(), **operation})
    return {'operations': rows[offset:offset + limit], 'total': len(rows),
            'next_offset': offset + limit if offset + limit < len(rows) else None,
            'components': document.get('components', {})}


def commands():
    from openprogram.webui import server
    return {name: handler for name, handler in server.WS_ACTIONS.items()
            if name not in INTERNAL_COMMANDS and not name.startswith('webtab_')}


def describe_commands():
    from .command_schema import arguments_for
    return [{'name': name, 'description': inspect.getdoc(handler) or name,
             'arguments': arguments_for(handler), 'transport': 'backend-command'}
            for name, handler in sorted(commands().items())]
