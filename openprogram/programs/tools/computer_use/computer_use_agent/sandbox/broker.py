"""Host-owned GUI primitive admission and durable receipts.

Only trusted adapters are registered here. Generated Python receives opaque
resource names and can neither register adapters nor choose execution identity.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass
import json
import hashlib
import logging
import os
import threading
import time
from types import MappingProxyType, SimpleNamespace
from typing import Callable, Mapping
import uuid

from openprogram.runtime.execution.attempts import AttemptStore, AttemptStatus
from openprogram.runtime.execution.effects import EffectClassification, EffectConflict, EffectStatus, EffectStore
from openprogram.runtime.execution.model import ExecutionStatus
from openprogram.runtime.execution.state_blobs import ExecutionStateBlobStore
from openprogram.runtime.execution.store import MAX_AGENT_STATE_BLOB_BYTES
from openprogram.programs import ToolReturn

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class GuiOperationContext:
    """Trusted adapters call check immediately before a primitive and after waits."""
    check: Callable[[], None]
    deadline: float


@dataclass(frozen=True)
class _Resource:
    target: str
    methods: Mapping[str, Callable]
    validate: Callable
    # (method, arguments) -> True for a primitive that only reads: its failure
    # applied nothing, so it resolves not committed instead of uncertain.
    reads: Callable[[str, dict], bool]


class _RunTableSteps:
    """A chat turn's GUI steps: recorded in the run tables so recovery can see them."""

    def __init__(self, effects: EffectStore):
        self._effects = effects

    def admission_open(self, execution_id: str, attempt_id: str, generation: int) -> bool:
        execution = self._effects.executions.get_execution(execution_id)
        attempt = AttemptStore(self._effects.executions).get(attempt_id)
        return not (execution is None or execution.status is not ExecutionStatus.RUNNING
                    or execution.current_attempt_id != attempt_id
                    or execution.owner_lease.get("generation") != generation
                    or attempt is None or attempt.execution_id != execution_id
                    or attempt.generation != generation or attempt.status is not AttemptStatus.ACTIVE)

    def open_steps(self, execution_id: str, invocation_id: str):
        from contextlib import closing

        def is_step_of_invocation(effect):
            metadata = json.loads(effect["metadata_json"])
            return (isinstance(metadata, dict) and metadata.get("kind") == "gui.primitive"
                    and metadata.get("invocation_id") == invocation_id)

        with closing(self._effects.executions._connect()) as connection:
            return connection.rows("effects", execution_id=execution_id, status=("dispatched", "uncertain"),
                                   where=is_step_of_invocation)

    def save_images(self, execution_id: str, attempt_id: str, effect_id: str, images, check) -> list[dict]:
        blobs = ExecutionStateBlobStore(self._effects.executions)
        records = []
        for index, image in enumerate(images):
            chunks = []
            for offset in range(0, len(image), MAX_AGENT_STATE_BLOB_BYTES):
                check()
                name = f"gui_image_{index}_{offset}"
                blob = blobs.put(execution_id=execution_id, attempt_id=attempt_id,
                                 name=name, payload=image[offset:offset + MAX_AGENT_STATE_BLOB_BYTES],
                                 media_type="application/octet-stream", schema_version=1)
                blobs.attach_ref(execution_id=execution_id, ref=blob.ref, name=name,
                                 reference_kind="effect", reference_id=effect_id)
                chunks.append(blob.ref)
            records.append({"mime_type": "image/png", "byte_length": len(image),
                            "sha256": hashlib.sha256(image).hexdigest(), "chunks": chunks})
        return records


class SubprocessGuiSteps:
    """A function subprocess's GUI steps, which nothing needs to recover.

    If the subprocess dies, its parent reports one tool error and the model
    reads the screen. The caller already gets each receipt as the step's result.
    Used in place of the ``EffectStore``.
    """

    def __init__(self):
        self._parent = os.getppid()

    def admission_open(self, execution_id: str, attempt_id: str, generation: int) -> bool:
        return os.getppid() == self._parent

    def open_steps(self, execution_id: str, invocation_id: str):
        return ()

    def save_images(self, execution_id: str, attempt_id: str, effect_id: str, images, check) -> list[dict]:
        return [{"mime_type": "image/png", "byte_length": len(image),
                 "sha256": hashlib.sha256(image).hexdigest()} for image in images]

    def register(self, *, effect_id: str, **_details):
        return SimpleNamespace(effect_id=effect_id)

    def mark_dispatched(self, *_args, **_details) -> None:
        return None

    def mark_uncertain(self, *_args, **_details) -> None:
        return None

    def resolve(self, *_args, **_details) -> None:
        return None


