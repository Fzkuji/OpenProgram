"""Tests for built-in agentic functions (general_action, agent_loop, wait).

NOTE: this test file is currently disabled — the built-in modules it
imports (``openprogram.functions.agentics.{general_action,agent_loop,
wait}``) no longer exist in the codebase, they were removed during the
function-calling unification refactor (commit f4a3a30). The file
itself is still untracked. Re-enable once those built-ins are
restored under the new namespace.
"""

import json
import pytest
from unittest.mock import MagicMock, patch

pytestmark = pytest.mark.skip(
    reason="built-in agentic functions (general_action / agent_loop / wait) "
           "were removed during the function-calling refactor; tests await rewrite"
)

from openprogram import agentic_function
from openprogram.agentic_programming.runtime import Runtime

class TestGeneralAction:
    """Tests for general_action agentic function."""

    def test_json_response(self):
        """general_action parses JSON response from LLM."""
        from openprogram.functions.agentics.general_action import general_action
        from openprogram.agentic_programming.runtime import Runtime

        rt = Runtime(
            call=lambda *a, **kw: '{"success": true, "output": "installed numpy", "error": null}'
        )
        result = general_action(instruction="install numpy", runtime=rt)
        assert result["success"] is True
        assert "numpy" in result["output"]

    def test_markdown_json_response(self):
        """general_action extracts JSON from markdown fences."""
        from openprogram.functions.agentics.general_action import general_action
        from openprogram.agentic_programming.runtime import Runtime

        rt = Runtime(
            call=lambda *a, **kw: 'Here is the result:\n```json\n{"success": true, "output": "done", "error": null}\n```'
        )
        result = general_action(instruction="do something", runtime=rt)
        assert result["success"] is True

    def test_plain_text_fallback(self):
        """general_action falls back when LLM returns plain text."""
        from openprogram.functions.agentics.general_action import general_action
        from openprogram.agentic_programming.runtime import Runtime

        rt = Runtime(call=lambda *a, **kw: "I completed the task successfully.")
        result = general_action(instruction="do something", runtime=rt)
        assert result["success"] is True
        assert "completed" in result["output"]

    def test_error_response(self):
        """general_action handles error JSON."""
        from openprogram.functions.agentics.general_action import general_action
        from openprogram.agentic_programming.runtime import Runtime

        rt = Runtime(
            call=lambda *a, **kw: '{"success": false, "output": "", "error": "file not found"}'
        )
        result = general_action(instruction="read missing file", runtime=rt)
        assert result["success"] is False
        assert "not found" in result["error"]

    def test_no_runtime_raises(self):
        """general_action raises ValueError without runtime."""
        from openprogram.functions.agentics.general_action import general_action
        with pytest.raises(ValueError, match="runtime is required"):
            general_action(instruction="hello")


# ══════════════════════════════════════════════════════════════
# agent_loop tests
# ══════════════════════════════════════════════════════════════

