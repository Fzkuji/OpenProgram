import multiprocessing
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from threading import Lock, Thread

import pytest


def test_child_webtab_bridge_round_trips_one_request():
    from openprogram.agent.process_runner import _new_child_webtab_bridge

    events = Queue()
    request, handle_answer = _new_child_webtab_bridge(events)
    result = {}
    thread = Thread(
        target=lambda: result.update(request({"op": "active"}, 1)),
        daemon=True,
    )
    thread.start()

    envelope = events.get(timeout=1)
    assert envelope["__op_webtab__"] is True
    assert envelope["data"]["command"] == {"op": "active"}
    assert handle_answer({
        "__op_webtab_result__": True,
        "req_id": envelope["data"]["req_id"],
        "result": {"ok": True, "tab_id": "tab-1", "target_id": "target-1"},
    }) is True

    thread.join(timeout=1)
    assert result == {"ok": True, "tab_id": "tab-1", "target_id": "target-1"}


def test_parent_webtab_bridge_rejects_unknown_operations(monkeypatch):
    from openprogram.agent import process_runner
    from openprogram.webui.ws_actions import webtab

    replies = Queue()
    called = []
    monkeypatch.setattr(
        webtab,
        "_request",
        lambda command, timeout: called.append((command, timeout)) or {"ok": True},
    )

    process_runner._bridge_webtab_to_parent(
        {"req_id": "bad", "command": {"op": "close"}, "timeout": 99},
        replies,
    )

    assert called == []
    assert replies.get(timeout=1)["result"] == {
        "ok": False,
        "error": "unsupported webtab bridge operation",
    }


def test_parent_webtab_bridge_result_crosses_process_queue(monkeypatch):
    from openprogram.agent import process_runner
    from openprogram.webui.ws_actions import webtab

    expected = {"ok": True, "tab_id": "tab-1", "target_id": "target-1"}
    monkeypatch.setattr(webtab, "_request", lambda command, timeout: expected)
    replies = multiprocessing.get_context("spawn").Queue()
    try:
        process_runner._bridge_webtab_to_parent(
            {"req_id": "active", "command": {"op": "active"}, "timeout": 1},
            replies,
        )

        envelope = replies.get(timeout=3)
        assert envelope["result"] == expected
        assert "_owner_ws" not in envelope["result"]
    finally:
        replies.close()
        replies.join_thread()


def test_parent_webtab_bridge_forwards_exact_screenshot(monkeypatch):
    from openprogram.agent import process_runner
    from openprogram.webui.ws_actions import webtab

    seen = []
    monkeypatch.setattr(
        webtab,
        "request_bound_screenshot",
        lambda binding_id, **kwargs: seen.append((binding_id, kwargs)) or {
            "ok": True,
            "image_data_url": "data:image/png;base64,cG5n",
        },
    )
    replies = Queue()
    process_runner._bridge_webtab_to_parent({
        "req_id": "screenshot",
        "command": {
            "op": "screenshot",
            "binding_id": "surface-1",
            "expected_page_revision": 2,
            "expected_access_revision": 3,
            "expected_geometry_revision": 4,
        },
        "timeout": 2,
    }, replies, allowed_bindings={"surface-1"})

    assert replies.get(timeout=1)["result"]["ok"] is True
    assert seen == [("surface-1", {
        "timeout": 2,
        "expected_page_revision": 2,
        "expected_access_revision": 3,
        "expected_geometry_revision": 4,
    })]


def test_parent_webtab_bridge_forwards_open_to_request_open_tab(monkeypatch):
    from openprogram.agent import process_runner
    from openprogram.webui.ws_actions import webtab

    seen = []
    monkeypatch.setattr(
        webtab,
        "request_open_tab",
        lambda url, timeout=15.0: seen.append((url, timeout)) or {
            "ok": True, "binding_id": "surface_from_open",
        },
    )
    replies = Queue()
    process_runner._bridge_webtab_to_parent({
        "req_id": "open",
        "command": {"op": "open", "url": "https://example.com/"},
        "timeout": 2,
    }, replies)

    assert replies.get(timeout=1)["result"] == {
        "ok": True, "binding_id": "surface_from_open",
    }
    assert seen == [("https://example.com/", 2)]


