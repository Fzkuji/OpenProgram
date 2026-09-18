"""HTTP adapter for the same business command handlers used by WebSocket clients."""
import json


class CommandReply:
    """Request-scoped reply sink; ongoing execution stays in canonical stores."""
    def __init__(self, request):
        self.scope = request.scope
        self.headers = request.headers
        self.app = request.app
        self.frames = []
        self.closed = False
        self.size = 0
        self._history_protocol = 1
        self._bounded_history = True

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
    from openprogram.webui import server
    from openprogram.webui.ws_errors import OperationError
    if name not in commands():
        raise ValueError('unknown_or_internal_framework_command')
    if ('permission_mode' in arguments or
            (name == 'set_project_config' and arguments.get('key') == 'permission_mode')):
        raise ValueError('framework_cannot_change_approval_policy')
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
