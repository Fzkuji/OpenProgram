"""Registered Python execution preserves actual failures and their output."""
from __future__ import annotations

import asyncio

import pytest

from openprogram.programs import get_agent_tool


@pytest.mark.parametrize(
    ("code", "failed", "expected"),
    [
        ("print('success-output')", False, "success-output"),
        ("print('before-failure'); raise ValueError('code-failed')", True, "code-failed"),
        ("import sys; sys.exit(7)", True, "exit=7"),
        ("import time; print('before-timeout', flush=True); time.sleep(30)", True, "timed out"),
    ],
)
def test_registered_execute_code_reports_subprocess_outcome(tmp_path, monkeypatch, code, failed, expected):
    monkeypatch.setattr('openprogram.host.sandbox.resolve_policy', lambda: None)
    result = asyncio.run(get_agent_tool('execute_code').execute(
        'code-outcome', {'code': code, 'cwd': str(tmp_path), 'timeout': 1}, None, None,
    ))
    assert result.is_error is failed
    text = '\n'.join(block.text for block in result.content if hasattr(block, 'text'))
    assert expected in text
    if 'before-' in code:
        assert ('before-timeout' if 'sleep' in code else 'before-failure') in text
    assert not list(tmp_path.glob('.openprogram-execute-*'))


@pytest.mark.parametrize('outcome', ['success', 'failed', 'timeout'])
def test_registered_execute_code_preserves_remote_backend_outcomes(monkeypatch, outcome):
    import subprocess

    killed = []

    class Process:
        returncode = 7 if outcome == 'failed' else 0

        def communicate(self, input, timeout):
            assert input == 'print(42)'
            if outcome == 'timeout':
                raise subprocess.TimeoutExpired('python -', timeout, output=b'remote-partial')
            return 'remote-output', None

        def kill(self):
            killed.append(True)

    class Backend:
        backend_id = 'ssh'

        def spawn(self, command):
            return Process()

    monkeypatch.setattr('openprogram.host.backend.get_active_backend', Backend)
    result = asyncio.run(get_agent_tool('execute_code').execute(
        'remote-code-outcome', {'code': 'print(42)', 'timeout': 1}, None, None,
    ))
    assert result.is_error is (outcome != 'success')
    text = '\n'.join(block.text for block in result.content if hasattr(block, 'text'))
    assert ('remote-partial' if outcome == 'timeout' else 'remote-output') in text
    assert killed == ([True] if outcome == 'timeout' else [])
