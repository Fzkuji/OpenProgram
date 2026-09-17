"""Native completion receipts and exact-socket dispatch; no real PTY is used."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

_PATH = Path(__file__).resolve().parents[3] / "openprogram" / "terminal_resources.py"
_SPEC = importlib.util.spec_from_file_location("terminal_result_contract", _PATH)
module = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = module
_SPEC.loader.exec_module(module)


def test_exact_native_receipt_detaches_result_and_completes_once():
    results = module.TerminalResults()
    token, ready, holder = results.reserve("main")
    result = {"ok": True, "data": "output", "command_complete": None}
    results.deliver(token, "main", result)
    result["data"] = "changed"
    assert ready.is_set()
    assert holder["result"]["data"] == "output"
    with pytest.raises(PermissionError):
        results.deliver(token, "main", {"ok": True})
    results.release(token)


def test_foreign_window_or_late_receipt_never_completes_a_different_request():
    results = module.TerminalResults()
    token, ready, _ = results.reserve("main")
    with pytest.raises(PermissionError):
        results.deliver(token, "other", {"ok": True})
    assert not ready.is_set()
    results.release(token)
    with pytest.raises(PermissionError):
        results.deliver(token, "main", {"ok": True})


@pytest.mark.parametrize("result", [None, {"ok": 1}, {"ok": True, "data": "x" * 256001}, {"ok": True, "value": float("nan")}])
def test_malformed_or_unbounded_results_are_refused(result):
    results = module.TerminalResults()
    token, ready, _ = results.reserve("main")
    with pytest.raises(ValueError):
        results.deliver(token, "main", result)
    assert not ready.is_set()
    results.release(token)


def test_pending_result_slots_are_bounded_and_released():
    results = module.TerminalResults(limit=1)
    token, _, _ = results.reserve("main")
    with pytest.raises(RuntimeError):
        results.reserve("main")
    results.release(token)
    results.reserve("other")


@pytest.mark.parametrize("connected", [True, False])
def test_dispatch_uses_native_completion_without_browser_field_filtering(monkeypatch, connected):
    ws, loop = object(), object()
    broker, results = module.TerminalTickets(), module.TerminalResults()
    monkeypatch.setattr(module, "_tickets", broker)
    monkeypatch.setattr(module, "_results", results)
    webtab = SimpleNamespace(registered_desktop_windows=lambda: [(ws, "main", 8)])
    monkeypatch.setitem(sys.modules, "openprogram.webui.ws_actions", SimpleNamespace(webtab=webtab))
    monkeypatch.setitem(sys.modules, "openprogram.webui", SimpleNamespace(server=SimpleNamespace(_loop=loop)))
    sent = []

    def send(target, payload, event_loop):
        assert target is ws and event_loop is loop
        message = json.loads(payload)
        assert message["type"] == "terminal.command"
        sent.append(message)
        if not connected:
            return False
        plan = broker.claim(message["data"]["ticket"], "main")
        # The native result preserves raw output and delivery semantics.
        results.deliver(plan["result_ticket"], "main", {"ok": True, "data": "native output", "command_complete": None})
        return True

    monkeypatch.setitem(sys.modules, "openprogram.webui.ws_delivery", SimpleNamespace(send_to_connection=send))
    response = module._dispatch({"window_id": "main", "terminal_id": "id", "operation_id": "op"},
                                ws=ws, window_id="main", revision=8, valid=lambda: True)
    assert response["ok"] is connected
    if connected:
        assert response["data"] == "native output"
        assert response["command_complete"] is None
    else:
        assert response["result_unconfirmed"] is True
    assert len(sent) == 1
    assert not broker._tickets and not results._pending
