"""One real Agent turn on a candidate, run by the candidate's own worker.

The switch controller asks for it through the worker's owner route, because a
turn proves the agent loop, tool dispatch and persistence of the new code in
the process that will serve them. It runs in a scratch conversation with the
owner's default model and reads one file.

A provider or network error says nothing about the candidate, and neither
does a machine where no model has been chosen yet, so both are reported as
inconclusive and the switch goes on. Anything else that goes wrong is the
candidate's own failure, and the switch rolls back.
"""
from __future__ import annotations

import logging
import uuid

from ..store import SelfUpdateStore
from ..types import UpdatePhase
from ..versions.layout import current_runtime

SOURCE = "self_update_turn_check"
_PROMPT = "Use the read tool once on {path}, then reply with only the word DONE."
_INCONCLUSIVE_REASONS = ("provider", "rate_limit", "network", "auth", "context_overflow", "billing", "model")
_log = logging.getLogger(__name__)


def _path_to_read() -> str:
    runtime = current_runtime()
    if runtime is not None:
        return str(runtime.layout.version(runtime.version) / "version.json")
    import openprogram

    return str(openprogram.__file__)


def _classify(result) -> dict:
    if not result.failed:
        return {"outcome": "ok" if result.tool_calls else "inconclusive"}
    from openprogram.runtime.conversation.model_tools import NO_MODEL_CONFIGURED

    reason = (result.error_reason or "").lower()
    if (
        result.error_retryable
        or any(word in reason for word in _INCONCLUSIVE_REASONS)
        or NO_MODEL_CONFIGURED in (result.error or "")
    ):
        return {"outcome": "inconclusive", "error": (result.error or "")[:500]}
    return {"outcome": "failed", "error": (result.error or result.error_reason or "the turn failed")[:500]}


def run_turn_check(update_id: str, *, store: SelfUpdateStore | None = None) -> dict:
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.runtime.context.session_db import default_db
    from openprogram.runtime.conversation.turn_runtime import process_user_turn
    from openprogram.runtime.permissions.authority import owner_principal_id

    target = store or SelfUpdateStore()
    record = target.load(update_id)
    if record.state.phase is not UpdatePhase.VERIFYING:
        raise ValueError("a turn check needs an update in verifying")
    session_id = f"self-update-check-{uuid.uuid4().hex[:12]}"
    request = TurnRequest(
        session_id=session_id, user_text=_PROMPT.format(path=_path_to_read()), agent_id=record.request.agent_id,
        source=SOURCE, permission_mode="bypass", tools_override=["read"], speaker_kind="owner",
        principal_id=owner_principal_id(), authority_tier="owner", interaction="background",
    )
    try:
        return _classify(process_user_turn(request))
    except Exception as exc:  # noqa: BLE001 - a crash in the loop is the verdict.
        _log.exception("turn check crashed")
        return {"outcome": "failed", "error": f"{type(exc).__name__}: {exc}"[:500]}
    finally:
        try:
            default_db().delete_session(session_id)
        except Exception:  # noqa: BLE001 - a leftover scratch conversation is harmless.
            _log.warning("turn check conversation not removed", exc_info=True)


__all__ = ["SOURCE", "run_turn_check"]
