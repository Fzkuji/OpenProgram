"""Tests for CLI-based providers (Codex, Claude Code, Gemini CLI)."""

import base64
import json
import subprocess
import pytest
from unittest.mock import MagicMock, patch, mock_open

from agentic import agentic_function
from agentic.runtime import Runtime

class TestCodexRuntime:
    """Tests for CodexRuntime with mocked subprocess."""

    @pytest.fixture(autouse=True)
    def setup_mock(self, monkeypatch, tmp_path):
        """Mock shutil.which and subprocess.Popen."""
        self.tmp_path = tmp_path
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/codex" if name == "codex" else None)

        # Default mock stdout lines (no special events)
        self._stdout_lines = ["mock codex reply\n"]

        def mock_popen(cmd, **kwargs):
            proc = MagicMock()
            proc.returncode = 0
            proc.stdin = MagicMock()
            proc.stderr = MagicMock()
            proc.stderr.read.return_value = ""
            proc.stdout = iter(self._stdout_lines)
            proc.wait.return_value = 0
            proc.poll.return_value = 0
            # Write output to -o file
            for i, arg in enumerate(cmd):
                if arg == "-o" and i + 1 < len(cmd):
                    with open(cmd[i + 1], "w") as f:
                        f.write("mock codex reply")
                    break
            self._last_cmd = cmd
            self._last_kwargs = kwargs
            self._last_proc = proc
            return proc

        self._mock_popen = MagicMock(side_effect=mock_popen)
        monkeypatch.setattr("subprocess.Popen", self._mock_popen)

        yield

    def _make_runtime(self, **kwargs):
        from agentic.providers.codex import CodexRuntime
        return CodexRuntime(cli_path="/usr/bin/codex", **kwargs)

    def test_text_only_call(self):
        """Text-only content produces correct codex exec command."""
        rt = self._make_runtime()
        result = rt._call([{"type": "text", "text": "hello"}])
        assert result == "mock codex reply"
        cmd = self._last_cmd
        assert cmd[0] == "/usr/bin/codex"
        assert "exec" in cmd
        assert "-a" in cmd
        assert cmd[cmd.index("-a") + 1] == "never"
        assert "--full-auto" in cmd
        assert "--skip-git-repo-check" in cmd
        # Prompt passed via stdin, "-" is the stdin marker
        assert cmd[-1] == "-"
        # Verify prompt was written to stdin
        self._last_proc.stdin.write.assert_called_with("hello")

    def test_model_flag(self):
        """Model is passed via --model flag."""
        rt = self._make_runtime(model="o3")
        rt._call([{"type": "text", "text": "hi"}], model="o3")
        cmd = self._last_cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "o3"

    def test_image_from_file(self, tmp_path):
        """Image with path is passed via -i flag."""
        img_path = tmp_path / "test.png"
        img_path.write_bytes(b"\x89PNG" + b"\x00" * 10)

        rt = self._make_runtime()
        rt._call([{"type": "image", "path": str(img_path)}])
        cmd = self._last_cmd
        assert "-i" in cmd
        idx = cmd.index("-i")
        assert cmd[idx + 1] == str(img_path)

    def test_image_from_base64(self):
        """Image with base64 data is written to temp file and passed via -i."""
        import base64 as b64
        data = b64.b64encode(b"\x89PNG\x00" * 3).decode()
        rt = self._make_runtime()
        rt._call([{"type": "image", "data": data, "media_type": "image/png"}])
        cmd = self._last_cmd
        assert "-i" in cmd
        idx = cmd.index("-i")
        # Should be a temp file path
        assert "codex_img_" in cmd[idx + 1]

    def test_image_url_fallback_to_text(self):
        """Image with URL adds text note since codex CLI doesn't support URLs."""
        rt = self._make_runtime()
        rt._call([{"type": "image", "url": "https://example.com/img.png"}])
        cmd = self._last_cmd
        # No -i flag for URL
        assert "-i" not in cmd
        # URL should appear in prompt written to stdin
        write_calls = self._last_proc.stdin.write.call_args_list
        prompt_input = write_calls[0][0][0] if write_calls else ""
        assert "https://example.com/img.png" in prompt_input

    def test_session_resume(self):
        """Second call uses 'resume' subcommand."""
        rt = self._make_runtime(session_id="test-session")
        rt._call([{"type": "text", "text": "first"}])
        cmd1 = self._last_cmd
        assert "resume" not in cmd1

        rt._call([{"type": "text", "text": "second"}])
        cmd2 = self._last_cmd
        assert "resume" in cmd2
        assert "test-session" in cmd2

    def test_auto_session_captures_thread_id_and_resumes(self):
        """Auto sessions resume only after Codex reports a real thread id."""
        call_count = [0]

        def popen_with_thread(cmd, **kwargs):
            call_count[0] += 1
            proc = MagicMock()
            proc.returncode = 0
            proc.stdin = MagicMock()
            proc.stderr = MagicMock()
            proc.stderr.read.return_value = ""
            proc.wait.return_value = 0
            proc.poll.return_value = 0
            if call_count[0] == 1:
                proc.stdout = iter(['{"type":"thread.started","thread_id":"thread-123"}\n'])
            else:
                proc.stdout = iter(['{"type":"thread.resumed","thread_id":"thread-123"}\n'])
            for i, arg in enumerate(cmd):
                if arg == "-o" and i + 1 < len(cmd):
                    with open(cmd[i + 1], "w") as f:
                        f.write("mock codex reply")
                    break
            self._last_cmd = cmd
            self._last_proc = proc
            return proc

        self._mock_popen.side_effect = popen_with_thread
        rt = self._make_runtime()

        assert rt.has_session is False
        assert rt._session_id is None

        rt._call([{"type": "text", "text": "first"}])
        assert rt.has_session is True
        assert rt._session_id == "thread-123"
        cmd1 = self._mock_popen.call_args[0][0]
        assert "resume" not in cmd1

        rt._call([{"type": "text", "text": "second"}])
        cmd2 = self._mock_popen.call_args[0][0]
        assert "resume" in cmd2
        assert "thread-123" in cmd2

    def test_auto_session_without_thread_id_stays_stateless(self):
        """If Codex does not report a thread id, later calls should not resume."""
        rt = self._make_runtime()

        assert rt.has_session is False
        assert rt._session_id is None

        rt._call([{"type": "text", "text": "first"}])
        rt._call([{"type": "text", "text": "second"}])
        cmd = self._last_cmd
        assert "resume" not in cmd
        assert rt._session_id is None

    def test_stateless_mode(self):
        """session_id=None never uses resume."""
        rt = self._make_runtime(session_id=None)
        rt._call([{"type": "text", "text": "first"}])
        rt._call([{"type": "text", "text": "second"}])
        cmd = self._last_cmd
        assert "resume" not in cmd

    def test_workdir_flag(self):
        """workdir is passed via --cd flag."""
        rt = self._make_runtime(workdir="/tmp/myproject")
        rt._call([{"type": "text", "text": "hi"}])
        cmd = self._last_cmd
        idx = cmd.index("--cd")
        assert cmd[idx + 1] == "/tmp/myproject"

    def test_search_flag(self):
        """search=True adds the root-level --search flag."""
        rt = self._make_runtime(search=True)
        rt._call([{"type": "text", "text": "weather"}])
        cmd = self._last_cmd
        assert "--search" in cmd
        assert cmd.index("--search") < cmd.index("exec")

    def test_custom_approval_policy(self):
        """Custom approval policy is passed as a root-level flag."""
        rt = self._make_runtime(approval_policy="on-request")
        rt._call([{"type": "text", "text": "hi"}])
        cmd = self._last_cmd
        idx = cmd.index("-a")
        assert cmd[idx + 1] == "on-request"

    def test_response_format_appended(self):
        """response_format is appended to prompt text."""
        rt = self._make_runtime()
        schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        rt._call([{"type": "text", "text": "test"}], response_format=schema)
        # Prompt is written to stdin
        write_calls = self._last_proc.stdin.write.call_args_list
        prompt_input = write_calls[0][0][0] if write_calls else ""
        assert "JSON" in prompt_input

    def test_cli_not_found(self, monkeypatch):
        """Missing CLI raises FileNotFoundError."""
        monkeypatch.setattr("shutil.which", lambda name: None)
        from agentic.providers.codex import CodexRuntime
        with pytest.raises(FileNotFoundError, match="Codex CLI not found"):
            CodexRuntime(cli_path=None)

    def test_cli_error_propagates(self):
        """CLI errors are raised as RuntimeError."""
        def failing_popen(cmd, **kwargs):
            proc = MagicMock()
            proc.returncode = 1
            proc.stdin = MagicMock()
            proc.stderr = MagicMock()
            proc.stderr.read.return_value = "something went wrong"
            proc.stdout = iter([])
            proc.wait.return_value = 1
            # Write output file so code can read it
            for i, arg in enumerate(cmd):
                if arg == "-o" and i + 1 < len(cmd):
                    with open(cmd[i + 1], "w") as f:
                        f.write("")
                    break
            return proc

        self._mock_popen.side_effect = failing_popen
        rt = self._make_runtime()

        with pytest.raises(RuntimeError, match="Codex CLI error"):
            rt._call([{"type": "text", "text": "test"}])

    def test_auth_error(self):
        """Auth errors raise ConnectionError."""
        def auth_fail_popen(cmd, **kwargs):
            proc = MagicMock()
            proc.returncode = 1
            proc.stdin = MagicMock()
            proc.stderr = MagicMock()
            proc.stderr.read.return_value = "Invalid API key"
            proc.stdout = iter([])
            proc.wait.return_value = 1
            for i, arg in enumerate(cmd):
                if arg == "-o" and i + 1 < len(cmd):
                    with open(cmd[i + 1], "w") as f:
                        f.write("")
                    break
            return proc

        self._mock_popen.side_effect = auth_fail_popen
        rt = self._make_runtime()

        with pytest.raises(ConnectionError, match="authentication"):
            rt._call([{"type": "text", "text": "test"}])

    def test_timeout(self):
        """Timeout raises TimeoutError."""
        def timeout_popen(cmd, **kwargs):
            proc = MagicMock()
            proc.stdin = MagicMock()
            proc.stderr = MagicMock()
            proc.stdout = iter([])
            proc.wait.side_effect = subprocess.TimeoutExpired(cmd="codex", timeout=10)
            proc.kill = MagicMock()
            for i, arg in enumerate(cmd):
                if arg == "-o" and i + 1 < len(cmd):
                    with open(cmd[i + 1], "w") as f:
                        f.write("")
                    break
            return proc

        self._mock_popen.side_effect = timeout_popen
        rt = self._make_runtime(timeout=10)

        with pytest.raises(TimeoutError, match="timed out"):
            rt._call([{"type": "text", "text": "test"}])

    def test_reset(self):
        """reset() creates new session and resets turn count."""
        def popen_with_thread(cmd, **kwargs):
            proc = MagicMock()
            proc.returncode = 0
            proc.stdin = MagicMock()
            proc.stderr = MagicMock()
            proc.stderr.read.return_value = ""
            proc.stdout = iter(['{"type":"thread.started","thread_id":"thread-reset"}\n'])
            proc.wait.return_value = 0
            proc.poll.return_value = 0
            for i, arg in enumerate(cmd):
                if arg == "-o" and i + 1 < len(cmd):
                    with open(cmd[i + 1], "w") as f:
                        f.write("mock codex reply")
                    break
            return proc

        self._mock_popen.side_effect = popen_with_thread
        rt = self._make_runtime()
        rt._call([{"type": "text", "text": "first"}])
        old_session = rt._session_id
        rt.reset()
        assert old_session == "thread-reset"
        assert rt._session_id is None
        assert rt._turn_count == 0
        assert rt.has_session is False

    def test_sandbox_mode(self):
        """Custom sandbox mode without full_auto."""
        rt = self._make_runtime(full_auto=False, sandbox="read-only")
        rt._call([{"type": "text", "text": "hi"}])
        cmd = self._last_cmd
        assert "--full-auto" not in cmd
        idx = cmd.index("--sandbox")
        assert cmd[idx + 1] == "read-only"

    def test_audio_block_warns(self):
        """Audio blocks emit a warning and are skipped."""
        rt = self._make_runtime()
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            rt._call([{"type": "text", "text": "hi"}, {"type": "audio", "path": "test.wav"}])
            audio_warnings = [x for x in w if "audio" in str(x.message).lower()]
            assert len(audio_warnings) == 1

    def test_video_block_warns(self):
        """Video blocks emit a warning and are skipped."""
        rt = self._make_runtime()
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            rt._call([{"type": "text", "text": "hi"}, {"type": "video", "path": "test.mp4"}])
            video_warnings = [x for x in w if "video" in str(x.message).lower()]
            assert len(video_warnings) == 1

    def test_file_block_warns(self):
        """File/PDF blocks emit a warning and are skipped."""
        rt = self._make_runtime()
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            rt._call([{"type": "text", "text": "hi"}, {"type": "file", "path": "test.pdf"}])
            file_warnings = [x for x in w if "file" in str(x.message).lower()]
            assert len(file_warnings) == 1


def test_visualizer_codex_runtime_enables_search(monkeypatch):
    """Visualizer chat uses stateless Codex with native web search enabled."""
    from agentic.visualize import server

    captured = {}

    def fake_create_runtime(provider=None, model=None, **kwargs):
        captured["provider"] = provider
        captured["model"] = model
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr("agentic.providers.create_runtime", fake_create_runtime)

    server._create_runtime_for_visualizer("codex")

    assert captured["provider"] == "codex"
    assert captured["kwargs"]["session_id"] is None
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
        from agentic.providers.claude_code import ClaudeCodeRuntime
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
    """Tests for GeminiCLIRuntime with mocked subprocess."""

    @pytest.fixture(autouse=True)
    def setup_mock(self, monkeypatch):
        """Mock shutil.which and subprocess.run."""
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/gemini" if name == "gemini" else None)

        def mock_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "mock gemini reply"
            result.stderr = ""
            return result

        self._mock_run = MagicMock(side_effect=mock_run)
        monkeypatch.setattr("subprocess.run", self._mock_run)

    def _make_runtime(self, **kwargs):
        from agentic.providers.gemini_cli import GeminiCLIRuntime
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


