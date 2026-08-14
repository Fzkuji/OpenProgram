from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest


class _FakeLocator:
    def __init__(self, page, index: int | None = None):
        self.page = page
        self.index = index

    def nth(self, index: int):
        return _FakeLocator(self.page, index)

    def aria_snapshot(self):
        return "- document:\n  - button \"Save\"\n  - textbox \"Name\""

    def click(self):
        self.page.calls.append(("click", self.index))
        self.page.body_text = "Saved"

    def fill(self, text: str):
        self.page.calls.append(("fill", self.index, text))

    def press(self, key: str):
        self.page.calls.append(("press", self.index, key))

    def hover(self):
        self.page.calls.append(("hover", self.index))

    def select_option(self, value: str):
        self.page.calls.append(("select", self.index, value))


class _FakePage:
    def __init__(self):
        self.url = "http://127.0.0.1:18100/fixture"
        self.body_text = "Name Save"
        self.calls = []
        self.viewport_size = {"width": 960, "height": 640}

    def title(self):
        return "Fixture"

    def locator(self, _selector: str):
        return _FakeLocator(self)

    def evaluate(self, _script: str):
        return {
            "text": self.body_text,
            "scroll_x": 0,
            "scroll_y": 0,
            "device_scale_factor": 1,
            "elements": [
                {
                    "dom_index": 0,
                    "tag": "button",
                    "role": "button",
                    "name": "Save",
                    "disabled": False,
                },
                {
                    "dom_index": 1,
                    "tag": "input",
                    "role": "textbox",
                    "name": "Name",
                    "disabled": False,
                },
            ],
        }

    def screenshot(self, *, full_page: bool):
        self.calls.append(("screenshot", full_page))
        return b"\x89PNG fake"

    def goto(self, url: str):
        self.calls.append(("goto", url))
        self.url = url

    def inner_text(self, selector: str):
        assert selector == "body"
        return self.body_text


class _FakeBrowserAPI:
    def __init__(self):
        self.page = _FakePage()
        self._sessions = {}
        self.closed = []
        self.threads = []

    def execute(self, action: str, **kwargs):
        self.threads.append(threading.get_ident())
        if action == "open":
            assert kwargs["engine"] == "app"
            self._sessions["br_test"] = {
                "page": self.page,
                "app_tab_id": "tab-1",
                "app_target_id": "target-1",
            }
            return "Opened browser session `br_test` (engine=app)."
        if action == "close":
            sid = kwargs["session_id"]
            self.closed.append(sid)
            self._sessions.pop(sid, None)
            return f"Closed {sid}."
        raise AssertionError(f"unexpected browser action: {action}")


def _controller():
    from openprogram.programs.agentic_functions.browser_agent import (
        BrowserPageController,
    )

    api = _FakeBrowserAPI()
    return BrowserPageController(browser_api=api), api


def test_browser_agent_is_an_explicit_internal_agentic_module():
    from openprogram.programs._registry import AGENTIC_MODULES

    assert "browser_agent" in AGENTIC_MODULES


def test_observe_returns_dom_aria_and_refs_without_a_screenshot():
    controller, api = _controller()

    result = controller.execute(action="observe")

    assert result["url"] == api.page.url
    assert result["title"] == "Fixture"
    assert result["viewport"] == {
        "width": 960,
        "height": 640,
        "device_scale_factor": 1,
        "scroll_x": 0,
        "scroll_y": 0,
    }
    assert result["text"] == "Name Save"
    assert result["aria_snapshot"].startswith("- document:")
    assert [element["ref"] for element in result["elements"]] == ["e1", "e2"]
    assert not any(call[0] == "screenshot" for call in api.page.calls)


def test_screenshot_is_one_current_viewport_image_on_the_same_frame():
    from openprogram.programs import ToolReturn

    controller, api = _controller()
    observation = controller.execute(action="observe")

    result = controller.execute(
        action="screenshot",
        expected_frame_id=observation["frame_id"],
    )

    assert isinstance(result, ToolReturn)
    assert result.images == [b"\x89PNG fake"]
    assert api.page.calls[-1] == ("screenshot", False)
    assert observation["frame_id"] in (result.text or "")


