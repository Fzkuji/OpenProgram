"""No frontend automation: invoke the existing authenticated backend operation."""
import base64
import re
from urllib.parse import quote

from .authority import require_owner


def call(action='describe', operation='', arguments=None):
    from openprogram.programs.application_client import request
    require_owner()
    values = dict(arguments or {})
    if action == 'describe':
        return request('/api/framework/operations?query=' + quote(operation, safe='')
                       + '&offset=' + str(int(values.get('offset', 0))))
    if action == 'commands':
        return request('/api/framework/commands')
    if action == 'command':
        return request('/api/framework/commands/' + quote(operation, safe=''), method='POST', body=values)
    if action == 'interface':
        from openprogram.agent.surface_context import current
        context = current() or {}
        window = context.get('origin_window_id') or context.get('window_id') or ''
        window = values.get('window_id', window)
        body = {'operation': operation, 'window_id': window, 'arguments': values.get('arguments', [])}
        if operation == 'tabs.openWebTab':
            if len(body['arguments']) != 1 or not isinstance(body['arguments'][0], str):
                raise ValueError('invalid_web_open_arguments')
            from openprogram.resources import registry
            return registry.invoke('web', 'open', {'url': body['arguments'][0]})
        if operation.startswith('native.webTab.') or operation == 'tabs.setWebTabPinned':
            from .web_interface import authorized_page
            with authorized_page(values, window) as page:
                return request('/api/framework/interface', method='POST', body={**body, 'window_id': page['window_id'], 'page': page})
        return request('/api/framework/interface', method='POST', body=body)
    if action != 'invoke':
        raise ValueError('unsupported_framework_action')
    method, _, template = operation.partition(' ')
    # Only a registered product operation is callable; no arbitrary URL, auth
    # header, redirect, internal transport endpoint or path traversal fallback.
    found = request('/api/framework/operations?query=' + quote(operation, safe=''))
    if not any(row['id'] == operation for row in found['operations']):
        raise ValueError('unknown_framework_operation')
    if operation == 'POST /api/application-instances/{key}/operations/{operation}':
        if 'content_base64' in values or values.get('content_type', 'application/json') != 'application/json':
            raise ValueError('application_operation_requires_json')
        values['body'] = {**values.get('body', {}), 'agent': True}
    parameters = values.get('path', {})
    def replace(match):
        key = match.group(1).split(':')[0]
        value = str(parameters[key])
        if value in {'.', '..'} or '\x00' in value:
            raise ValueError('invalid_path_parameter')
        return quote(value, safe='')
    path = re.sub(r'\{([^}]+)\}', replace, template)
    from openprogram.backend_endpoint import resolve_backend_endpoint
    import httpx
    endpoint = resolve_backend_endpoint()
    query = values.get('query', {})
    if any(str(key).startswith('_') for key in query):
        raise ValueError('internal_query_parameter')
    options = {'params': query}
    if 'body' in values:
        options['json'] = values['body']
    if 'content_base64' in values:
        options['content'] = base64.b64decode(values['content_base64'], validate=True)
    headers = {'Authorization': endpoint.authorization_header, 'Origin': endpoint.origin}
    if 'content_type' in values:
        headers['Content-Type'] = values['content_type']
    with httpx.Client(timeout=360, follow_redirects=False, trust_env=False) as client:
        response = client.request(method, endpoint.base_url + path, headers=headers, **options)
    if 'json' in response.headers.get('content-type', ''):
        return {'ok': not response.is_error, 'status': response.status_code, 'result': response.json()}
    if len(response.content) > 4 * 1024 * 1024:
        raise ValueError('framework_response_too_large_use_file_export')
    return {'ok': not response.is_error, 'status': response.status_code,
            'content_type': response.headers.get('content-type'),
            'content_base64': base64.b64encode(response.content).decode()}
