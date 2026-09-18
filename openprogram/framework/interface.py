"""Exact-connection UI command requests; timeout never resends a mutation."""
import asyncio
import json
from pathlib import Path
import secrets
from jsonschema import Draft202012Validator

COMMANDS = json.loads(Path(__file__).with_name('interface_commands.json').read_text())
_windows = {}
_pending = {}


async def register_window(ws, command):
    from openprogram.webui.ws_actions.runtime import trusted_runtime_actor
    actor = trusted_runtime_actor(getattr(ws, 'scope', None), surface='interface')
    if not actor or actor.get('session_ids') or actor.get('project_ids'):
        raise PermissionError('interface_requires_unrestricted_owner_connection')
    value = command.get('window_id')
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        raise ValueError('invalid_interface_window')
    # Registration only declares a view on an authenticated owner connection.
    _windows[ws] = value


async def result(ws, command):
    entry = _pending.get(command.get('request_id'))
    if entry is None or entry[0] is not ws:
        return
    value = command.get('result')
    if not isinstance(value, dict) or len(json.dumps(value)) > 4 * 1024 * 1024:
        return
    future = entry[1]
    if not future.done():
        future.set_result(value)


def release(ws):
    _windows.pop(ws, None)
    for owner, future in list(_pending.values()):
        if owner is ws and not future.done():
            future.set_result({'ok': False, 'error': 'interface_disconnected', 'result_unconfirmed': True})


def describe():
    return {'commands': COMMANDS, 'windows': sorted(set(_windows.values()))}


async def invoke(operation, arguments, window_id='', page=None):
    descriptor = COMMANDS.get(operation)
    if descriptor is None:
        raise ValueError('unsupported_interface_operation')
    Draft202012Validator(descriptor['arguments']).validate(arguments)
    candidates = [ws for ws, wid in _windows.items() if not window_id or wid == window_id]
    if len(candidates) != 1:
        raise ValueError('select_one_connected_interface_window')
    if len(_pending) >= 128:
        raise ValueError('interface_busy')
    ws = candidates[0]
    request_id = secrets.token_hex(16)
    future = asyncio.get_running_loop().create_future()
    _pending[request_id] = (ws, future)
    try:
        await ws.send_text(json.dumps({'type': 'framework.interface', 'data': {
            'request_id': request_id, 'window_id': _windows[ws],
            'operation': operation, 'arguments': arguments, 'page': page}}))
        try:
            return await asyncio.wait_for(future, timeout=30)
        except asyncio.TimeoutError:
            return {'ok': False, 'error': 'interface_result_unconfirmed', 'result_unconfirmed': True}
    finally:
        _pending.pop(request_id, None)
