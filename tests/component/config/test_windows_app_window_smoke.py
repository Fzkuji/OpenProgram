"""The Windows App smoke proves the window becomes usable, not just the worker.

1.2.0's rehearsal passed with a window stuck on "Waiting for backend to
start...": the smoke only asked /healthz. The window gets its signed-in
address from `web auth-url`, so the smoke runs that command, asks the App's
own DevTools port which page the window shows, and reopens the App with the
worker still running to prove the worker did not keep the App's socket.
"""
from __future__ import annotations

from pathlib import Path

SMOKE = Path(__file__).resolve().parents[3] / "scripts/release/smoke-windows-desktop.ps1"


def test_the_smoke_requires_a_signed_in_window_on_first_start_and_after_reopening():
    text = SMOKE.read_text(encoding="utf-8")
    text.encode("ascii")  # Windows PowerShell 5.1 reads it the same as 7
    assert 'web auth-url --base-url "http://127.0.0.1:$Port"' in text
    assert "#token=[A-Za-z0-9_-]{43}$" in text
    assert 'worker status' in text and r"running \(PID \d+" in text
    assert 'Invoke-RestMethod -Uri "http://127.0.0.1:$DebugPort/json"' in text
    assert '-like "http://127.0.0.1:$Port/chat*"' in text
    first = text.index('Wait-AppChat "the first start"')
    closed = text.index("Get-NetTCPConnection -LocalPort $DebugPort -State Listen", first)
    reopened = text.index('Wait-AppChat "the reopened App"', closed)
    assert first < closed < reopened
    assert '(Join-Path $State "web-port")' in text
