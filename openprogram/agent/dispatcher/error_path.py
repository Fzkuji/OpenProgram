"""Turn error path — the dispatcher's except branch (dispatcher-split).

Two paths, both delegated to _turn_lifecycle:
  * placeholder present → fold the error into the same row so the chat
    UI shows a red assistant bubble (not an orphan system message next
    to an empty bubble).
  * placeholder missing → standalone system error node.

Head movement stays with the TurnWriter (``record_failure``) — the one
object allowed to move this turn's head. The failed turn is finalized
(context commit, git commit, snapshot eviction) so the git timeline has
no hole where something went wrong, the failure is classified into the
structured error taxonomy, and the error ``TurnResult`` is built.
"""
from __future__ import annotations

import logging
import time
from typing import Optional, TYPE_CHECKING

from openprogram.agent.dispatcher.types import TurnResult
from openprogram.agent.dispatcher.finalize import finalize_error_turn
from openprogram.agent.internals._turn_lifecycle import (
    fold_error_into_placeholder as _fold_error_into_placeholder,
    write_standalone_error_node as _write_standalone_error_node,
)

if TYPE_CHECKING:
    from openprogram.agent.dispatcher.types import EventCallback, TurnRequest
    from openprogram.agent.dispatcher.turn_writer import TurnWriter

_log = logging.getLogger(__name__)


def handle_turn_error(
    *,
    db,
    req: "TurnRequest",
    session: dict,
    exc: BaseException,
    writer: "TurnWriter",
    user_msg_id: str,
    assistant_msg_id: str,
    placeholder_inserted: bool,
    project_baseline,
    on_event: "EventCallback",
    started_at: float,
) -> TurnResult:
    """Fold/record the error, finalize the failed turn, build the result."""
    e = exc
    from openprogram.providers.structured_output import (
        StructuredOutputError,
        StructuredOutputGenerationError,
        StructuredOutputUnsupportedError,
    )
    structured_error = e if isinstance(e, StructuredOutputError) else None
    structured_attempts = None
    if structured_error is not None:
        if isinstance(structured_error, StructuredOutputUnsupportedError):
            structured_attempts = 0
        elif isinstance(structured_error, StructuredOutputGenerationError):
            structured_attempts = 1
        else:
            structured_attempts = (
                getattr(req.response_format, "max_validation_retries", 0) + 1
            )
    head_for_next: Optional[str] = None
    err_text: Optional[str] = None
    if placeholder_inserted:
        err_text = _fold_error_into_placeholder(
            req.session_id, assistant_msg_id, e,
        )
        if err_text is not None:
            head_for_next = assistant_msg_id
    if err_text is None:
        err_id = _write_standalone_error_node(
            db, req.session_id, user_msg_id, req.source, e,
        )
        err_text = f"[error] {type(e).__name__}: {e}"
        head_for_next = err_id
    # Move head to the failed turn so the next user message
    # chains off it, not off the user message that triggered it —
    # on an advancing turn only (turn_writer.py).
    writer.record_failure(head_for_next)
    # An error is a terminal state, not a missing one: finalize the turn
    # so the error node gets the same bookkeeping (context commit, git
    # commit, snapshot eviction) a successful turn gets. Without this the
    # git timeline has a hole exactly where something went wrong, and a
    # retry forks from a predecessor whose commit was never written.
    finalize_error_turn(
        db=db,
        req=req,
        session=session,
        assistant_msg_id=head_for_next or assistant_msg_id,
        _project_baseline=project_baseline,
        on_event=on_event,
        error_text=err_text,
    )
    # Classify the failure into the structured taxonomy (an LLMError
    # carries its own reason; anything else is classified) so the webui can
    # render a retryable rate-limit differently from a fatal auth/context
    # failure. See docs/design/providers/reliability/error-taxonomy-propagation.md.
    try:
        from openprogram.providers.utils.errors import taxonomy_fields
        _e_reason, _e_retryable, _e_retry_after = taxonomy_fields(e)
    except Exception:
        _log.debug("error taxonomy classification failed", exc_info=True)
        _e_reason = _e_retryable = _e_retry_after = None
    on_event({"type": "chat_response",
              "data": {"type": "error", "session_id": req.session_id,
                       "msg_id": user_msg_id,
                       "content": err_text, "reason": _e_reason,
                       "retryable": _e_retryable,
                       "retry_after_s": _e_retry_after}})
    return TurnResult(
        final_text="",
        user_msg_id=user_msg_id,
        assistant_msg_id=(
            assistant_msg_id if placeholder_inserted else ""),
        failed=True,
        error=str(e),
        error_reason=_e_reason,
        error_retryable=_e_retryable,
        error_retry_after_s=_e_retry_after,
        duration_ms=int((time.time() - started_at) * 1000),
        structured_error_code=(structured_error.code if structured_error else None),
        structured_output_attempts=structured_attempts,
        structured_output_issues=(structured_error.issues if structured_error else []),
    )
