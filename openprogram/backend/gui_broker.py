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
import threading
import time
from types import MappingProxyType
from typing import Callable, Mapping
import uuid

from openprogram.execution.attempts import AttemptStore, AttemptStatus
from openprogram.execution.effects import EffectClassification, EffectStatus, EffectStore
from openprogram.execution.model import ExecutionStatus
from openprogram.execution.state_blobs import ExecutionStateBlobStore
from openprogram.execution.store import MAX_AGENT_STATE_BLOB_BYTES
from openprogram.programs import ToolReturn


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


class GuiBroker:
    def __init__(self, *, effects: EffectStore, execution_id: str, attempt_id: str,
                 generation: int, invocation_id: str):
        if not all((execution_id, attempt_id, invocation_id)) or generation < 1:
            raise ValueError("GUI broker requires host execution and invocation identity")
        self._owner_context = copy_context()
        self._effects = effects
        self._execution_id = execution_id
        self._attempt_id = attempt_id
        self._generation = generation
        self._invocation_id = invocation_id
        self._resources = {}
        self._lock = threading.RLock()
        self._revoked = threading.Event()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gui-broker")

    def register(self, *, target: str, methods: Mapping[str, Callable], validate: Callable) -> str:
        """Host-only: bind one exact target and its dynamic grant/frame validator."""
        if not target or not methods or not callable(validate) or not all(
            isinstance(name, str) and callable(fn) for name, fn in methods.items()
        ):
            raise ValueError("invalid trusted GUI resource")
        with self._lock:
            if self._revoked.is_set():
                raise PermissionError("GUI broker revoked")
            handle = uuid.uuid4().hex
            self._resources[handle] = _Resource(target, MappingProxyType(dict(methods)), validate)
            return handle

    def revoke_resource(self, handle: str):
        with self._lock:
            self._resources.pop(handle, None)

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
            # A validator can wait; re-read durable admission after it returns.
            execution = self._effects.executions.get_execution(self._execution_id)
            attempt = AttemptStore(self._effects.executions).get(self._attempt_id)
            if (execution is None or execution.status is not ExecutionStatus.RUNNING
                    or execution.current_attempt_id != self._attempt_id
                    or execution.owner_lease.get("generation") != self._generation
                    or attempt is None or attempt.execution_id != self._execution_id
                    or attempt.generation != self._generation or attempt.status is not AttemptStatus.ACTIVE
                    or attempt.lease_expires_at <= time.time()):
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
        try:
            check()
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
                blobs = ExecutionStateBlobStore(self._effects.executions)
                for index, image in enumerate(images):
                    chunks = []
                    for offset in range(0, len(image), MAX_AGENT_STATE_BLOB_BYTES):
                        check()
                        name = f"gui_image_{index}_{offset}"
                        blob = blobs.put(execution_id=self._execution_id, attempt_id=self._attempt_id,
                                         name=name, payload=image[offset:offset + MAX_AGENT_STATE_BLOB_BYTES],
                                         media_type="application/octet-stream", schema_version=1)
                        blobs.attach_ref(execution_id=self._execution_id, ref=blob.ref, name=name,
                                         reference_kind="effect", reference_id=effect.effect_id)
                        chunks.append(blob.ref)
                    image_records.append({"mime_type": "image/png", "byte_length": len(image),
                                          "sha256": hashlib.sha256(image).hexdigest(), "chunks": chunks})
            # The adapter receipt, never a child message, resolves this effect.
            receipt = {"state": "applied", "target": resource.target, "method": method, "value": value}
            if image_records:
                receipt["images"] = image_records
            receipt = json.loads(json.dumps(receipt, allow_nan=False))
            if adapter_error is not None:
                diagnostic = {**receipt, "state": "uncertain"}
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
        except BaseException:
            # If persistence itself fails, DISPATCHED already denotes unresolved
            # work; preserve that record and the original exception.
            try:
                self._effects.mark_uncertain(effect.effect_id, expected_status=EffectStatus.DISPATCHED,
                                             receipt=diagnostic)
            except Exception:
                pass
            raise
