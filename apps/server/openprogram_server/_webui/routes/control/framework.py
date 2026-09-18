"""Authenticated operation discovery and request/response command entry."""
from fastapi import Request, Query
from fastapi.responses import JSONResponse


def register(app):
    from .catalog import operations, describe_commands
    from .commands import invoke

    @app.get('/api/framework/operations')
    def describe(query: str = '', offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
        return operations(app, query, offset, limit)

    @app.get('/api/framework/commands')
    def command_catalog():
        return {'commands': describe_commands()}

    @app.post('/api/framework/commands/{name}')
    async def command(name: str, body: dict, request: Request):
        try:
            return await invoke(request, name, body)
        except (ValueError, KeyError, TypeError) as exc:
            return JSONResponse({'ok': False, 'error': str(exc)}, status_code=400)

    @app.post('/api/framework/interface')
    async def interface(body: dict):
        from openprogram.framework.interface import describe, invoke
        try:
            if not body.get('operation'):
                return describe()
            return await invoke(body['operation'], body.get('arguments', []), body.get('window_id', ''), body.get('page'))
        except (ValueError, TypeError) as exc:
            return JSONResponse({'ok': False, 'error': str(exc)}, status_code=400)
        except Exception as exc:
            from jsonschema import ValidationError
            if isinstance(exc, ValidationError):
                return JSONResponse({'ok': False, 'error': exc.message}, status_code=400)
            raise