def test_write_requires_fresh_frame_and_invalidates_old_refs():
    controller, api = _controller()
    observation = controller.execute(action="observe")

    result = controller.execute(
        action="click",
        expected_frame_id=observation["frame_id"],
        ref="e1",
    )

    assert result["ok"] is True
    assert api.page.calls[-1] == ("click", 0)
    stale = controller.execute(
        action="click",
        expected_frame_id=observation["frame_id"],
        ref="e1",
    )
    assert stale == {"ok": False, "reason_code": "stale_observation"}


def test_dom_change_between_observe_and_action_makes_the_frame_stale():
    controller, api = _controller()
    observation = controller.execute(action="observe")
    api.page.body_text = "The page changed independently"

    result = controller.execute(
        action="click",
        expected_frame_id=observation["frame_id"],
        ref="e1",
    )

    assert result == {"ok": False, "reason_code": "stale_observation"}
    assert not any(call[0] == "click" for call in api.page.calls)


def test_dom_verification_after_the_last_write_is_completion_authority():
    controller, _api = _controller()
    first = controller.execute(action="observe")
    controller.execute(
        action="click", expected_frame_id=first["frame_id"], ref="e1"
    )
    second = controller.execute(action="observe")

    verified = controller.execute(
        action="verify",
        expected_frame_id=second["frame_id"],
        assertion="text_contains",
        value="Saved",
    )

    assert verified["passed"] is True
    result = controller.final_result(summary="form submitted")
    assert result["status"] == "succeeded"
    assert result["reason_code"] == "verified"
    assert result["completion_evidence"] == [verified["evidence"]]


def test_model_summary_cannot_claim_success_without_runtime_verification():
    controller, _api = _controller()
    controller.execute(action="observe")

    result = controller.final_result(summary="I completed the task successfully")

    assert result["status"] == "failed"
    assert result["reason_code"] == "verification_missing"


def test_controller_closes_only_its_own_browser_session():
    controller, api = _controller()
    controller.execute(action="observe")

    controller.close()

    assert api.closed == ["br_test"]
    assert controller.session_id == ""


def test_controller_owns_browser_session_on_one_dedicated_thread():
    controller, api = _controller()
    caller_thread = threading.get_ident()
    controller.execute(action="observe")

    controller.close()

    assert api.closed == ["br_test"]
    assert "br_test" not in api._sessions
    assert len(set(api.threads)) == 1
    assert api.threads[0] != caller_thread
    assert controller.session_id == ""


def test_public_browser_agent_uses_restricted_tool_and_closes(monkeypatch):
    from openprogram.programs.agentic_functions import browser_agent as module

    class _StubController:
        def __init__(self):
            self.closed = False
            self.tool = SimpleNamespace(name="browser_page")

        def final_result(self, *, summary: str, reason_code: str | None = None):
            return {
                "status": "failed",
                "reason_code": reason_code or "verification_missing",
                "summary": summary,
                "steps_taken": 0,
                "completion_evidence": [],
            }

        def close(self):
            self.closed = True

    controller = _StubController()
    monkeypatch.setattr(module, "_new_controller", lambda: controller)

    class _Runtime:
        def exec(self, **kwargs):
            assert kwargs["tools"] == [controller.tool]
            assert kwargs["parallel_tool_calls"] is False
            assert kwargs["max_iterations"] == 15
            assert "response_format" not in kwargs
            return "model says done"

    result = module.browser_agent(
        task="Submit the local form",
        max_steps=4,
        max_seconds=30,
        runtime=_Runtime(),
    )

    assert result["status"] == "failed"
    assert result["reason_code"] == "verification_missing"
    assert controller.closed is True


def test_external_initial_url_requires_approval_but_localhost_does_not():
    from openprogram.programs.agentic_functions.browser_agent import (
        _browser_agent_requires_approval,
    )

    assert _browser_agent_requires_approval(url="http://127.0.0.1:3000") is False
    assert isinstance(
        _browser_agent_requires_approval(url="https://example.com/form"), str
    )


