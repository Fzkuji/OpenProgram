"""Function progress frames retain an addressable transcript identity."""
import threading
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("caller", ["", "assistant-reply"])
def test_live_progress_addresses_top_level_function_card(monkeypatch, caller):
    from openprogram.webui import _exec_dag
    from openprogram.webui.ws_actions import branch

    tree = {"path": "function-node", "name": "gui_agent", "status": "running",
            "children": [{"path": "step", "name": "planning", "status": "running"}]}
    monkeypatch.setattr(_exec_dag, "build_exec_dag", lambda *_args: tree)
    monkeypatch.setattr(branch, "build_branches_payload", lambda *_args: {})
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: SimpleNamespace(invalidate_cache=lambda *_: None))
    received = threading.Event()
    frames = []

    def sink(frame):
        if frame.get("type") == "chat_response":
            frames.append(frame)
            received.set()

    with _exec_dag.live_progress("session", caller, "gui_agent", on_event=sink):
        assert received.wait(2), "no progress before the function returns"
        assert frames[0]["data"]["msg_id"] == (caller or "function-node")
        assert frames[0]["data"]["tree"]["children"][0]["status"] == "running"