@pytest.mark.parametrize("ownership", [
    {"created": False, "reused": True},
    {},
    {"created": True},
    {"created": True, "reused": True},
])
def test_parent_webtab_bridge_borrows_visible_page_without_created_proof(
    monkeypatch, ownership,
):
    from openprogram.agent import process_runner
    from openprogram.webui.ws_actions import webtab

    activated = []
    closed = []
    monkeypatch.setattr(
        webtab,
        "request_open_tab",
        lambda _url, timeout=15.0: {
            "ok": True,
            "binding_id": "surface-visible",
            "window_id": "window-1",
            "tab_id": "tab-visible",
            "target_id": "target-visible",
            **ownership,
        },
    )
    monkeypatch.setattr(
        webtab,
        "request_bound_tab",
        lambda binding_id, **kwargs: activated.append((binding_id, kwargs)) or {
            "ok": True,
        },
    )
    monkeypatch.setattr(
        webtab,
        "request_close_tab",
        lambda *_args, **_kwargs: closed.append((_args, _kwargs)) or {"ok": True},
    )
    tracked = {}
    allowed = set()
    open_replies = Queue()
    process_runner._bridge_webtab_to_parent({
        "req_id": "visible-open",
        "command": {"op": "open", "url": "https://example.com/"},
        "timeout": 2,
    }, open_replies, tracked, allowed_window_id="window-1",
        allowed_bindings=allowed)
    assert open_replies.get(timeout=1)["result"]["ok"] is True

    activate_replies = Queue()
    process_runner._bridge_webtab_to_parent({
        "req_id": "visible-activate",
        "command": {"op": "activate", "binding_id": "surface-visible"},
        "timeout": 2,
    }, activate_replies, tracked, allowed_window_id="window-1",
        allowed_bindings=allowed)

    assert activate_replies.get(timeout=1)["result"]["ok"] is True
    assert tracked == {
        "surface-visible": {
            "window_id": "window-1",
            "tab_id": "tab-visible",
            "agent_owned": False,
            "close_on_exit": False,
        },
    }
    assert allowed == {"surface-visible"}
    assert activated[0][0] == "surface-visible"

    close_replies = Queue()
    process_runner._bridge_webtab_to_parent({
        "req_id": "visible-close",
        "command": {"op": "close", "binding_id": "surface-visible"},
        "timeout": 2,
    }, close_replies, tracked, allowed_window_id="window-1",
        allowed_bindings=allowed)

    close_result = close_replies.get(timeout=1)["result"]
    assert close_result["ok"] is False
    assert close_result["reason_code"] == "page_context_stale"
    assert closed == []
    assert set(tracked) == {"surface-visible"}

    released = []
    monkeypatch.setattr(
        webtab, "release_binding", lambda binding_id: released.append(binding_id),
    )
    assert process_runner._cleanup_bridged_webtabs(tracked) == []
    assert closed == []
    assert released == ["surface-visible"]
    assert tracked == {}


def test_parent_webtab_bridge_targets_background_open(monkeypatch):
    from openprogram.agent import process_runner
    from openprogram.webui.ws_actions import webtab

    other = object()
    owner = object()
    seen = []
    monkeypatch.setattr(
        webtab,
        "registered_desktop_windows",
        lambda: [(other, "window-1", 6), (owner, "window-2", 7)],
    )
    monkeypatch.setattr(
        webtab,
        "request_on_ws",
        lambda ws, command, timeout=5.0: seen.append(
            (ws, command, timeout)
        ) or {
            "ok": True,
            "window_id": "window-2",
            "tab_id": "tab-background",
            "target_id": "target-background",
            "created": True,
            "reused": False,
        },
    )
    bindings = []
    monkeypatch.setattr(
        webtab,
        "register_binding",
        lambda *args, **kwargs: bindings.append((args, kwargs))
        or "surface-background",
    )
    monkeypatch.setattr(
        webtab,
        "binding_connection",
        lambda binding_id: owner if binding_id == "surface-background" else None,
    )

    replies = Queue()
    owned = {}
    command = {
        "op": "open",
        "url": "https://www.google.com/",
        "window_id": "window-2",
        "background": True,
    }
    process_runner._bridge_webtab_to_parent({
        "req_id": "background-open",
        "command": command,
        "timeout": 2,
    }, replies, owned, allowed_window_id="window-2")

    assert replies.get(timeout=1)["result"]["binding_id"] == "surface-background"
    assert seen == [(owner, command, 2)]
    assert bindings[0][0][:4] == (
        owner, "window-2", "tab-background", "target-background",
    )
    assert bindings[0][1]["expected_connection_revision"] == 7
    assert owned == {
        "surface-background": {
            "window_id": "window-2",
            "tab_id": "tab-background",
            "agent_owned": True,
            "close_on_exit": True,
            "owner_ws": owner,
        },
    }


