"""A provider's credentials: list, add, rename, turn off, remove, check,
replace, reorder, and the strategy that picks among them; and a
conversation's pin.

Routes (``{p}`` a provider id, ``{cid}`` a credential id):

  GET    /api/providers/{p}/credentials                -> list (shape below)
  POST   /api/providers/{p}/credentials                {api_key, label?, validate?}
  POST   /api/providers/{p}/credentials/check-key      {api_key, replace?} -> {ok, check}
  PATCH  /api/providers/{p}/credentials/{cid}          {label?, enabled?}
  DELETE /api/providers/{p}/credentials/{cid}
  POST   /api/providers/{p}/credentials/{cid}/check
  POST   /api/providers/{p}/credentials/{cid}/replace  {api_key}
  POST   /api/providers/{p}/credentials/reorder        {order: [cid, ...]}
  POST   /api/providers/{p}/credentials/strategy       {strategy}
  GET    /api/sessions/{sid}/credentials               -> {binding, pin}
  POST   /api/sessions/{sid}/credentials               {provider, credential_id | null}

The list's shape and each change are :mod:`openprogram.models.listing.manage`,
which ``openprogram providers keys`` uses too. Sign-ins (OAuth, device code,
an imported login, a pasted token) are added through
``/api/providers/{p}/login/*`` (``provider_login.py``); signing in again with
the same account renews its row. Every change is announced to open tabs as
``provider.credentials.changed`` (``runtime/events/bridges.py``). No response
ever carries a secret: a key shows as its last four characters. The add
dialog checks a pasted key with ``check-key`` (nothing is stored) and stores
it only when Add key or Replace key is pressed; that request reuses the check.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from fastapi import Body
from fastapi.responses import JSONResponse

from openprogram.models.listing import manage
from openprogram.models.auth.credentials import is_redacted_value

from .credential_secrets import check_request_body, is_nonempty_printable_ascii

_log = logging.getLogger(__name__)


def _error(message: str, status: int = 400, **extra: Any) -> JSONResponse:
    return JSONResponse(content={"error": message, **extra}, status_code=status)


def _answer(run: Callable[[], Any]) -> JSONResponse:
    try:
        result = run()
    except manage.CredentialEditError as exc:
        return _error(str(exc), exc.status, **exc.extra)
    return JSONResponse(content=result if result is not None else {"ok": True})


def _settings_changed() -> None:
    """Tell open tabs to reload enabled models (a provider or models were turned on)."""
    try:
        from openprogram.runtime.events import emit_ws_frame
        emit_ws_frame({"type": "agent_settings_changed", "data": {}})
    except Exception:
        _log.debug("settings change not broadcast", exc_info=True)


def _valid_key(value: Any) -> bool:
    return (is_nonempty_printable_ascii(value) and bool(value.strip())
            and "sk-ant-oat" not in value and not is_redacted_value(value))


def register(app):
    @app.get("/api/providers/{provider}/credentials")
    def api_credentials(provider: str):
        return JSONResponse(content=manage.provider_credentials(provider))

    @app.post("/api/providers/{provider}/credentials")
    def api_credentials_add(provider: str, body: Any = Body(default=None)):
        # One of the three endpoints that carry a secret, so the field set is
        # checked here rather than by the credential-rejecting helper.
        if not isinstance(body, dict) or "api_key" not in body \
                or not set(body) <= {"api_key", "label", "validate"}:
            return _error("body must be {api_key, label?, validate?}")
        key, label, validate = body["api_key"], body.get("label", ""), body.get("validate", True)
        if not _valid_key(key) or not isinstance(label, str) or not isinstance(validate, bool):
            return _error("invalid key body")
        def _add() -> dict:
            added = manage.add_api_key(provider, key.strip(), label=label.strip(), validate=validate)
            check = added.get("check") or {}
            if check.get("auto_enabled") or check.get("enabled_models"):
                _settings_changed()
            return added

        return _answer(_add)

    @app.post("/api/providers/{provider}/credentials/check-key")
    def api_credentials_check_key(provider: str, body: Any = Body(default=None)):
        # Carries a secret: the exact field set is checked here.
        if not isinstance(body, dict) or "api_key" not in body \
                or not set(body) <= {"api_key", "replace"} or not _valid_key(body["api_key"]):
            return _error("body must be {api_key, replace?}")
        replace = body.get("replace")
        if replace is not None and not (isinstance(replace, str) and replace):
            return _error("replace must be a credential id")
        return _answer(lambda: manage.check_key(provider, body["api_key"].strip(), replace=replace))

    @app.patch("/api/providers/{provider}/credentials/{cid}")
    def api_credentials_update(provider: str, cid: str, body: Any = Body(default=None)):
        error = check_request_body(body, allowed={"label", "enabled"})
        if error is not None:
            return _error(error)
        enabled = body.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            return _error("enabled must be true or false")
        return _answer(lambda: {"ok": True, "credential": manage.edit(
            provider, cid, label=body.get("label"), enabled=enabled)})

    @app.delete("/api/providers/{provider}/credentials/{cid}")
    def api_credentials_remove(provider: str, cid: str):
        return _answer(lambda: manage.remove(provider, cid))

    @app.post("/api/providers/{provider}/credentials/{cid}/check")
    def api_credentials_check(provider: str, cid: str):
        return _answer(lambda: manage.check(provider, cid))

    @app.post("/api/providers/{provider}/credentials/{cid}/replace")
    def api_credentials_replace(provider: str, cid: str, body: Any = Body(default=None)):
        if not isinstance(body, dict) or set(body) != {"api_key"} or not _valid_key(body["api_key"]):
            return _error("body must be {api_key}")
        return _answer(lambda: manage.replace(provider, cid, body["api_key"].strip()))

    @app.post("/api/providers/{provider}/credentials/reorder")
    def api_credentials_reorder(provider: str, body: Any = Body(default=None)):
        error = check_request_body(body, allowed={"order"}, required={"order"})
        if error is not None:
            return _error(error)
        return _answer(lambda: manage.reorder(provider, body["order"]))

    @app.post("/api/providers/{provider}/credentials/strategy")
    def api_credentials_strategy(provider: str, body: Any = Body(default=None)):
        error = check_request_body(body, allowed={"strategy"}, required={"strategy"})
        if error is not None:
            return _error(error)
        return _answer(lambda: manage.set_strategy(provider, body["strategy"]))

    @app.get("/api/sessions/{session_id}/credentials")
    def api_session_credentials(session_id: str):
        from openprogram.models.auth.selection import session_credentials
        return JSONResponse(content=session_credentials(session_id))

    @app.post("/api/sessions/{session_id}/credentials")
    def api_session_pin(session_id: str, body: Any = Body(default=None)):
        """Pin the conversation to one credential of a provider, or set it
        back to automatic with ``credential_id: null``."""
        # A credential id names a row, not a secret, so the exact field set
        # is checked here rather than by the credential-rejecting helper.
        if not isinstance(body, dict) or set(body) != {"provider", "credential_id"}:
            return _error("body must be {provider, credential_id}")
        provider, cid = body["provider"], body["credential_id"]
        if not is_nonempty_printable_ascii(provider) or not (cid is None or is_nonempty_printable_ascii(cid)):
            return _error("invalid pin body")
        from openprogram.models.auth.selection import session_credentials, set_pin
        from openprogram.models.auth.types import AuthConfigError
        try:
            saved = set_pin(session_id, provider, cid)
        except AuthConfigError as exc:
            return _error(str(exc), 404)
        if not saved:
            return _error("no such conversation", 404)
        try:
            from openprogram.runtime.events import emit_ws_frame
            emit_ws_frame({"type": "session.credentials.changed",
                           "data": {"session_id": session_id, **session_credentials(session_id)}})
        except Exception:
            _log.debug("pin change not sent to tabs", exc_info=True)
        return JSONResponse(content=session_credentials(session_id))


__all__ = ["register"]
