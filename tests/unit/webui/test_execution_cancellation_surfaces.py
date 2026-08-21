"""Structural checks that every surface uses execution.cancel."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_surfaces_send_execution_cancel_and_use_cancel_copy():
    composer = (
        ROOT / "apps/web/components/chat/composer/submit/use-chat-submit.ts"
    ).read_text(encoding="utf-8")
    index = (
        ROOT / "apps/web/components/chat/composer/index.tsx"
    ).read_text(encoding="utf-8")
    strip = (
        ROOT / "apps/web/components/chat/messages/execution-strip.tsx"
    ).read_text(encoding="utf-8")
    attach = (
        ROOT / "apps/web/components/chat/messages/attach-card.tsx"
    ).read_text(encoding="utf-8")
    tui = (ROOT / "apps/cli/src/screens/REPL.tsx").read_text(encoding="utf-8")
    parser = (
        ROOT / "apps/cli/python/openprogram_cli/_impl/parser.py"
    ).read_text(encoding="utf-8")
    runtime = (
        ROOT / "apps/server/openprogram_server/_webui/ws_actions/runtime.py"
    ).read_text(encoding="utf-8")
    cli_cmd = (
        ROOT / "apps/cli/python/openprogram_cli/_impl/commands/execution.py"
    ).read_text(encoding="utf-8")
    tui_events = (
        ROOT / "apps/cli/src/screens/repl/useWsEvents.ts"
    ).read_text(encoding="utf-8")
    forced = (
        ROOT / "openprogram/agent/dispatcher/forced_tool.py"
    ).read_text(encoding="utf-8")

    assert 'action: "execution.cancel"' in composer
    assert "execution_id" in composer
    assert 'text("Cancel execution", "取消运行")' in index
    assert 'text("Cancelling…", "正在取消")' in index
    assert 'action: "execution.cancel"' in strip
    assert 'text("Cancel execution", "取消运行")' in strip
    assert 'action: "execution.cancel"' in attach
    assert "mode: 'force'" not in tui
    assert "mode=\"force\"" not in tui
    assert "Cancel execution" in tui
    assert "action: 'execution.cancel'" in tui
    assert "execution_id: streaming.id" not in tui
    assert "executionIdRef.current" in tui
    assert "execution.updated" in tui_events
    assert "ev.data.execution_id" in tui_events
    assert "/api/execution/cancel" in cli_cmd
    assert "from openprogram.agent.run_control import" not in cli_cmd
    assert "execution_id=resolved_execution_id" in forced
    assert "execution_id=_forced_node_id" in (
        ROOT / "apps/server/openprogram_server/_webui/routes/chat.py"
    ).read_text(encoding="utf-8")
    assert 'dest="execution_verb"' in parser
    assert '"cancel", help="Cancel one execution by id"' in parser
    assert "execution_id" in parser
    assert "cancel_execution" in runtime
    assert 'cmd.get("mode") == "force"' not in runtime
    lifecycle = (
        ROOT / "apps/server/openprogram_server/_webui/routes/lifecycle.py"
    ).read_text(encoding="utf-8")
    assert '@app.post("/api/execution/cancel")' in lifecycle
    assert '"execution_id": task.get("execution_id")' in (
        ROOT / "apps/server/openprogram_server/server.py"
    ).read_text(encoding="utf-8")
    assert "cancelling: true" not in composer
    stop_body = composer.split("export function stopSession")[1][:1600]
    assert 'status: "cancelled"' in stop_body
    assert 'setRunningTaskFor(targetSessionId, null, "always")' in stop_body
