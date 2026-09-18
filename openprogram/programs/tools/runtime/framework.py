"""Use product operations without clicking the OpenProgram frontend."""
from openprogram.programs._runtime import function


@function(name='framework', toolset=['core'], unsafe_in=['wechat', 'telegram', 'plan'],
          requires_approval=True, path_params={}, url_params=[], max_result_chars=48_000)
def framework(action: str = 'describe', operation: str = '', arguments: dict | None = None):
    """Discover and invoke authenticated OpenProgram backend operations.

    describe searches HTTP operations by operation text and arguments.offset.
    invoke uses the exact returned id (METHOD /api/path/{parameter}); arguments
    has path, query, body, or content_base64/content_type for binary uploads.
    commands lists business commands; command invokes a named command with its
    ordinary JSON arguments, returning response frames. Long work continues in
    the backend; use execution/session APIs to observe it. interface with empty
    operation describes exact-window UI/native commands; use returned names and
    schemas to manipulate views without screen automation. Never retry an
    uncertain mutation or use this entry to answer your own approval request.
    Tool approval and owner/backend/sandbox restrictions still apply.
    """
    from openprogram.framework.client import call
    from openprogram.programs import ToolReturn
    try:
        result = call(action, operation, arguments)
        return ToolReturn(json_data=result, is_error=result.get('ok') is False)
    except (ValueError, PermissionError, RuntimeError, OSError, KeyError) as exc:
        return ToolReturn(json_data={'ok': False, 'error': str(exc)}, is_error=True)


setattr(framework, '_run_in_worker', True)
