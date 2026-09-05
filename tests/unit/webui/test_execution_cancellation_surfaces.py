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
    assert "const commandId = crypto.randomUUID()" in composer
    assert "command_id: commandId" in composer
    assert "expected_version: expectedVersion" in composer
    assert 'text("Cancel execution", "取消运行")' in index
    assert 'text("Cancelling…", "正在取消")' in index
    assert 'action: "execution.cancel"' in strip
    assert "command_id: crypto.randomUUID()" in strip
    assert "expected_version: expectedVersion" in strip
    assert 'text("Cancel execution", "取消运行")' in strip
    assert 'action: "execution.cancel"' in attach
    assert "command_id: crypto.randomUUID()" in attach
    assert "expected_version: expectedVersion" in attach
    assert "mode: 'force'" not in tui
    assert "mode=\"force\"" not in tui
    assert "Cancel execution" in tui
    assert "action: 'execution.cancel'" in tui
    assert "command_id: randomLocalId()" in tui
    assert "expected_version: expectedVersion" in tui
    assert "execution_id: streaming.id" not in tui
    assert "executionIdRef.current" in tui
    assert "execution.updated" in tui_events
    assert "ev.data.execution_id" in tui_events
    assert '"cancel": "/api/execution/cancel"' in cli_cmd
    assert "_EXECUTION_CONTROL_PATHS[operation]" in cli_cmd
    assert "from openprogram.agent.run_control import" not in cli_cmd
    assert "execution_id=resolved_execution_id" in forced
    route = (
        ROOT / "apps/server/openprogram_server/_webui/routes/chat.py"
    ).read_text(encoding="utf-8")
    assert '"kind": "forced_tool"' in route
    assert '"execution_id": execution_id' in route
    assert 'dest="execution_verb"' in parser
    assert '("cancel", "Cancel one execution")' in parser
    assert "execution_id" in parser
    assert "submit_execution_control" in runtime
    assert 'cmd.get("mode") == "force"' not in runtime
    assert "handle_stop" not in runtime
    assert '"stop":' not in runtime
    lifecycle = (
        ROOT / "apps/server/openprogram_server/_webui/routes/lifecycle.py"
    ).read_text(encoding="utf-8")
    assert (
        'for operation in ("pause", "continue", "step", "steer", "cancel", "fork", "retry")'
        in lifecycle
    )
    assert 'app.post(f"/api/execution/{operation}")(endpoint)' in lifecycle
    assert "submit_execution_control" in lifecycle
    assert "cancel_canonical_execution" not in lifecycle
    assert '@app.post("/api/pause")' not in lifecycle
    assert '@app.post("/api/resume")' not in lifecycle
    assert '@app.post("/api/stop")' not in lifecycle
    run_control = (
        ROOT / "openprogram/agent/run_control.py"
    ).read_text(encoding="utf-8")
    assert "def pause_execution" not in run_control
    assert "def resume_execution" not in run_control
    assert "_pause_event" not in run_control
    assert "msg_id}_reply" not in composer
    assert '"execution_id": task.get("execution_id")' in (
        ROOT / "apps/server/openprogram_server/server.py"
    ).read_text(encoding="utf-8")
    assert '"status_version": task.get("status_version")' in (
        ROOT / "apps/server/openprogram_server/server.py"
    ).read_text(encoding="utf-8")
    assert "cancelling: true" not in composer
    stop_body = composer.split("export function stopSession", 1)[1]
    assert 'status: "cancelled"' in stop_body
    assert 'setRunningTaskFor(targetSessionId, null, "always")' in stop_body