class TestAgentLoop:
    """Tests for agent_loop autonomous execution."""

    @staticmethod
    def _mock_call(step_responses):
        """Create a mock call that handles both _step and wait calls.

        step_responses: list of JSON strings for _step calls (consumed in order).
        wait calls are auto-detected by content and return wait=0.
        """
        step_idx = [0]
        def mock(content, **kw):
            # Detect wait calls by checking content text
            text = ""
            for block in content:
                if isinstance(block, dict) and "text" in block:
                    text += block["text"]
            if "Action just completed" in text:
                return '{"wait": 0, "reason": "test"}'
            # Step call
            idx = step_idx[0]
            step_idx[0] += 1
            if idx < len(step_responses):
                return step_responses[idx]
            return step_responses[-1]  # repeat last
        return mock

    def test_done_on_first_step(self, tmp_path):
        """agent_loop stops when LLM reports done=true."""
        from openprogram.functions.agentics.agent_loop import agent_loop
        from openprogram.agentic_programming.runtime import Runtime

        rt = Runtime(call=self._mock_call([
            '{"done": true, "action": "wrote paper", "result": "complete", "next": null, "error": null}',
        ]))
        result = agent_loop(goal="write a paper", runtime=rt, max_steps=10, state_dir=str(tmp_path))
        assert result["done"] is True
        assert result["steps"] == 1

    def test_multi_step(self, tmp_path):
        """agent_loop runs multiple steps until done."""
        from openprogram.functions.agentics.agent_loop import agent_loop
        from openprogram.agentic_programming.runtime import Runtime

        rt = Runtime(call=self._mock_call([
            '{"done": false, "action": "research", "result": "found papers", "next": "write intro", "error": null}',
            '{"done": false, "action": "write intro", "result": "drafted", "next": "finalize", "error": null}',
            '{"done": true, "action": "finalize", "result": "complete", "next": null, "error": null}',
        ]))
        result = agent_loop(goal="write survey", runtime=rt, max_steps=10, state_dir=str(tmp_path))
        assert result["done"] is True
        assert result["steps"] == 3

    def test_max_steps_reached(self, tmp_path):
        """agent_loop stops at max_steps."""
        from openprogram.functions.agentics.agent_loop import agent_loop
        from openprogram.agentic_programming.runtime import Runtime

        rt = Runtime(call=self._mock_call([
            '{"done": false, "action": "work", "result": "progress", "next": "more", "error": null}',
        ]))
        result = agent_loop(goal="infinite task", runtime=rt, max_steps=3, state_dir=str(tmp_path))
        assert result["done"] is False
        assert result["steps"] == 3
        assert "max_steps" in result.get("error", "")

    def test_callback_can_cancel(self, tmp_path):
        """Returning False from callback stops the loop."""
        from openprogram.functions.agentics.agent_loop import agent_loop
        from openprogram.agentic_programming.runtime import Runtime

        rt = Runtime(call=self._mock_call([
            '{"done": false, "action": "work", "result": "ok", "next": "more", "error": null}',
        ]))
        result = agent_loop(goal="task", runtime=rt, max_steps=100, state_dir=str(tmp_path), callback=lambda r: False)
        assert result["done"] is True
        assert result.get("cancelled") is True
        assert result["steps"] == 1

    def test_handles_exception(self, tmp_path):
        """agent_loop records errors and continues."""
        from openprogram.functions.agentics.agent_loop import agent_loop
        from openprogram.agentic_programming.runtime import Runtime

        call_count = [0]
        def mock(content, **kw):
            text = ""
            for block in content:
                if isinstance(block, dict) and "text" in block:
                    text += block["text"]
            if "Action just completed" in text:
                return '{"wait": 0, "reason": "test"}'
            call_count[0] += 1
            if call_count[0] == 1:
                raise ConnectionError("network down")
            return '{"done": true, "action": "retry", "result": "ok", "next": null, "error": null}'

        rt = Runtime(call=mock, max_retries=1)
        result = agent_loop(goal="fragile task 3", runtime=rt, max_steps=5, state_dir=str(tmp_path))
        assert result["done"] is True
        assert result["steps"] == 2
        assert "error" in result["history"][0]["error"].lower()
        assert result["history"][1]["done"] is True

    def test_no_runtime_raises(self):
        """agent_loop raises ValueError without runtime."""
        from openprogram.functions.agentics.agent_loop import agent_loop
        with pytest.raises(ValueError, match="runtime is required"):
            agent_loop(goal="hello")

    def test_state_persistence(self, tmp_path):
        """agent_loop persists state to disk."""
        from openprogram.functions.agentics.agent_loop import agent_loop
        from openprogram.agentic_programming.runtime import Runtime

        rt = Runtime(call=self._mock_call([
            '{"done": true, "action": "done", "result": "ok", "next": null, "error": null}',
        ]))
        state_dir = str(tmp_path)
        result = agent_loop(goal="persist test", runtime=rt, state_dir=state_dir)
        assert result["done"] is True

        state_files = list(tmp_path.glob("agent_loop_*.json"))
        assert len(state_files) == 1

        import json
        with open(state_files[0]) as f:
            saved = json.load(f)
        assert saved["goal"] == "persist test"
        assert saved["done"] is True


# ══════════════════════════════════════════════════════════════
# wait tests
# ══════════════════════════════════════════════════════════════

class TestWait:
    """Tests for wait agentic function."""

    def test_returns_seconds(self):
        """wait returns the number of seconds decided by LLM."""
        from openprogram.functions.agentics.wait import wait
        from openprogram.agentic_programming.runtime import Runtime

        rt = Runtime(call=lambda *a, **kw: '{"wait": 0, "reason": "check immediately"}')
        seconds = wait(action="wrote a file", runtime=rt)
        assert seconds == 0

    def test_parses_nonzero_wait(self):
        """wait parses a nonzero wait time. Note: sleep is called internally."""
        from openprogram.functions.agentics.wait import wait
        from openprogram.agentic_programming.runtime import Runtime
        from unittest.mock import patch

        rt = Runtime(call=lambda *a, **kw: '{"wait": 5, "reason": "server starting"}')
        with patch("openprogram.functions.agentics.wait.time.sleep") as mock_sleep:
            seconds = wait(action="started server", runtime=rt)
            assert seconds == 5
            mock_sleep.assert_called_once_with(5)

    def test_fallback_on_bad_json(self):
        """wait defaults to 0 if LLM returns unparseable response."""
        from openprogram.functions.agentics.wait import wait
        from openprogram.agentic_programming.runtime import Runtime

        rt = Runtime(call=lambda *a, **kw: "I think you should wait a bit")
        seconds = wait(action="did something", runtime=rt)
        assert seconds == 0

    def test_no_runtime_raises(self):
        """wait raises ValueError without runtime."""
        from openprogram.functions.agentics.wait import wait
        with pytest.raises(ValueError, match="runtime is required"):
            wait(action="hello")