def test_parent_background_open_response_timeout_requires_manual_cleanup(
    monkeypatch,
):
    from openprogram.agent import process_runner
    from openprogram.webui.ws_actions import webtab

    owner = object()
    command = {
        "op": "open",
        "url": "https://www.google.com/",
        "window_id": "window-2",
        "background": True,
    }
    seen = []
    monkeypatch.setattr(
        webtab,
        "registered_desktop_windows",
        lambda: [(owner, "window-2", 7)],
    )
    monkeypatch.setattr(
        webtab,
        "request_on_ws",
        lambda ws, sent, timeout=5.0: seen.append((ws, sent, timeout)) or {
            "ok": False,
            "reason_code": webtab.RESPONSE_TIMEOUT_REASON_CODE,
            "error": "timeout: no desktop shell replied within 2s",
        },
    )

    replies = Queue()
    tracked = {}
    process_runner._bridge_webtab_to_parent({
        "req_id": "background-open-timeout",
        "command": command,
        "timeout": 2,
    }, replies, tracked, allowed_window_id="window-2")

    result = replies.get(timeout=1)["result"]
    assert seen == [(owner, command, 2)]
    assert result["status"] == "infeasible"
    assert result["success"] is False
    assert result["infeasible_declared"] is True
    assert result["reason_code"] == "page_cleanup_failed"
    assert "Close the remaining background Page" in result[
        "handoff_instruction"
    ]
    assert tracked == {}


@pytest.mark.parametrize("open_result", [
    {
        "ok": True,
        "window_id": "window-2",
        "tab_id": "tab-unusable",
        "target_id": "target-unusable",
        "created": True,
        "reused": False,
    },
    {
        "ok": True,
        "tab_id": "tab-unusable",
        "target_id": "target-unusable",
        "created": True,
        "reused": False,
    },
    {
        "ok": True,
        "window_id": "window-1",
        "tab_id": "tab-unusable",
        "created": True,
        "reused": False,
    },
])
def test_parent_webtab_bridge_rolls_back_unusable_open_result(
    monkeypatch, open_result,
):
    from openprogram.agent import process_runner
    from openprogram.webui.ws_actions import webtab

    owner = object()
    seen = []
    monkeypatch.setattr(
        webtab,
        "registered_desktop_windows",
        lambda: [(owner, "window-1", 7)],
    )

    def request(ws, command, timeout=5.0):
        seen.append((ws, command, timeout))
        if command["op"] == "open":
            return open_result
        return {"ok": True, "tab_id": command["tab_id"]}

    monkeypatch.setattr(webtab, "request_on_ws", request)
    monkeypatch.setattr(
        webtab,
        "register_binding",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("an unusable Page must not be bound")
        ),
    )
    replies = Queue()
    tracked = {}
    allowed = set()
    command = {
        "op": "open",
        "url": "https://www.google.com/",
        "window_id": "window-1",
        "background": True,
    }
    process_runner._bridge_webtab_to_parent({
        "req_id": "background-open-unusable",
        "command": command,
        "timeout": 2,
    }, replies, tracked, allowed_window_id="window-1",
        allowed_bindings=allowed)

    result = replies.get(timeout=1)["result"]
    assert result["ok"] is False
    assert result["reason_code"] == "page_context_stale"
    assert seen == [
        (owner, command, 2),
        (owner, {
            "op": "close",
            "window_id": "window-1",
            "tab_id": "tab-unusable",
        }, 2),
    ]
    assert tracked == {}
    assert allowed == set()


@pytest.mark.parametrize("ownership", [
    {"created": False, "reused": True},
    {"created": True},
    {"created": True, "reused": True},
    {"created": False, "reused": False},
    {"created": 1, "reused": False},
    {"created": True, "reused": 0},
])
def test_parent_webtab_bridge_does_not_close_unowned_invalid_page(
    monkeypatch, ownership,
):
    from openprogram.agent import process_runner
    from openprogram.webui.ws_actions import webtab

    owner = object()
    seen = []
    monkeypatch.setattr(
        webtab,
        "registered_desktop_windows",
        lambda: [(owner, "window-1", 7)],
    )
    monkeypatch.setattr(
        webtab,
        "request_on_ws",
        lambda ws, command, timeout=5.0: seen.append(
            (ws, command, timeout)
        ) or {
            "ok": True,
            "window_id": "window-2",
            "tab_id": "tab-user",
            "target_id": "target-user",
            **ownership,
        },
    )

    replies = Queue()
    command = {
        "op": "open",
        "url": "https://example.com/",
        "window_id": "window-1",
    }
    process_runner._bridge_webtab_to_parent({
        "req_id": "reused-visible-invalid",
        "command": command,
        "timeout": 2,
    }, replies, {}, allowed_window_id="window-1")

    result = replies.get(timeout=1)["result"]
    assert result["ok"] is False
    assert result["reason_code"] == "page_context_stale"
    assert seen == [(owner, command, 2)]


