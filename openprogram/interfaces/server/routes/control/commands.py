"""HTTP adapter for the same business command handlers used by WebSocket clients."""
import json

from openprogram.interfaces.server.transcript.flat_rows import HTTP_ROW_BYTES

PERMISSION_COMMANDS = {'set_permission', 'add_permission_rule', 'remove_permission_rule'}


class CommandReply:
    """Request-scoped reply sink; ongoing execution stays in canonical stores."""
    def __init__(self, request):
        self.scope = request.scope
        self.headers = request.headers
        self.app = request.app
        self.frames = []
        self.closed = False
        self.size = 0
        # load_session answers with the newest plain rows that fit.
        self.flat_row_byte_limit = HTTP_ROW_BYTES

    async def send_text(self, text):
        if self.closed:
            return
        self.size += len(text.encode())
        if self.size > 4 * 1024 * 1024:
            raise ValueError('command_reply_too_large')
        self.frames.append(json.loads(text))

    async def close(self, **_kwargs):
        self.closed = True


async def invoke(request, name, arguments):
    from .catalog import commands
    from openprogram.interfaces.server import server
    from openprogram.interfaces.server.ws_errors import OperationError
    from openprogram.runtime.permissions import refusal
    if name in PERMISSION_COMMANDS or 'permission_mode' in arguments or (
            name == 'set_project_config' and arguments.get('key') == 'permission_mode'):
        raise refusal.framework_permission_settings(name)
    if name not in commands():
        raise ValueError('unknown_or_internal_framework_command')
    reply = CommandReply(request)
    try:
        await server._handle_ws_command(reply, {**arguments, 'action': name})
    except OperationError as exc:
        return {'ok': False, 'error': exc.code, 'frames': reply.frames}
    finally:
        reply.closed = True
    def failed_frame(frame):
        if frame.get('type') in {'error', 'action_error', 'operation_error'}:
            return True
        data = frame.get('data')
        return isinstance(data, dict) and (bool(data.get('error')) or data.get('ok') is False
                                          or data.get('status') in {'error', 'failed'})
    failed = any(failed_frame(frame) for frame in reply.frames)
    return {'ok': not failed, 'frames': reply.frames,
            'note': 'Long-running work is observed through its execution or session API.'}
