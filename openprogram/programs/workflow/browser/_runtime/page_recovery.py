"""Platform page recovery."""
from __future__ import annotations
from importlib import import_module

# Bind to this workflow package, including an isolated published snapshot.
state = import_module("..", __package__)


def _requested_url(arguments: dict | None) -> str:
    if not isinstance(arguments, dict):
        return ""
    url = arguments.get("url")
    return url.strip() if isinstance(url, str) else ""


def _has_usable_page(context, web_session_id: str, page_context_token: str) -> bool:
    from ..web_use_runtime import _unresolved_session_id

    if web_session_id and not _unresolved_session_id(web_session_id):
        return True
    if page_context_token.startswith("pct_"):
        return True
    for surface in (context or {}).get("surfaces") or []:
        if isinstance(surface, dict) and surface.get("binding_id"):
            return True
    return False


def _open_page_error(opened: dict) -> dict:
    from openprogram.agent.surface_context import DESKTOP_UNAVAILABLE_ERROR

    return {
        **opened,
        "ok": False,
        "reason_code": opened.get("reason_code") or "desktop_unavailable",
        "error": opened.get("error") or DESKTOP_UNAVAILABLE_ERROR,
    }


def _opened_observation_is_live(observed) -> bool:
    """True when observe-on-open returned a reusable live Page session."""
    if not isinstance(observed, dict):
        return False
    if observed.get("ok") is False:
        return False
    if observed.get("closed") is True:
        return False
    if not str(observed.get("frame_id") or "").strip():
        return False
    if not str(observed.get("web_session_id") or "").strip():
        return False
    return True


def _start_session_on_opened_page(*, context, owner_id: str, backend: str, arguments: dict, before_dispatch=None):
    from openprogram.agent import surface_context
    from ..web_use_runtime import get_registry

    registry = get_registry()
    listed = registry.list_pages(context=context, owner_id=owner_id)
    pages = listed.get("pages") if isinstance(listed, dict) else None
    token = ""
    if isinstance(pages, list) and pages and isinstance(pages[0], dict):
        token = str(pages[0].get("page_context_token") or "")
    if not token:
        surface_context.release_bindings(context)
        return {
            "ok": False,
            "reason_code": "desktop_unavailable",
            "error": "desktop app opened a tab but no Page token was issued",
        }
    observed = registry.execute(
        command="observe",
        backend=backend,
        owner_id=owner_id,
        page_context_token=token,
        page_context=context,
        arguments=arguments,
        **({"before_dispatch": before_dispatch} if before_dispatch is not None else {}),
    )
    if isinstance(observed, dict):
        observed = dict(observed)
        observed.pop("page_context_token", None)
    if state._opened_observation_is_live(observed):
        return observed
    surface_context.release_bindings(context)
    if not isinstance(observed, dict):
        return {
            "ok": False,
            "reason_code": "desktop_unavailable",
            "error": "desktop app opened a tab but the Page could not be observed",
        }
    if observed.get("ok") is not False:
        observed["ok"] = False
    return observed


def _recover_web_use_page(failure: dict, *, backend: str):
    from openprogram.agent import surface_context
    from ..web_use_runtime import get_registry
    from openprogram.agent.run_control import check_cancelled

    check_cancelled()
    owner_id = surface_context.web_use_owner_id(surface_context.current())
    tab_id, window_id = failure.get("recovery_tab_id"), failure.get("recovery_window_id")
    if tab_id and window_id:
        context = surface_context.capture_pages()
        if not isinstance(context, dict) or not context.get("context_id"):
            return failure
        try:
            check_cancelled()
        except BaseException:
            surface_context.release_bindings(context)
            raise
        registry = get_registry()
        listed = registry.list_pages(context=context, owner_id=owner_id)
        if not listed.get("ok"):
            surface_context.release_bindings(context)
            return failure
        pages = listed.get("pages") or []
        target = next((page for page in pages if page.get("tab_id") == tab_id and page.get("window_id") == window_id), None)
        unused = [page["page_context_token"] for page in pages if page is not target]
        registry.release_page_capabilities(unused, owner_id=owner_id)
        if target:
            try:
                return registry.execute(command="observe", backend=backend, owner_id=owner_id,
                                        page_context_token=target["page_context_token"], arguments={},
                                        before_dispatch=check_cancelled)
            finally:
                registry.release_page_capabilities([target["page_context_token"]], owner_id=owner_id)
        surface_context.release_bindings(context)
    check_cancelled()
    context = surface_context.open_page(failure["recovery_url"], **({"window_id": window_id} if window_id else {}))
    if "surfaces" not in context:
        return state._open_page_error(context)
    try:
        check_cancelled()
        return state._start_session_on_opened_page(context=context, owner_id=owner_id, backend=backend, arguments={}, before_dispatch=check_cancelled)
    except BaseException:
        surface_context.release_bindings(context)
        raise