@pytest.mark.parametrize("close_results", [[False, True], [False, False]])
def test_parent_webtab_bridge_retries_rejected_identity_rollback(
    monkeypatch, close_results,
):
    from openprogram.agent import process_runner
    from openprogram.webui.ws_actions import webtab

    owner = object()
    close_calls = []
    monkeypatch.setattr(
        webtab,
        "registered_desktop_windows",
        lambda: [(owner, "window-1", 7)],
    )

    def request(_ws, command, timeout=5.0):
        if command["op"] == "open":
            return {
                "ok": True,
                "window_id": "window-2",
                "tab_id": "tab-unusable",
                "target_id": "target-unusable",
                "created": True,
                "reused": False,
            }
        close_calls.append((command, timeout))
        succeeded = close_results[len(close_calls) - 1]
        return {"ok": succeeded, "error": "close rejected"}

    monkeypatch.setattr(webtab, "request_on_ws", request)
    monkeypatch.setattr(webtab, "binding_connection", lambda _binding_id: None)
    monkeypatch.setattr(webtab, "release_binding", lambda _binding_id: None)
    replies = Queue()
    tracked = {}
    process_runner._bridge_webtab_to_parent({
        "req_id": "background-open-cleanup-rejected",
        "command": {
            "op": "open",
            "url": "https://www.google.com/",
            "window_id": "window-1",
            "background": True,
        },
        "timeout": 2,
    }, replies, tracked, allowed_window_id="window-1")

    result = replies.get(timeout=1)["result"]
    if close_results[-1]:
        assert result["reason_code"] == "page_context_stale"
        assert tracked == {}
    else:
        assert result["reason_code"] == "page_cleanup_failed"
        assert result["success"] is False
        assert result["infeasible_declared"] is True
        assert next(iter(tracked.values()))["cleanup_exhausted"] is True
        failures = process_runner._cleanup_bridged_webtabs(tracked)
        assert failures == [{
            "binding_id": next(iter(tracked)),
            "window_id": "window-1",
            "tab_id": "tab-unusable",
            "error": "close rejected",
        }]
    assert [call[0]["tab_id"] for call in close_calls] == [
        "tab-unusable", "tab-unusable",
    ]


def test_parent_webtab_bridge_rejects_background_open_outside_origin(monkeypatch):
    from openprogram.agent import process_runner
    from openprogram.webui.ws_actions import webtab

    calls = []
    monkeypatch.setattr(
        webtab,
        "registered_desktop_windows",
        lambda: calls.append("registered") or [],
    )
    replies = Queue()
    process_runner._bridge_webtab_to_parent({
        "req_id": "background-open-other-window",
        "command": {
            "op": "open",
            "url": "https://www.google.com/",
            "window_id": "window-2",
            "background": True,
        },
        "timeout": 2,
    }, replies, {}, allowed_window_id="window-1")

    result = replies.get(timeout=1)["result"]
    assert result["ok"] is False
    assert result["reason_code"] == "page_context_stale"
    assert calls == []


def test_parent_webtab_bridge_targets_background_close(monkeypatch):
    from openprogram.agent import process_runner
    from openprogram.webui.ws_actions import webtab

    owner = object()
    seen = []
    released = []
    monkeypatch.setattr(
        webtab,
        "registered_desktop_windows",
        lambda: [(owner, "window-1", 7)],
    )
    monkeypatch.setattr(
        webtab,
        "binding_connection",
        lambda binding_id: owner if binding_id == "surface-background" else None,
    )
    monkeypatch.setattr(
        webtab,
        "request_on_ws",
        lambda ws, command, timeout=5.0: seen.append(
            (ws, command, timeout)
        ) or {"ok": True, "tab_id": "tab-background"},
    )
    monkeypatch.setattr(
        webtab, "release_binding", lambda binding_id: released.append(binding_id),
    )

    replies = Queue()
    owned = {
        "surface-background": {
            "window_id": "window-1",
            "tab_id": "tab-background",
            "agent_owned": True,
            "close_on_exit": True,
        },
    }
    command = {
        "op": "close",
        "window_id": "window-1",
        "tab_id": "tab-background",
    }
    process_runner._bridge_webtab_to_parent({
        "req_id": "background-close",
        "command": command,
        "timeout": 2,
    }, replies, owned)

    assert replies.get(timeout=1)["result"]["ok"] is True
    assert seen == [(owner, command, 2)]
    assert released == ["surface-background"]
    assert owned == {}


def test_parent_webtab_bridge_rejects_exact_close_for_unowned_page(monkeypatch):
    from openprogram.agent import process_runner
    from openprogram.webui.ws_actions import webtab

    calls = []
    monkeypatch.setattr(
        webtab,
        "registered_desktop_windows",
        lambda: calls.append("registered") or [],
    )
    monkeypatch.setattr(
        webtab,
        "binding_connection",
        lambda _binding_id: calls.append("binding") or None,
    )
    monkeypatch.setattr(
        webtab,
        "request_on_ws",
        lambda *_args, **_kwargs: calls.append("close") or {"ok": True},
    )
    replies = Queue()
    process_runner._bridge_webtab_to_parent({
        "req_id": "unowned-close",
        "command": {
            "op": "close",
            "window_id": "window-1",
            "tab_id": "user-tab",
        },
        "timeout": 2,
    }, replies, {}, allowed_window_id="window-1")

    result = replies.get(timeout=1)["result"]
    assert result["ok"] is False
    assert result["reason_code"] == "page_context_stale"
    assert calls == []


