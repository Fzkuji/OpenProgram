from queue import Queue
from threading import Thread


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
