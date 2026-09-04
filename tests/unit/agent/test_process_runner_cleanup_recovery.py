from __future__ import annotations

import json
import pickle
import queue

import pytest


@pytest.mark.parametrize(
    ("close_results", "original_status", "cleanup_persists"),
    [
        ([False, True], "error", False),
        ([False, True], "cancelled", False),
        ([False, False], "succeeded", True),
    ],
)
def test_parent_retries_child_exact_close_before_terminal_is_persisted(
    monkeypatch, close_results, original_status, cleanup_persists,
):
    from openprogram.agent import process_runner
    from openprogram.webui.ws_actions import webtab

    owner = object()
    closed_tabs = []
    released = []
    original = {
        "status": original_status,
        "success": original_status == "succeeded",
        "infeasible_declared": False,
        "reason_code": (
            "verified" if original_status == "succeeded" else original_status
        ),
        "summary": f"The GUI task ended with {original_status}.",
    }

    class FakeProcess:
        pid = 4321
        exitcode = 0

        def __init__(self, *, target, args, daemon):
            del target, daemon
            self.result_path = args[5]
            self.event_queue = args[6]
            self.answer_queue = args[8]
            self.alive = True

        def start(self):
            self.event_queue.put({
                "__op_webtab__": True,
                "data": {
                    "req_id": "open",
                    "command": {
                        "op": "open",
                        "url": "https://www.google.com/",
                        "window_id": "window-1",
                        "background": True,
                    },
                    "timeout": 2,
                },
            })

        def join(self, timeout=None):
            assert timeout is None
            opened = self.answer_queue.get(timeout=2)["result"]
            self.event_queue.put({
                "__op_webtab__": True,
                "data": {
                    "req_id": "close",
                    "command": {
                        "op": "close",
                        "window_id": opened["window_id"],
                        "tab_id": opened["tab_id"],
                    },
                    "timeout": 2,
                },
            })
            closed = self.answer_queue.get(timeout=2)["result"]
            child_result = original if closed["ok"] else {
                "status": "infeasible",
                "success": False,
                "infeasible_declared": True,
                "reason_code": "page_cleanup_failed",
                "summary": "The background Page could not be confirmed closed.",
                "handoff_instruction": "Close the remaining background Page.",
            }
            with open(self.result_path, "wb") as result_file:
                pickle.dump({
                    "ok": True,
                    "runtime_msg_id": "runtime-1",
                    "text": json.dumps(child_result),
                }, result_file)
            self.alive = False

        def is_alive(self):
            return self.alive

    class FakeContext:
        Queue = queue.Queue

        def Process(self, **kwargs):
            return FakeProcess(**kwargs)

    def request_on_ws(_owner, command, timeout=5.0):
        del timeout
        if command["op"] == "open":
            return {
                "ok": True,
                "created": True,
                "reused": False,
                "window_id": "window-1",
                "tab_id": "tab-background",
                "target_id": "target-background",
            }
        closed_tabs.append(command["tab_id"])
        succeeded = close_results[min(
            len(closed_tabs) - 1,
            len(close_results) - 1,
        )]
        return {"ok": succeeded, **({} if succeeded else {
            "error": "close rejected",
        })}

    monkeypatch.setattr(process_runner.mp, "get_context", lambda _kind: FakeContext())
    monkeypatch.setattr(webtab, "registered_desktop_windows", lambda: [
        (owner, "window-1", 7),
    ])
    monkeypatch.setattr(webtab, "request_on_ws", request_on_ws)
    monkeypatch.setattr(
        webtab, "register_binding", lambda *_args, **_kwargs: "surface-background",
    )
    monkeypatch.setattr(webtab, "binding_connection", lambda _binding_id: owner)
    monkeypatch.setattr(webtab, "binding_page_key", lambda _binding_id: "page:background")
    monkeypatch.setattr(webtab, "binding_revisions", lambda _binding_id: {})
    monkeypatch.setattr(
        webtab, "release_binding", lambda binding_id: released.append(binding_id),
    )

    result = process_runner.run_agentic_in_subprocess(
        tool_name="gui_agent",
        kwargs={},
        session_id="s",
        anchor_msg_id="m",
        surface_context_snapshot={"origin_window_id": "window-1"},
    )

    assert closed_tabs == ["tab-background"] * len(close_results)
    if cleanup_persists:
        assert result["reason_code"] == "page_cleanup_failed"
        assert result["success"] is False
        assert result["infeasible_declared"] is True
        assert "Close the remaining background Page" in result[
            "handoff_instruction"
        ]
        assert released == []
    else:
        assert json.loads(result["text"]) == original
        assert "page_cleanup_failed" not in result
        assert released == ["surface-background"]