def test_parent_webtab_bridge_rejects_binding_close_for_borrowed_page(monkeypatch):
    from openprogram.agent import process_runner
    from openprogram.webui.ws_actions import webtab

    calls = []
    monkeypatch.setattr(
        webtab,
        "request_close_tab",
        lambda *_args, **_kwargs: calls.append((_args, _kwargs)) or {"ok": True},
    )
    tracked = {
        "surface-user": {
            "window_id": "window-1",
            "tab_id": "user-tab",
            "agent_owned": False,
            "close_on_exit": False,
        },
    }
    replies = Queue()
    process_runner._bridge_webtab_to_parent({
        "req_id": "borrowed-close",
        "command": {"op": "close", "binding_id": "surface-user"},
        "timeout": 2,
    }, replies, tracked, allowed_window_id="window-1",
        allowed_bindings={"surface-user"})

    result = replies.get(timeout=1)["result"]
    assert result["ok"] is False
    assert result["reason_code"] == "page_context_stale"
    assert calls == []
    assert set(tracked) == {"surface-user"}


def test_parent_webtab_bridge_captures_origin_pages_as_borrowed_bindings(
    monkeypatch,
):
    from openprogram.agent import process_runner, surface_context

    captured = {
        "context_id": "page-context",
        "window_id": "window-1",
        "surfaces": [{
            "window_id": "window-1",
            "tab_id": "user-tab",
            "binding_id": "surface-existing",
        }],
    }
    inputs = []
    monkeypatch.setattr(
        surface_context,
        "capture_pages",
        lambda context: inputs.append(context) or captured,
    )
    replies = Queue()
    tracked = {}
    allowed_bindings = set()
    process_runner._bridge_webtab_to_parent({
        "req_id": "capture-origin",
        "command": {
            "op": "capture_pages",
            "window_id": "window-1",
            "tab_id": "user-tab",
        },
        "timeout": 2,
    }, replies, tracked, allowed_window_id="window-1",
        allowed_bindings=allowed_bindings)

    result = replies.get(timeout=1)["result"]
    assert result == {"ok": True, "context": captured}
    assert inputs[0]["origin_window_id"] == "window-1"
    assert inputs[0]["origin_tab_id"] == "user-tab"
    assert tracked == {
        "surface-existing": {
            "window_id": "window-1",
            "tab_id": "user-tab",
            "agent_owned": False,
            "close_on_exit": False,
        },
    }
    assert allowed_bindings == {"surface-existing"}


def test_parent_webtab_bridge_rejects_page_capture_outside_origin(monkeypatch):
    from openprogram.agent import process_runner, surface_context

    calls = []
    monkeypatch.setattr(
        surface_context,
        "capture_pages",
        lambda context: calls.append(context) or {},
    )
    replies = Queue()
    process_runner._bridge_webtab_to_parent({
        "req_id": "capture-other",
        "command": {"op": "capture_pages", "window_id": "window-2"},
        "timeout": 2,
    }, replies, {}, allowed_window_id="window-1", allowed_bindings=set())

    result = replies.get(timeout=1)["result"]
    assert result["ok"] is False
    assert result["reason_code"] == "page_context_stale"
    assert calls == []


def test_parent_webtab_bridge_keeps_prior_borrowed_binding_for_live_session(
    monkeypatch,
):
    from openprogram.agent import process_runner, surface_context
    from openprogram.webui.ws_actions import webtab

    captures = iter([
        {
            "context_id": "capture-1",
            "window_id": "window-1",
            "surfaces": [{
                "window_id": "window-1",
                "tab_id": "tab-existing",
                "binding_id": "surface-1",
            }],
        },
        {
            "context_id": "capture-2",
            "window_id": "window-1",
            "surfaces": [{
                "window_id": "window-1",
                "tab_id": "tab-existing",
                "binding_id": "surface-2",
            }],
        },
    ])
    released = []
    monkeypatch.setattr(surface_context, "capture_pages", lambda _context: next(captures))
    monkeypatch.setattr(
        webtab, "release_binding", lambda binding_id: released.append(binding_id),
    )
    tracked = {}
    allowed = set()
    for req_id in ("capture-1", "capture-2"):
        replies = Queue()
        process_runner._bridge_webtab_to_parent({
            "req_id": req_id,
            "command": {"op": "capture_pages", "window_id": "window-1"},
            "timeout": 2,
        }, replies, tracked, allowed_window_id="window-1",
            allowed_bindings=allowed)
        assert replies.get(timeout=1)["result"]["ok"] is True

    assert released == []
    assert set(tracked) == {"surface-1", "surface-2"}
    assert allowed == {"surface-1", "surface-2"}

    activated = []
    monkeypatch.setattr(
        webtab,
        "request_bound_tab",
        lambda binding_id, **_kwargs: activated.append(binding_id) or {"ok": True},
    )
    replies = Queue()
    process_runner._bridge_webtab_to_parent({
        "req_id": "activate-prior-session-binding",
        "command": {"op": "activate", "binding_id": "surface-1"},
        "timeout": 2,
    }, replies, tracked, allowed_window_id="window-1",
        allowed_bindings=allowed)
    assert replies.get(timeout=1)["result"]["ok"] is True
    assert activated == ["surface-1"]


