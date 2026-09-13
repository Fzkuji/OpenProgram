"""web use screenshots tests."""
from __future__ import annotations
from ._support import (
    asyncio,
    json,
    pytest,
)


def test_gui_harness_screenshot_capability_is_one_request_only(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.programs import ToolReturn
    from openprogram.programs.workflow import browser as module
    from openprogram.programs.workflow.browser import (
        web_use_runtime,
    )

    class _Registry:
        def __init__(self):
            self.revoked = 0

        def execute(self, **kwargs):
            command = kwargs["command"]
            if command == "observe":
                return {"frame_id": "f1", "web_session_id": "cs1"}
            if command == "act" and kwargs["arguments"]["action"] == "screenshot":
                return ToolReturn(images=[b"png"], json_data={"frame_id": "f1"})
            if command == "verify":
                return {"passed": True}
            return {"ok": True}

        def revoke_screenshot(self, _session_id):
            self.revoked += 1

    registry = _Registry()
    monkeypatch.setattr(web_use_runtime, "get_registry", lambda: registry)
    monkeypatch.setattr(surface_context, "current", lambda: {
        "context_id": "ctx",
        "surfaces": [{
            "binding_id": "b1",
            "capabilities": ["observe"],
        }],
    })
    monkeypatch.setattr(surface_context, "resolve_binding", lambda _page="": "b1")
    monkeypatch.setattr(surface_context, "resolve_page_key", lambda _page="": "p1")

    class _Runtime:
        def __init__(self):
            self.requests = []
            self.contents = []
            self.tool_results = []

        def exec(self, **kwargs):
            self.requests.append([block["type"] for block in kwargs["content"]])
            self.contents.append(kwargs["content"])
            index = len(self.requests)
            if index == 1:
                self.tool_results.append(asyncio.run(kwargs["tools"][0].execute(
                    "c1", {"action": "screenshot", "expected_frame_id": "f1"},
                    asyncio.Event(), None,
                )))
            elif index == 2:
                assert "b'png'" not in kwargs["content"][0]["text"]
            elif index == 3:
                asyncio.run(kwargs["tools"][0].execute(
                    "c3", {
                        "action": "verify", "expected_frame_id": "f1",
                        "assertion": "text_contains", "value": "done",
                    }, asyncio.Event(), None,
                ))
            return ""

    runtime = _Runtime()
    result = module._run_browser_task_commands(
        task="visual task", backend="open_claude_chrome",
        max_steps=1, max_seconds=30, runtime=runtime,
    )
    assert result["status"] == "succeeded"
    assert runtime.requests == [["text"], ["text", "image"], ["text"]]
    assert [block["type"] for block in runtime.contents[1]] == ["text"]
    assert [block.type for block in runtime.tool_results[0].content] == ["text"]
    assert set(json.loads(runtime.tool_results[0].content[0].text)) == {
        "frame_id", "image_attached",
    }
    assert registry.revoked == 1



def test_gui_harness_releases_unsent_final_screenshot(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.programs import ToolReturn
    from openprogram.programs.workflow import browser as module
    from openprogram.programs.workflow.browser import (
        web_use_runtime,
    )

    captured = {}

    class _Registry:
        revoked = 0

        def execute(self, **kwargs):
            command = kwargs["command"]
            if command == "observe":
                return {"frame_id": "f1", "web_session_id": "cs1"}
            if command == "act":
                captured["result"] = ToolReturn(
                    images=[b"png"], json_data={"frame_id": "f1"},
                )
                return captured["result"]
            return {"ok": True}

        def revoke_screenshot(self, _session_id):
            self.revoked += 1

    registry = _Registry()
    monkeypatch.setattr(web_use_runtime, "get_registry", lambda: registry)
    monkeypatch.setattr(surface_context, "current", lambda: {
        "context_id": "ctx",
        "surfaces": [{
            "binding_id": "b1",
            "capabilities": ["observe"],
        }],
    })
    monkeypatch.setattr(surface_context, "resolve_binding", lambda _page="": "b1")
    monkeypatch.setattr(surface_context, "resolve_page_key", lambda _page="": "p1")

    class _Runtime:
        calls = 0

        def exec(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                asyncio.run(kwargs["tools"][0].execute(
                    "c1", {"action": "screenshot", "expected_frame_id": "f1"},
                    asyncio.Event(), None,
                ))
            return ""

    result = module._run_browser_task_commands(
        task="visual task", backend="open_claude_chrome",
        max_steps=1, max_seconds=30, runtime=_Runtime(),
    )

    assert result["reason_code"] == "tool_not_executed"
    assert result["summary"] == (
        "The model did not execute the required browser_page tool call."
    )
    assert captured["result"].images == []
    assert registry.revoked == 1



@pytest.mark.parametrize("cancelled", [False, True])
def test_gui_harness_releases_same_request_screenshot_on_runtime_error(
    monkeypatch, cancelled,
):
    from openprogram.agent import surface_context
    from openprogram.programs import ToolReturn
    from openprogram.programs.workflow import browser as module
    from openprogram.programs.workflow.browser import (
        web_use_runtime,
    )
    from openprogram.providers.utils.errors import ExecInterrupt

    captured = {}

    class _Registry:
        revoked = 0

        def execute(self, **kwargs):
            command = kwargs["command"]
            if command == "observe":
                return {"frame_id": "f1", "web_session_id": "cs1"}
            if command == "act":
                captured["result"] = ToolReturn(
                    images=[b"png"], json_data={"frame_id": "f1"},
                )
                return captured["result"]
            return {"ok": True}

        def revoke_screenshot(self, _session_id):
            self.revoked += 1

    registry = _Registry()
    monkeypatch.setattr(web_use_runtime, "get_registry", lambda: registry)
    monkeypatch.setattr(surface_context, "current", lambda: {
        "context_id": "ctx",
        "surfaces": [{
            "binding_id": "b1",
            "capabilities": ["observe"],
        }],
    })
    monkeypatch.setattr(surface_context, "resolve_binding", lambda _page="": "b1")
    monkeypatch.setattr(surface_context, "resolve_page_key", lambda _page="": "p1")

    class _Runtime:
        def exec(self, **kwargs):
            asyncio.run(kwargs["tools"][0].execute(
                "c1", {"action": "screenshot", "expected_frame_id": "f1"},
                asyncio.Event(), None,
            ))
            if cancelled:
                raise ExecInterrupt("cancelled after tool execution")
            raise RuntimeError("provider failed after tool execution")

    expected_error = ExecInterrupt if cancelled else RuntimeError
    with pytest.raises(expected_error, match="cancelled|provider failed"):
        module._run_browser_task_commands(
            task="visual task", backend="open_claude_chrome",
            max_steps=1, max_seconds=30, runtime=_Runtime(),
        )

    assert captured["result"].images == []
    assert registry.revoked == 1



def test_web_use_preserves_screenshot_tool_return(monkeypatch):
    from openprogram.programs import ToolReturn
    from openprogram.programs.workflow import browser
    screenshot = ToolReturn(images=[b"png"], json_data={"frame_id": "f1"})
    monkeypatch.setattr(browser, "_execute_web_use", lambda *args: screenshot)
    result = browser.web_use(command="act", arguments={"action": "screenshot"})
    assert result is screenshot
    assert result.images == [b"png"]

