"""Bind an existing owned WebUseSession to the isolated GUI interpreter."""
from __future__ import annotations

from openprogram.programs import ToolReturn

_FIELDS = {
    "observe": set(), "screenshot": set(),
    "navigate": {"url"}, "click": {"ref", "x", "y"},
    "type": {"ref", "text"}, "press": {"ref", "key"},
    "hover": {"ref"}, "scroll": {"amount"}, "select": {"ref", "value"},
    "verify": {"assertion", "value"},
}


def register_browser_page(broker, registry, *, owner_id: str, web_session_id: str) -> str:
    """Host-only binding; the caller retains the existing session's lifetime.

    No current-page fallback, new Page, implicit observation token or child
    supplied ownership field is accepted. Registry/controller checks still apply.
    """
    if not owner_id.strip() or not web_session_id.strip() or web_session_id.strip().lower() == "pending":
        raise ValueError("browser binding requires exact owner and session")

    def validate(method, arguments, observation):
        if method not in _FIELDS or set(arguments) - _FIELDS[method]:
            raise ValueError("unsupported browser method or arguments")
        if method != "observe" and (not isinstance(observation, str) or not observation.strip()):
            raise ValueError("browser operation requires explicit observation")

    def invoke(method, arguments, observation, context):
        context.check()
        command = method if method in {"observe", "verify"} else "act"
        params = dict(arguments)
        if method != "observe":
            params["expected_frame_id"] = observation
        if command == "act":
            params["action"] = method
        result = registry.execute(command=command, owner_id=owner_id, web_session_id=web_session_id,
                                  arguments=params, before_dispatch=context.check)
        context.check()
        metadata = result.json_data if isinstance(result, ToolReturn) else result
        if isinstance(result, ToolReturn) and result.is_error:
            return result
        if isinstance(metadata, dict) and metadata.get("ok") is False:
            reason = metadata.get("reason_code", "browser operation failed") if isinstance(metadata, dict) else "browser operation failed"
            if isinstance(result, ToolReturn):
                return ToolReturn(text=result.text or reason, images=result.images, json_data=metadata, is_error=True)
            raise RuntimeError(reason)
        if not isinstance(metadata, dict):
            raise RuntimeError("browser operation returned no structured observation")
        if method in {"observe", "screenshot"}:
            frame = metadata.get("frame_id")
            if not isinstance(frame, str) or not frame.strip():
                raise RuntimeError("browser observation missing frame")
            if method == "screenshot" and frame != observation:
                raise RuntimeError("browser screenshot frame mismatch")
        return result

    methods = {method: (lambda args, observation, context, method=method:
                        invoke(method, args, observation, context)) for method in _FIELDS}
    return broker.register(target=f"browser:{web_session_id}", methods=methods, validate=validate)