def test_parent_webtab_bridge_closes_page_when_binding_registration_fails(
    monkeypatch,
):
    from openprogram.agent import process_runner
    from openprogram.webui.ws_actions import webtab

    owner = object()
    commands = []
    monkeypatch.setattr(
        webtab,
        "registered_desktop_windows",
        lambda: [(owner, "window-1", 7)],
    )

    def request(_owner, command, timeout=5.0):
        commands.append((command, timeout))
        if command["op"] == "open":
            return {
                "ok": True,
                "window_id": "window-1",
                "tab_id": "tab-created",
                "target_id": "target-created",
                "created": True,
                "reused": False,
            }
        return {"ok": True}

    monkeypatch.setattr(webtab, "request_on_ws", request)
    monkeypatch.setattr(
        webtab,
        "register_binding",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("connection changed")
        ),
    )
    replies = Queue()
    owned = {}
    process_runner._bridge_webtab_to_parent({
        "req_id": "binding-race",
        "command": {
            "op": "open",
            "url": "https://www.google.com/",
            "window_id": "window-1",
            "background": True,
        },
        "timeout": 2,
    }, replies, owned, allowed_window_id="window-1")

    result = replies.get(timeout=1)["result"]
    assert result["ok"] is False
    assert "RuntimeError: connection changed" in result["error"]
    assert commands == [
        ({
            "op": "open",
            "url": "https://www.google.com/",
            "window_id": "window-1",
            "background": True,
        }, 2),
        ({
            "op": "close",
            "window_id": "window-1",
            "tab_id": "tab-created",
        }, 2),
    ]
    assert owned == {}


def test_parent_cleans_background_page_after_child_termination(monkeypatch):
    from openprogram.agent import process_runner
    from openprogram.webui.ws_actions import webtab

    owner = object()
    closed = []
    released = []
    monkeypatch.setattr(
        webtab,
        "binding_connection",
        lambda binding_id: owner if binding_id == "surface-background" else None,
    )
    monkeypatch.setattr(
        webtab,
        "request_on_ws",
        lambda ws, command, timeout=5.0: closed.append(
            (ws, command, timeout)
        ) or {"ok": True},
    )
    monkeypatch.setattr(
        webtab, "release_binding", lambda binding_id: released.append(binding_id),
    )
    owned = {
        "surface-background": {
            "window_id": "window-1",
            "tab_id": "tab-background",
            "agent_owned": True,
            "close_on_exit": True,
        },
    }

    process_runner._cleanup_bridged_webtabs(owned)

    assert closed == [(owner, {
        "op": "close",
        "window_id": "window-1",
        "tab_id": "tab-background",
    }, 5.0)]
    assert released == ["surface-background"]
    assert owned == {}


def test_parent_cleanup_uses_recorded_owner_after_binding_is_invalidated(
    monkeypatch,
):
    from openprogram.agent import process_runner
    from openprogram.webui.ws_actions import webtab

    owner = object()
    closed = []
    released = []
    monkeypatch.setattr(webtab, "binding_connection", lambda _binding_id: None)
    monkeypatch.setattr(
        webtab,
        "request_on_ws",
        lambda ws, command, timeout=5.0: closed.append(
            (ws, command, timeout)
        ) or {"ok": True},
    )
    monkeypatch.setattr(
        webtab, "release_binding", lambda binding_id: released.append(binding_id),
    )
    tracked = {
        "surface-background": {
            "window_id": "window-1",
            "tab_id": "tab-background",
            "agent_owned": True,
            "close_on_exit": True,
            "owner_ws": owner,
        },
    }

    process_runner._cleanup_bridged_webtabs(tracked)

    assert closed == [(owner, {
        "op": "close",
        "window_id": "window-1",
        "tab_id": "tab-background",
    }, 5.0)]
    assert released == ["surface-background"]
    assert tracked == {}