class GuiBroker:
    def __init__(self, *, effects: EffectStore | SubprocessGuiSteps, execution_id: str, attempt_id: str,
                 generation: int, invocation_id: str):
        if not all((execution_id, attempt_id, invocation_id)) or generation < 1:
            raise ValueError("GUI broker requires host execution and invocation identity")
        self._owner_context = copy_context()
        self._effects = effects
        self._steps = effects if isinstance(effects, SubprocessGuiSteps) else _RunTableSteps(effects)
        self._execution_id = execution_id
        self._attempt_id = attempt_id
        self._generation = generation
        self._invocation_id = invocation_id
        self._resources = {}
        self._lock = threading.RLock()
        self._revoked = threading.Event()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gui-broker")

    def register(self, *, target: str, methods: Mapping[str, Callable], validate: Callable,
                 reads: Callable[[str, dict], bool] | None = None) -> str:
        """Host-only: bind one exact target and its dynamic grant/frame validator."""
        if not target or not methods or not callable(validate) or not all(
            isinstance(name, str) and callable(fn) for name, fn in methods.items()
        ):
            raise ValueError("invalid trusted GUI resource")
        with self._lock:
            if self._revoked.is_set():
                raise PermissionError("GUI broker revoked")
            handle = uuid.uuid4().hex
            self._resources[handle] = _Resource(target, MappingProxyType(dict(methods)), validate,
                                                reads or (lambda _method, _arguments: False))
            return handle

    def bind_invocation(self, invocation_id: str):
        """Host-only: the tool call whose primitives the next receipts belong to."""
        if not invocation_id:
            raise ValueError("GUI broker requires an invocation identity")
        with self._lock:
            self._invocation_id = invocation_id

    def revoke_resource(self, handle: str):
        with self._lock:
            self._resources.pop(handle, None)

    def settle(self) -> int:
        """Close the steps of the bound call that are still open as it ends; return how many.

        A step that failed after it started, or that the call's deadline cut
        off, has no receipt of its own. The call's result reports it to the
        model, which reads the screen before repeating it, so the record is
        closed with what is known. Left open, it would stop the turn at its
        next checkpoint and wait for a person to resolve what the screen
        already shows. Only the attempt that owns the call can close them.
        """
        with self._lock:
            invocation = self._invocation_id
        rows = self._steps.open_steps(self._execution_id, invocation)
        closed = 0
        for row in rows:
            try:
                known = json.loads(row["receipt_json"]) if row["receipt_json"] else {}
            except ValueError:
                known = {}
            try:
                self._effects.resolve(
                    row["effect_id"], expected_status=EffectStatus(row["status"]),
                    outcome=EffectStatus.COMMITTED,
                    receipt={**(known if isinstance(known, dict) else {}), "state": "unknown",
                             "reported_by": "call_result"},
                    attempt_id=self._attempt_id, generation=self._generation,
                )
                closed += 1
            except Exception:
                _log.warning("failed to settle GUI effect effect_id=%s", row["effect_id"], exc_info=True)
        return closed

    def revoke(self):
        # Close admission before cancellation/child teardown. Never wait for a
        # blocking adapter here; its dispatched effect remains recoverable.
        with self._lock:
            self._revoked.set()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def submit(self, request: dict, *, deadline: float, cancel=None):
        if not isinstance(request, dict) or set(request) != {"resource", "method", "arguments", "observation"}:
            raise ValueError("invalid GUI operation fields")
        if (not isinstance(request["resource"], str) or not isinstance(request["method"], str)
                or not isinstance(request["arguments"], dict)
                or request["observation"] is not None and not isinstance(request["observation"], str)):
            raise ValueError("invalid GUI operation values")
        encoded = json.dumps(request, allow_nan=False)
        if len(encoded.encode()) > 64 * 1024:
            raise ValueError("GUI operation exceeds argument limit")
        # Copy across the host executor boundary; callers cannot mutate admission.
        request = json.loads(encoded)
        with self._lock:
            if self._revoked.is_set():
                raise PermissionError("GUI broker revoked")
            return self._executor.submit(self._owner_context.copy().run, self._dispatch, request, deadline, cancel)

    def _dispatch(self, request, deadline, cancel):
        handle, method = request["resource"], request["method"]
        with self._lock:
            resource = self._resources.get(handle)
        if resource is None or method not in resource.methods:
            raise PermissionError("unknown GUI resource or denied method")
        args, observation = request["arguments"], request["observation"]

        def check_admission():
            if self._revoked.is_set() or cancel is not None and cancel.is_set():
                raise PermissionError("GUI operation cancelled or revoked")
            if time.monotonic() >= deadline:
                raise TimeoutError("GUI operation deadline exceeded")
            with self._lock:
                if self._resources.get(handle) is not resource:
                    raise PermissionError("GUI resource revoked")

        def check():
            check_admission()
            resource.validate(method, args, observation)
            # A validator can wait; re-read admission after it returns.
            if not self._steps.admission_open(self._execution_id, self._attempt_id, self._generation):
                raise PermissionError("GUI execution attempt is stale or admission is closed")
            check_admission()

        check()
        action_id = "gui_" + uuid.uuid4().hex
        effect = self._effects.register(
            effect_id="effect_" + action_id, execution_id=self._execution_id,
            attempt_id=self._attempt_id, action_id=action_id,
            classification=EffectClassification.NONREPEATABLE, idempotency_key=None,
            metadata={"kind": "gui.primitive", "invocation_id": self._invocation_id,
                      "target": resource.target, **request},
        )
        check()
        self._effects.mark_dispatched(effect.effect_id, expected_status=EffectStatus.PLANNED)
        diagnostic = None
        started = False
        try:
            check()
            started = True
            value = resource.methods[method](args, observation, GuiOperationContext(check, deadline))
            check()
            images = ()
            adapter_error = None
            image_records = []
            if isinstance(value, ToolReturn):
                if value.is_error:
                    adapter_error = value.text or "GUI adapter failed"
                images = tuple(value.images)
                if len(images) > 4 or any(not isinstance(image, bytes) or len(image) > 4 * 1024 * 1024
                                          or not image.startswith(b"\x89PNG\r\n\x1a\n") for image in images):
                    raise ValueError("GUI images must be bounded PNG bytes, never paths or URLs")
                value = {"text": value.text, "json_data": value.json_data}
                image_records = self._steps.save_images(self._execution_id, self._attempt_id,
                                                        effect.effect_id, images, check)
            # The adapter receipt, never a child message, resolves this effect.
            receipt = {"state": "applied", "target": resource.target, "method": method, "value": value}
            if image_records:
                receipt["images"] = image_records
            receipt = json.loads(json.dumps(receipt, allow_nan=False))
            if adapter_error is not None:
                diagnostic = {**receipt, "state": "uncertain"}
                if _refused(value):
                    started = False
                raise RuntimeError(adapter_error)
            check()
            with self._lock:
                # Commit admission linearizes with revoke here, after receipt
                # materialization. No adapter/validator/SQLite work holds this
                # lock. An already-admitted receipt may finish persisting after
                # revoke; this records the completed primitive, not new authority.
                check_admission()
                admitted_receipt = receipt
            self._effects.resolve(
                effect.effect_id, expected_status=EffectStatus.DISPATCHED,
                outcome=EffectStatus.COMMITTED, receipt=admitted_receipt,
                attempt_id=self._attempt_id, generation=self._generation,
            )
            return {"effect_id": effect.effect_id, **receipt, **({"_images": images} if images else {})}
        except BaseException as exc:
            # If persistence itself fails, DISPATCHED already denotes unresolved
            # work; preserve that record and the original exception.
            try:
                if not started or _reads(resource, method, args):
                    # Nothing was applied: admission stopped it, or it only read.
                    self._effects.resolve(
                        effect.effect_id, expected_status=EffectStatus.DISPATCHED,
                        outcome=EffectStatus.NOT_COMMITTED,
                        receipt={**(diagnostic or {"target": resource.target, "method": method}),
                                 "state": "failed", "error": f"{type(exc).__name__}: {exc}"[:1000]},
                        attempt_id=self._attempt_id, generation=self._generation,
                    )
                else:
                    self._effects.mark_uncertain(effect.effect_id, expected_status=EffectStatus.DISPATCHED,
                                                 receipt=diagnostic)
            except EffectConflict:
                # The call ended first and settled this step with its own result.
                _log.debug("GUI effect already settled effect_id=%s", effect.effect_id, exc_info=True)
            except Exception:
                _log.warning("failed to resolve GUI effect effect_id=%s", effect.effect_id, exc_info=True)
            raise


def _refused(value) -> bool:
    """The adapter reports that the operation never reached its target."""
    data = value.get("json_data") if isinstance(value, dict) else None
    return isinstance(data, dict) and data.get("action_dispatched") is False


def _reads(resource: _Resource, method: str, arguments: dict) -> bool:
    try:
        return bool(resource.reads(method, arguments))
    except Exception:
        return False
