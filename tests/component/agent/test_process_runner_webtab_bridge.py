import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from threading import Lock, Thread


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