def test_parent_cleanup_preserves_identity_when_close_is_rejected(monkeypatch):
    from openprogram.agent import process_runner
    from openprogram.webui.ws_actions import webtab

    owner = object()
    released = []
    monkeypatch.setattr(webtab, "binding_connection", lambda _binding_id: owner)
    monkeypatch.setattr(
        webtab,
        "request_on_ws",
        lambda *_args, **_kwargs: {"ok": False, "error": "close rejected"},
    )
    monkeypatch.setattr(
        webtab, "release_binding", lambda binding_id: released.append(binding_id),
    )
    tracked = {
        "surface-background": {
            "window_id": "window-1",
            "tab_id": "tab-background",
            "agent_owned": True,
            "close_on_exit": True,
            "owner_ws": owner,
        },
    }

    failures = process_runner._cleanup_bridged_webtabs(tracked)

    assert failures == [{
        "binding_id": "surface-background",
        "window_id": "window-1",
        "tab_id": "tab-background",
        "error": "close rejected",
    }]
    assert released == []
    assert set(tracked) == {"surface-background"}


@pytest.mark.parametrize("open_timeout", [False, True])
def test_timeout_waits_for_late_open_cleanup_failure(
    monkeypatch, open_timeout,
):
    from openprogram.agent import process_runner
    from openprogram.webui.ws_actions import webtab

    owner = object()
    open_started = threading.Event()
    release_open = threading.Event()
    closed_tabs = []

    class FakeProcess:
        pid = 4321
        exitcode = -9

        def __init__(self, *, target, args, daemon):
            del target, daemon
            self.alive = True
            self.event_queue = args[6]

        def start(self):
            self.event_queue.put({
                "__op_webtab__": True,
                "data": {
                    "req_id": "late-open",
                    "command": {
                        "op": "open",
                        "url": "https://example.com/",
                        "window_id": "window-1",
                        "background": True,
                    },
                    "timeout": 2,
                },
            })

        def join(self, timeout=None):
            if self.alive and timeout != 5:
                assert open_started.wait(1)

        def is_alive(self):
            return self.alive

        def kill(self):
            raise AssertionError("process-tree termination should succeed")

    process = None

    class FakeContext:
        Queue = Queue

        def Process(self, **kwargs):
            nonlocal process
            process = FakeProcess(**kwargs)
            return process

    def request_on_ws(_owner, command, timeout=5.0):
        del timeout
        if command["op"] == "open":
            open_started.set()
            assert release_open.wait(2)
            if open_timeout:
                return {
                    "ok": False,
                    "reason_code": webtab.RESPONSE_TIMEOUT_REASON_CODE,
                    "error": "background open timed out",
                }
            return {
                "ok": True,
                "created": True,
                "reused": False,
                "window_id": "window-1",
                "tab_id": "tab-late",
                "target_id": "target-late",
            }
        closed_tabs.append(command["tab_id"])
        return {"ok": False, "error": "close rejected"}

    monkeypatch.setattr(process_runner.mp, "get_context", lambda _kind: FakeContext())
    monkeypatch.setattr(
        "openprogram._compat.kill_process_tree",
        lambda _pid: setattr(process, "alive", False) or True,
    )
    monkeypatch.setattr(
        webtab,
        "registered_desktop_windows",
        lambda: [(owner, "window-1", 7)],
    )
    monkeypatch.setattr(webtab, "request_on_ws", request_on_ws)
    monkeypatch.setattr(
        webtab,
        "register_binding",
        lambda *_args, **_kwargs: "surface-late",
    )
    monkeypatch.setattr(webtab, "binding_connection", lambda _binding_id: owner)
    monkeypatch.setattr(webtab, "binding_page_key", lambda _binding_id: "page:late")
    monkeypatch.setattr(webtab, "binding_revisions", lambda _binding_id: {})
    monkeypatch.setattr(webtab, "release_binding", lambda _binding_id: None)

    def release_late_open():
        assert open_started.wait(1)
        time.sleep(0.65)
        release_open.set()

    releaser = Thread(target=release_late_open, daemon=True)
    releaser.start()
    started_at = time.monotonic()
    result = process_runner.run_agentic_in_subprocess(
        tool_name="gui_agent",
        kwargs={},
        session_id="s-late-open",
        anchor_msg_id="m",
        timeout_seconds=0.1,
        surface_context_snapshot={"origin_window_id": "window-1"},
    )
    elapsed = time.monotonic() - started_at

    assert 0.6 <= elapsed < 3
    assert closed_tabs == ([] if open_timeout else ["tab-late", "tab-late"])
    assert result["reason_code"] == "page_cleanup_failed"
    assert result["ok"] is False
    assert result["success"] is False
    assert result["infeasible_declared"] is True
    assert result["page_cleanup_result"]["cleanup_failures"] == (
        [{"error": "background open timed out"}]
        if open_timeout else
        [{
            "binding_id": "surface-late",
            "window_id": "window-1",
            "tab_id": "tab-late",
            "error": "close rejected",
        }]
    )
    assert "Close the remaining background Page" in result[
        "handoff_instruction"
    ]


