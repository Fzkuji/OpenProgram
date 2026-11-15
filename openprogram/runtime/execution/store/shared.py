"""Shared helpers of the canonical execution and control-command records."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from ..agent_input_budget import (
    AGENT_TURN_INPUT_MAX_BYTES,
    AgentInputBudgetError,
    budget_payload,
)
from ..model import (
    _json,
)

if TYPE_CHECKING:
    from .store import ExecutionStore


_log = logging.getLogger(__name__)


class ExecutionStoreError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class ExecutionConflict(ExecutionStoreError):
    pass


class CommandConflict(ExecutionStoreError):
    pass


_AGENT_TURN_INPUT_VERSION = 1
_AGENT_TURN_INPUT_MAX_BYTES = AGENT_TURN_INPUT_MAX_BYTES
_AGENT_TURN_INPUT_KINDS = frozenset({"chat", "forced_tool"})
_AGENT_TURN_INPUT_KEYS = frozenset({
    "version", "kind", "request", "tool_name", "tool_input",
    "anchor_msg_id", "work_dir", "agent_id", "source", "provider", "model",
    "response_format", "surface_context_snapshot",
})
_STATE_REF_PREFIX = "execstate://sha256/"
_STATE_HASH_LENGTH = 64
# Preferred chunk size for producers such as the GUI broker, not a storage cap.
MAX_AGENT_STATE_BLOB_BYTES = 1024 * 1024
RESOURCE_INTENT_KINDS = frozenset({
    "execution.admission.intent", "resource.admission.intent",
    "execution.claim.intent", "resource.claim.intent",
    "execution.release.intent", "resource.release.intent",
})


def _validate_agent_turn_payload(payload: Mapping[str, Any]) -> None:
    """Validate the durable Agent envelope without importing Agent runtime."""
    if not isinstance(payload, Mapping):
        raise ExecutionConflict("invalid_agent_input", "Agent turn input must be an object")
    if payload.get("version") != _AGENT_TURN_INPUT_VERSION:
        raise ExecutionConflict("invalid_agent_input_version", "unsupported Agent turn input version")
    kind = payload.get("kind")
    if kind not in _AGENT_TURN_INPUT_KINDS:
        raise ExecutionConflict("invalid_agent_input_kind", "Agent turn input kind must be chat or forced_tool")
    if set(payload) - _AGENT_TURN_INPUT_KEYS:
        raise ExecutionConflict("invalid_agent_input", "Agent turn input has unknown fields")
    if kind == "chat":
        request = payload.get("request")
        if not isinstance(request, Mapping):
            raise ExecutionConflict("invalid_agent_input", "chat input requires a request object")
        for required in ("user_text", "agent_id", "source"):
            if not isinstance(request.get(required), str) or not request[required]:
                raise ExecutionConflict("invalid_agent_input", f"chat input requires {required}")
    else:
        if not isinstance(payload.get("tool_name"), str) or not payload["tool_name"]:
            raise ExecutionConflict("invalid_agent_input", "forced_tool input requires tool_name")
        if not isinstance(payload.get("tool_input", {}), Mapping):
            raise ExecutionConflict("invalid_agent_input", "forced_tool input requires an object tool_input")
    try:
        encoded = _json(budget_payload(payload))
    except AgentInputBudgetError as exc:
        raise ExecutionConflict(exc.code, str(exc)) from exc
    except (TypeError, ValueError) as exc:
        raise ExecutionConflict("invalid_agent_input", "Agent turn input must be JSON serializable") from exc
    if len(encoded.encode("utf-8")) > _AGENT_TURN_INPUT_MAX_BYTES:
        raise ExecutionConflict("agent_input_too_large", "Agent turn input exceeds the size limit")


def _validate_job_agent_payload(payload: Mapping[str, Any]) -> None:
    try:
        from ..job_input import normalize_job_agent_input
        normalize_job_agent_input(payload)
    except ValueError as exc:
        raise ExecutionConflict("invalid_job_agent_input", str(exc)) from exc


def _object(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("stored JSON value must be an object")
    return value


def _fingerprint(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


_event_written: ContextVar[bool] = ContextVar(
    "execution_event_written", default=False
)




_store_construction_lock = threading.RLock()


@lru_cache(maxsize=8)
def _construct_store(path: Path) -> ExecutionStore:
    from .store import ExecutionStore
    return ExecutionStore(path)


def _store_for_path(path: Path) -> ExecutionStore:
    with _store_construction_lock:
        return _construct_store(path)


_store_for_path.cache_clear = _construct_store.cache_clear  # type: ignore[attr-defined]


def default_store() -> ExecutionStore:
    """Return the store for the currently active profile."""
    from openprogram.state.paths import get_run_state_path

    return _store_for_path(get_run_state_path())
