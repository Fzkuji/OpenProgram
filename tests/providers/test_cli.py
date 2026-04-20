"""Tests for CLI-based providers (Codex, Claude Code, Gemini CLI)."""

import base64
import json
import subprocess
import pytest
from unittest.mock import MagicMock, patch, mock_open

from openprogram import agentic_function
from openprogram.agentic_programming.runtime import Runtime

# Note: subprocess-based OpenAICodexRuntime tests were removed when the
# runtime switched to HTTP direct (chatgpt.com/backend-api or api.openai.com).
# Re-add targeted httpx-mocked tests when the HTTP shape stabilizes.
def test_visualizer_codex_runtime_enables_search(monkeypatch):
    """Visualizer Codex keeps default auto-session and enables native web search."""
    from openprogram.webui import server

    captured = {}

    def fake_create_runtime(provider=None, model=None, **kwargs):
        captured["provider"] = provider
        captured["model"] = model
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr("openprogram.legacy_providers.create_runtime", fake_create_runtime)

    server._create_runtime_for_visualizer("openai-codex")

    assert captured["provider"] == "openai-codex"
    assert "session_id" not in captured["kwargs"]
    assert captured["kwargs"]["search"] is True



# ══════════════════════════════════════════════════════════════
# ClaudeCodeRuntime unsupported modality tests
# ══════════════════════════════════════════════════════════════

class TestClaudeCodeRuntimeUnsupported:
    """Tests that ClaudeCodeRuntime warns on unsupported modalities."""

    @pytest.fixture(autouse=True)
    def setup_mock(self, monkeypatch):
        """Mock shutil.which and subprocess.Popen for persistent process mode."""
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude" if name == "claude" else None)

        # Mock Popen to simulate a persistent claude process
        self._mock_stdin = MagicMock()
        self._mock_stdout = MagicMock()
        self._mock_proc = MagicMock()
        self._mock_proc.poll.return_value = None  # process is alive
        self._mock_proc.stdin = self._mock_stdin
        self._mock_proc.stdout = self._mock_stdout
        self._mock_proc.stderr = MagicMock()

        # _read_response reads lines from stdout; return a result message
        self._mock_stdout.readline.side_effect = [
            '{"type":"result","result":"mock reply"}\n',
        ]

        self._orig_popen = subprocess.Popen
        monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: self._mock_proc)

    def _reset_stdout(self):
        """Reset mock stdout for a fresh _call."""
        self._mock_stdout.readline.side_effect = [
            '{"type":"result","result":"mock reply"}\n',
        ]

    def _make_runtime(self, **kwargs):
        from openprogram.legacy_providers.claude_code import ClaudeCodeRuntime
        return ClaudeCodeRuntime(cli_path="/usr/bin/claude", **kwargs)

    def test_audio_block_warns(self):
        """Audio blocks emit a warning and are filtered out."""
        rt = self._make_runtime()
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            rt._call([{"type": "text", "text": "hi"}, {"type": "audio", "path": "test.wav"}])
            audio_warnings = [x for x in w if "audio" in str(x.message).lower()]
            assert len(audio_warnings) == 1

    def test_video_block_warns(self):
        """Video blocks emit a warning and are filtered out."""
        rt = self._make_runtime()
        self._reset_stdout()
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            rt._call([{"type": "text", "text": "hi"}, {"type": "video", "path": "test.mp4"}])
            video_warnings = [x for x in w if "video" in str(x.message).lower()]
            assert len(video_warnings) == 1

    def test_file_block_warns(self):
        """File/PDF blocks emit a warning and are filtered out."""
        rt = self._make_runtime()
        self._reset_stdout()
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            rt._call([{"type": "text", "text": "hi"}, {"type": "file", "path": "test.pdf"}])
            file_warnings = [x for x in w if "file" in str(x.message).lower()]
            assert len(file_warnings) == 1

    def test_unknown_block_with_text_fallback(self):
        """Unknown blocks with text fall back to text content."""
        rt = self._make_runtime()
        self._reset_stdout()
        result = rt._call([{"type": "custom", "text": "fallback text"}])
        assert result == "mock reply"
        # Verify the text was sent via stdin
        written = self._mock_stdin.write.call_args[0][0]
        msg = json.loads(written.strip())
        content = msg["message"]["content"]
        assert any(block.get("type") == "text" and block.get("text") == "fallback text" for block in content)


# ══════════════════════════════════════════════════════════════
# Provider lazy import tests
# ══════════════════════════════════════════════════════════════

class TestGeminiCLIRuntime:
    """Tests for GeminiCLIRuntime with mocked subprocess.Popen."""

    @pytest.fixture(autouse=True)
    def setup_mock(self, monkeypatch):
        """Mock shutil.which and subprocess.Popen.

        GeminiCLIRuntime uses Popen + communicate() so self._proc is
        exposed for external kill. The mock returns a proc whose
        communicate() produces ("mock gemini reply", "") with rc=0.
        """
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/gemini" if name == "gemini" else None)

        def make_popen(cmd, **kwargs):
            proc = MagicMock()
            proc.communicate = MagicMock(return_value=("mock gemini reply", ""))
            proc.returncode = 0
            proc.kill = MagicMock()
            proc.terminate = MagicMock()
            return proc

        self._mock_run = MagicMock(side_effect=make_popen)
        monkeypatch.setattr("subprocess.Popen", self._mock_run)

    def _make_runtime(self, **kwargs):
        from openprogram.legacy_providers.gemini_cli import GeminiCLIRuntime
        return GeminiCLIRuntime(cli_path="/usr/bin/gemini", **kwargs)

    def test_unknown_block_with_text_fallback(self):
        """Unknown blocks with text fall back to plain text."""
        rt = self._make_runtime()
        result = rt._call([{"type": "custom", "text": "fallback text"}])
        assert result == "mock gemini reply"
        cmd = self._mock_run.call_args[0][0]
        # prompt is at index 1 (no -p flag)
        assert cmd[1] == "fallback text"

    def test_missing_type_defaults_to_text(self):
        """Blocks without type default to text instead of raising KeyError."""
        rt = self._make_runtime()
        result = rt._call([{"text": "implicit text"}])
        assert result == "mock gemini reply"
        cmd = self._mock_run.call_args[0][0]
        assert cmd[1] == "implicit text"

    def test_image_block_warns_and_uses_placeholder(self):
        """Image blocks warn and degrade to a text placeholder."""
        rt = self._make_runtime()
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = rt._call([{"type": "image", "path": "diagram.png"}])

        assert result == "mock gemini reply"
        image_warnings = [x for x in w if "image" in str(x.message).lower()]
        assert len(image_warnings) == 1
        cmd = self._mock_run.call_args[0][0]
        assert cmd[1] == "[Image: diagram.png]"

    @pytest.mark.parametrize(
        ("block_type", "path", "expected_prompt"),
        [
            ("audio", "clip.wav", "[Audio: clip.wav]"),
            ("video", "demo.mp4", "[Video: demo.mp4]"),
            ("file", "spec.pdf", "[File: spec.pdf]"),
        ],
    )
    def test_unsupported_modalities_warn_and_use_placeholders(self, block_type, path, expected_prompt):
        """Audio/video/file blocks warn and degrade to text placeholders."""
        rt = self._make_runtime()
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = rt._call([{"type": block_type, "path": path}])

        assert result == "mock gemini reply"
        matching_warnings = [x for x in w if block_type in str(x.message).lower()]
        assert len(matching_warnings) == 1
        cmd = self._mock_run.call_args[0][0]
        assert cmd[1] == expected_prompt