def test_parent_cleanup_only_releases_borrowed_page_binding(monkeypatch):
    from openprogram.agent import process_runner
    from openprogram.webui.ws_actions import webtab

    closed = []
    released = []
    monkeypatch.setattr(
        webtab,
        "request_on_ws",
        lambda *_args, **_kwargs: closed.append((_args, _kwargs)) or {"ok": True},
    )
    monkeypatch.setattr(
        webtab, "release_binding", lambda binding_id: released.append(binding_id),
    )
    borrowed = {
        "surface-existing": {
            "window_id": "window-1",
            "tab_id": "user-tab",
            "agent_owned": False,
            "close_on_exit": False,
        },
    }

    process_runner._cleanup_bridged_webtabs(borrowed)

    assert closed == []
    assert released == ["surface-existing"]
    assert borrowed == {}


def test_parent_webtab_bridge_forwards_close_binding(monkeypatch):
    from openprogram.agent import process_runner
    from openprogram.webui.ws_actions import webtab

    seen = []
    monkeypatch.setattr(
        webtab,
        "request_close_tab",
        lambda binding_id, timeout=5.0: seen.append((binding_id, timeout)) or {
            "ok": True,
        },
    )
    replies = Queue()
    process_runner._bridge_webtab_to_parent({
        "req_id": "close",
        "command": {"op": "close", "binding_id": "surface-1"},
        "timeout": 1,
    }, replies)

    assert replies.get(timeout=1)["result"] == {"ok": True}
    assert seen == [("surface-1", 1)]


def test_parent_webtab_bridge_forwards_expected_binding_revisions(monkeypatch):
    from openprogram.agent import process_runner
    from openprogram.webui.ws_actions import webtab

    seen = []
    monkeypatch.setattr(
        webtab,
        "request_bound_tab",
        lambda binding_id, **kwargs: seen.append((binding_id, kwargs)) or {
            "ok": True,
        },
    )
    replies = Queue()

    process_runner._bridge_webtab_to_parent({
        "req_id": "bound",
        "command": {
            "op": "activate",
            "binding_id": "surface-1",
            "expected_page_revision": 31,
            "expected_access_revision": 32,
            "expected_geometry_revision": 33,
        },
        "timeout": 1,
    }, replies)

    assert replies.get(timeout=1)["result"] == {"ok": True}
    assert seen == [("surface-1", {
        "url": "",
        "timeout": 1,
        "expected_page_revision": 31,
        "expected_access_revision": 32,
        "expected_geometry_revision": 33,
    })]


def test_multiwindow_bindings_remain_isolated_under_concurrency(monkeypatch):
    from openprogram.webui.ws_actions import webtab

    owners = [object(), object()]
    bindings = []
    expected = {}
    targets = {}
    for owner_index, owner in enumerate(owners):
        for tab_index in range(24):
            window_id = f"window-{owner_index}"
            tab_id = f"tab-{tab_index}"
            target_id = f"target-{tab_index}"
            binding_id = webtab.register_binding(
                owner, window_id, tab_id, target_id,
            )
            bindings.append(binding_id)
            expected[binding_id] = (owner, window_id, tab_id, target_id)
            targets[(owner, window_id, tab_id)] = target_id

    seen = []
    seen_lock = Lock()

    def request(owner, command, timeout=5.0):
        del timeout
        with seen_lock:
            seen.append((owner, command["window_id"], command["tab_id"]))
        return {
            "ok": True,
            "window_id": command["window_id"],
            "tab_id": command["tab_id"],
            "target_id": targets[(owner, command["window_id"], command["tab_id"])],
        }

    monkeypatch.setattr(webtab, "request_on_ws", request)
    try:
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(webtab.request_bound_tab, bindings))

        assert all(result["ok"] for result in results)
        assert len(seen) == len(bindings)
        for binding_id in bindings:
            owner, window_id, tab_id, _ = expected[binding_id]
            assert (owner, window_id, tab_id) in seen
        for tab_index in range(24):
            assert webtab.binding_page_key(bindings[tab_index]) != (
                webtab.binding_page_key(bindings[24 + tab_index])
            )

        webtab.release_connection(owners[0])
        assert all(binding_id not in webtab._bindings for binding_id in bindings[:24])
        assert all(binding_id in webtab._bindings for binding_id in bindings[24:])
    finally:
        for owner in owners:
            webtab.release_connection(owner)