@pytest.mark.parametrize(
    "interrupt",
    [
        asyncio.CancelledError(),
        pytest.param(None, id="runtime-exec-interrupt"),
        pytest.param("agentic", id="agentic-cancelled-error"),
    ],
)
def test_runtime_cancellation_returns_cancelled_and_closes(monkeypatch, interrupt):
    from openprogram.agentic_programming.function import CancelledError
    from openprogram.programs.agentic_functions import browser_agent as module
    from openprogram.providers.utils.errors import ExecInterrupt

    raised = (
        CancelledError("cancelled") if interrupt == "agentic"
        else interrupt or ExecInterrupt("cancelled")
    )

    class _Controller:
        tool = SimpleNamespace(name="browser_page")

        def __init__(self):
            self.closed = False

        def final_result(self, *, summary: str, reason_code: str | None = None):
            return {
                "status": "cancelled" if reason_code == "cancelled" else "failed",
                "reason_code": reason_code,
                "summary": summary,
            }

        def close(self):
            self.closed = True
            return None

    controller = _Controller()
    monkeypatch.setattr(module, "_new_controller", lambda: controller)

    class _Runtime:
        def exec(self, **_kwargs):
            raise raised

    result = module.browser_agent(task="Stop", runtime=_Runtime())

    assert result["status"] == "cancelled"
    assert result["reason_code"] == "cancelled"
    assert controller.closed is True


def test_unhandled_control_signal_propagates_after_cleanup(monkeypatch):
    from openprogram.programs.agentic_functions import browser_agent as module

    class _Controller:
        tool = SimpleNamespace(name="browser_page")

        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True
            return None

    controller = _Controller()
    monkeypatch.setattr(module, "_new_controller", lambda: controller)

    class _Runtime:
        def exec(self, **_kwargs):
            raise KeyboardInterrupt("process interrupt")

    with pytest.raises(KeyboardInterrupt, match="process interrupt"):
        module.browser_agent(task="Interrupt", runtime=_Runtime())

    assert controller.closed is True


def test_invalid_initial_url_fails_before_runtime_or_browser_open(monkeypatch):
    from openprogram.programs.agentic_functions import browser_agent as module

    controller, api = _controller()
    monkeypatch.setattr(module, "_new_controller", lambda: controller)

    class _Runtime:
        def exec(self, **_kwargs):
            raise AssertionError("runtime must not be called for an invalid URL")

    result = module.browser_agent(
        task="Read a local file", url="file:///etc/passwd", runtime=_Runtime()
    )

    assert result["status"] == "failed"
    assert result["reason_code"] == "unsupported_url"
    assert api._sessions == {}


def test_cleanup_failure_is_reported_and_downgrades_success(monkeypatch):
    from openprogram.programs.agentic_functions import browser_agent as module

    class _Controller:
        tool = SimpleNamespace(name="browser_page")

        def final_result(self, *, summary: str, reason_code: str | None = None):
            return {
                "status": "succeeded",
                "reason_code": reason_code or "verified",
                "summary": summary,
            }

        def close(self):
            return "playwright: disconnect failed"

    controller = _Controller()
    monkeypatch.setattr(module, "_new_controller", lambda: controller)

    class _Runtime:
        def exec(self, **_kwargs):
            return {"summary": "done"}

    result = module.browser_agent(task="Submit", runtime=_Runtime())

    assert result["status"] == "failed"
    assert result["reason_code"] == "cleanup_failed"
    assert result["cleanup_error"] == "playwright: disconnect failed"


def test_browser_agent_source_has_no_heavy_gui_or_auxiliary_vision_imports():
    import inspect
    from openprogram.programs.agentic_functions import browser_agent as module

    source = inspect.getsource(module)
    assert "import gui_harness" not in source
    assert "image_analyze" not in source
    assert "ultralytics" not in source
    assert "cv2" not in source
