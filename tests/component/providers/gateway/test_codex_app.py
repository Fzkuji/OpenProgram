"""Finding the Codex app's own app-server and telling when Codex is quiet (after magpie)."""
import os
import time

from openprogram.models.gateway import codex_app


def test_only_an_apps_own_stdio_app_server_is_matched():
    app = "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
    assert codex_app.is_app_own_server(f"{app} -c features.x=true app-server --analytics-default-enabled")
    assert not codex_app.is_app_own_server(f"{app} app-server --listen stdio://")
    assert not codex_app.is_app_own_server(f"{app} exec hello")
    assert not codex_app.is_app_own_server("/Users/me/.local/bin/codex app-server")


def test_sessions_written_lately_are_not_quiet(tmp_path):
    day = tmp_path / "sessions" / "2026" / "11" / "25"
    day.mkdir(parents=True)
    rollout = day / "rollout.jsonl"
    rollout.write_text("{}")
    now = time.time()
    assert not codex_app.sessions_quiet(tmp_path, now=now)
    os.utime(rollout, (now - 60, now - 60))
    assert codex_app.sessions_quiet(tmp_path, now=now)
    assert codex_app.sessions_quiet(tmp_path / "missing", now=now)
