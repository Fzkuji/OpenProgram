"""Platform web use."""
from __future__ import annotations
from importlib import import_module

# Bind to this workflow package, including an isolated published snapshot.
state = import_module("..", __package__)


def _execute_web_use(
    command: str, backend: str = "", page: str = "",
    page_context_token: str = "", web_session_id: str = "",
    arguments: dict | None = None,
) -> dict | state.ToolReturn:
    from openprogram.agent import surface_context
    from ..web_use_runtime import get_registry

    payload = state.normalize_web_use_arguments({
        "command": command,
        "backend": backend,
        "page": page,
        "page_context_token": page_context_token,
        "web_session_id": web_session_id,
        "arguments": arguments,
    })
    command = str(payload.get("command") or command)
    backend = str(payload.get("backend") or "")
    page = str(payload.get("page") or "")
    page_context_token = str(payload.get("page_context_token") or "")
    web_session_id = str(payload.get("web_session_id") or "")
    arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}

    context = surface_context.current()
    owner_context_id = str((context or {}).get("context_id") or "")
    captured_here = False
    opened_here = False
    url = state._requested_url(arguments)
    if command in {"observe", "act"} and url and not state._has_usable_page(
        context, web_session_id, page_context_token,
    ):
        opened = surface_context.open_page(url)
        if "surfaces" not in opened:
            return state._open_page_error(opened)
        context = opened
        captured_here = True
        opened_here = True
        web_session_id = ""
        page_context_token = ""
    needs_page_capture = (
        not opened_here
        and context is None
        and (
            command == "list_pages"
            or (
                command == "observe"
                and not web_session_id
                and not page_context_token
            )
        )
    )
    if needs_page_capture:
        context = (
            surface_context.capture_pages()
            if command == "list_pages"
            else surface_context.capture_active()
        )
        captured_here = True

    if command == "list_pages":
        if not captured_here:
            context = surface_context.capture_pages(
                context if surface_context.tool_enabled(context) else None
            )
            captured_here = True
        context = context or {}
        owner_id = surface_context.web_use_owner_id(
            {"context_id": owner_context_id} if owner_context_id else context
        )
        try:
            result = get_registry().list_pages(context=context, owner_id=owner_id)
        except Exception:
            if captured_here:
                surface_context.release_bindings(context)
            raise
        if captured_here and not result.get("ok"):
            surface_context.release_bindings(context)
        return result

    owner_id = surface_context.web_use_owner_id(
        {"context_id": owner_context_id} if owner_context_id else context
    )
    if opened_here:
        try:
            observed = state._start_session_on_opened_page(
                context=context,
                owner_id=owner_id,
                backend=backend,
                arguments=arguments if command == "observe" else {},
            )
        except Exception:
            surface_context.release_bindings(context)
            raise
        if observed.get("ok") is False:
            return observed
        if command == "observe" or str(arguments.get("action") or "") in {
            "", "navigate",
        }:
            return observed
        try:
            result = get_registry().execute(
                command="act",
                backend=backend,
                web_session_id=str(observed.get("web_session_id") or ""),
                owner_id=owner_id,
                page_context=context,
                arguments=arguments,
            )
        except Exception:
            surface_context.release_bindings(context)
            raise
        if isinstance(result, dict):
            result = dict(result)
            result.setdefault("web_session_id", observed.get("web_session_id"))
        return result

    binding_id = ""
    page_key = ""
    if command == "observe" and not web_session_id and not page_context_token:
        token = surface_context.bind(context)
        try:
            binding_id = surface_context.resolve_binding(page)
            page_key = surface_context.resolve_page_key(page)
        except Exception:
            if captured_here:
                surface_context.release_bindings(context)
            raise
        finally:
            surface_context.reset(token)
    try:
        result = get_registry().execute(
            command=command,
            backend=backend,
            web_session_id=web_session_id,
            binding_id=binding_id,
            page_key=page_key,
            owner_id=owner_id,
            page_context_token=page_context_token,
            page_context=context,
            arguments=arguments,
        )
    except Exception:
        if captured_here:
            surface_context.release_bindings(context)
        raise
    if captured_here and command == "observe" and (
        not result.get("web_session_id") or result.get("session_reused")
    ):
        surface_context.release_bindings(context)
    return result


def execute_direct_web_use(arguments: dict, *, owner_id: str):
    """Execute the first-class MCP contract with server-injected ownership."""
    from openprogram.agent import surface_context
    from ..web_use_runtime import get_registry

    arguments = state.normalize_web_use_arguments(arguments)
    command = str(arguments.get("command") or "")
    registry = get_registry()
    if command == "list_pages":
        context = surface_context.capture_pages()
        try:
            result = registry.list_pages(context=context, owner_id=owner_id)
        except Exception:
            surface_context.release_bindings(context)
            raise
        if not result.get("ok"):
            surface_context.release_bindings(context)
        return result
    nested = (
        arguments.get("arguments")
        if isinstance(arguments.get("arguments"), dict)
        else {}
    )
    web_session_id = str(arguments.get("web_session_id") or "")
    page_context_token = str(arguments.get("page_context_token") or "")
    url = state._requested_url(nested)
    if command in {"observe", "act"} and url and not state._has_usable_page(
        None, web_session_id, page_context_token,
    ):
        opened = surface_context.open_page(url)
        if "surfaces" not in opened:
            return state._open_page_error(opened)
        try:
            observed = state._start_session_on_opened_page(
                context=opened,
                owner_id=owner_id,
                backend=str(arguments.get("backend") or ""),
                arguments=nested if command == "observe" else {},
            )
        except Exception:
            surface_context.release_bindings(opened)
            raise
        if observed.get("ok") is False:
            return observed
        if command == "observe" or str(nested.get("action") or "") in {
            "", "navigate",
        }:
            return observed
        result = registry.execute(
            command="act",
            backend=str(arguments.get("backend") or ""),
            web_session_id=str(observed.get("web_session_id") or ""),
            owner_id=owner_id,
            page_context=opened,
            arguments=nested,
        )
        if isinstance(result, dict):
            result = dict(result)
            result.setdefault("web_session_id", observed.get("web_session_id"))
        return result
    return registry.execute(
        command=command,
        backend=str(arguments.get("backend") or ""),
        web_session_id=web_session_id,
        owner_id=owner_id,
        page_context_token=page_context_token,
        arguments=nested,
    )
