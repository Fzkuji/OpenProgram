"""Browser scheduling boundary tests; these do not claim real-page acceptance."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from types import SimpleNamespace
import threading
import time

import pytest

from openprogram.programs.workflow.browser import BrowserPageController
from openprogram.agentic_programming.function import CancelledError


def test_queued_browser_request_checks_cancellation_before_dispatch():
    controller = BrowserPageController(browser_api=object())
    gate, queued, cancelled = threading.Event(), threading.Event(), threading.Event()
    dispatched = []
    controller._execute = lambda *args, **kwargs: dispatched.append(1)
    def occupy():
        queued.set()
        assert gate.wait(3)
    blocker = controller._owner.submit(occupy)
    assert queued.wait(1)
    def check():
        if cancelled.is_set(): raise PermissionError("cancelled before browser dispatch")
    with ThreadPoolExecutor(max_workers=1) as caller:
        future = caller.submit(controller.execute, "observe", before_dispatch=check)
        deadline = time.monotonic() + 2
        while controller._owner._work_queue.qsize() == 0 and time.monotonic() < deadline:
            time.sleep(0.001)
        assert controller._owner._work_queue.qsize() == 1
        cancelled.set()
        gate.set()
        try:
            with pytest.raises(PermissionError, match="cancelled"):
                future.result(timeout=2)
        finally:
            controller._owner.shutdown(wait=True)
    assert dispatched == []


def test_ref_lookup_cancellation_is_checked_before_input():
    controller = BrowserPageController(browser_api=object())
    cancelled = threading.Event()
    writes = []
    controller._require_fresh = lambda frame: None
    controller._page = lambda: object()
    def lookup(ref):
        cancelled.set()
        return SimpleNamespace(fill=lambda text: writes.append(text)), ""
    controller._ref = lookup
    def check():
        if cancelled.is_set(): raise PermissionError("cancelled during lookup")
    try:
        with pytest.raises(PermissionError, match="cancelled"):
            controller.execute("type", ref="field", text="owned", before_dispatch=check)
    finally:
        controller._owner.shutdown(wait=True)
    assert writes == []


def test_page_executor_preserves_calling_authority_context():
    authority = ContextVar("page_guard_authority", default="default")
    controller = BrowserPageController(browser_api=object())
    controller._execute = lambda *args, **kwargs: authority.get()
    token = authority.set("restricted owner")
    try:
        assert controller.execute("observe") == "restricted owner"
    finally:
        authority.reset(token)
        controller._owner.shutdown(wait=True)


@pytest.mark.parametrize("error_type", [PermissionError, CancelledError])
def test_registry_passes_guard_to_actual_controller_and_cleans_cancelled_observe(error_type):
    from openprogram.programs.workflow.browser.web_use_runtime import ControllerBackend, WebUseSessionRegistry, SUPPORTED_BACKENDS
    controller = BrowserPageController(browser_api=object())
    cancelled = threading.Event()
    dispatched, released = [], []
    controller._execute = lambda *args, **kwargs: dispatched.append(1) or {"frame_id": "frame-1"}
    def validate(binding):
        cancelled.set()
        return {"ok": True}
    registry = WebUseSessionRegistry(
        adapters={name: ControllerBackend(name, lambda: controller) for name in SUPPORTED_BACKENDS},
        binding_validator=validate, binding_revision_resolver=lambda binding: {},
        page_key_resolver=lambda binding: binding, release_context=lambda context: released.append(context),
    )
    def check():
        if cancelled.is_set(): raise error_type("cancelled at registry validation")
    try:
        with pytest.raises(error_type, match="cancelled"):
            registry.execute(command="observe", owner_id="owner", binding_id="owned", before_dispatch=check)
        assert not registry._sessions and not registry._page_leases
        assert len(released) == 1
        assert dispatched == []
    finally:
        registry.close_all()
        controller._owner.shutdown(wait=True)


def test_registry_refuses_unguarded_backend_without_acquiring_page():
    from openprogram.programs.workflow.browser.web_use_runtime import WebUseSessionRegistry, SUPPORTED_BACKENDS
    calls = []
    backend = SimpleNamespace(observe=lambda *args: calls.append(1))
    registry = WebUseSessionRegistry(
        adapters={name: backend for name in SUPPORTED_BACKENDS},
        binding_validator=lambda binding: {"ok": True},
        binding_revision_resolver=lambda binding: {}, page_key_resolver=lambda binding: binding,
        release_context=lambda context: None,
    )
    result = registry.execute(command="observe", owner_id="owner", binding_id="owned", before_dispatch=lambda: None)
    assert result == {"ok": False, "reason_code": "guarded_dispatch_unsupported"}
    assert calls == [] and not registry._sessions and not registry._page_leases


def test_cancelled_observe_preserves_original_when_backend_cleanup_also_cancels():
    from openprogram.programs.workflow.browser.web_use_runtime import WebUseSessionRegistry, SUPPORTED_BACKENDS
    original = CancelledError("original cancellation")
    released = []
    def close(session):
        raise CancelledError("cleanup cancellation")
    adapter = SimpleNamespace(supports_operation_guard=True, close=close)
    registry = WebUseSessionRegistry(
        adapters={name: adapter for name in SUPPORTED_BACKENDS},
        binding_validator=lambda binding: {"ok": True}, binding_revision_resolver=lambda binding: {},
        page_key_resolver=lambda binding: binding, release_context=lambda context: released.append(context),
    )
    def check():
        raise original
    with pytest.raises(CancelledError) as caught:
        registry.execute(command="observe", owner_id="owner", binding_id="owned", before_dispatch=check)
    assert caught.value is original
    assert not registry._sessions and not registry._page_leases
    assert len(released) == 1
