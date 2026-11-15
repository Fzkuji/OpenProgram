"""Structural checks that every surface uses execution.cancel."""

from __future__ import annotations

from tests.support.desktop_source import read_server_source

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
        ROOT / "apps/web/components/chat/messages/execution/execution-strip.tsx"
    ).read_text(encoding="utf-8")
    attach = (
        ROOT / "apps/web/components/chat/messages/cards/attach-card.tsx"
    ).read_text(encoding="utf-8")
    tui = (ROOT / "apps/cli/src/screens/REPL.tsx").read_text(encoding="utf-8")
    parser = (
        ROOT / "openprogram/interfaces/cli/parser.py"
    ).read_text(encoding="utf-8")
    runtime = (
        ROOT / "openprogram/interfaces/server/ws_actions/runtime.py"
    ).read_text(encoding="utf-8")
    cli_cmd = (
        ROOT / "openprogram/interfaces/cli/commands/execution.py"
    ).read_text(encoding="utf-8")
    tui_events = (
        ROOT / "apps/cli/src/screens/repl/useWsEvents.ts"
    ).read_text(encoding="utf-8")
    forced = (
        ROOT / "openprogram/runtime/conversation/turn_runtime/forced_tool.py"
    ).read_text(encoding="utf-8")

    # The web builds every cancel frame in one helper; a stop names its
    # execution, so it goes out once.
    web_cancel = (
        ROOT / "apps/web/lib/execution/cancel-execution.ts"
    ).read_text(encoding="utf-8")
    assert 'action: "execution.cancel"' in web_cancel
    assert "command_id: commandId" in web_cancel
    assert "expected_version: expectedVersion" in web_cancel
    assert "const commandId = crypto.randomUUID()" in composer
    assert "sendExecutionCancel(send, executionId, typeof expectedVersion === \"number\" ? expectedVersion : 0, commandId)" in composer
    assert 'text("Cancel execution", "取消运行")' in index
    assert 'text("Cancelling…", "正在取消")' in index
    assert "sendExecutionCancel(sendRuntimeCommand, executionId, expectedVersion)" in strip
    assert 'text("Cancel execution", "取消运行")' in strip
    assert "sendExecutionCancel(sendRuntimeCommand, executionId, expectedVersion)" in attach
    assert "mode: 'force'" not in tui
    assert "mode=\"force\"" not in tui
    # The TUI builds its cancel frame in one helper, which also resends a
    # stop refused for a stale version; Ctrl-C and Esc both stop through
    # ``stopForeground``, which writes the one system line.
    tui_cancel = (
        ROOT / "apps/cli/src/screens/repl/cancelExecution.ts"
    ).read_text(encoding="utf-8")
    assert "pushSystem('Cancel execution')" in tui_cancel
    assert "action: 'execution.cancel' as const" in tui_cancel
    assert "command_id: commandId" in tui_cancel
    assert "expected_version: version" in tui_cancel
    assert "stopForeground({" in tui
    assert "cancelCurrentExecution(" in tui_cancel
    assert "newCommandId: randomLocalId," in tui
    assert "execution_id: streaming.id" not in tui
    assert "executionIdRef.current" in tui
    assert "execution.updated" in tui_events
    assert "ev.data.execution_id" in tui_events
    assert '"cancel": "/api/execution/cancel"' in cli_cmd
    assert "_EXECUTION_CONTROL_PATHS[operation]" in cli_cmd
    assert "from openprogram.runtime.conversation.run_control import" not in cli_cmd
    assert "execution_id=resolved_execution_id" in forced
    route = (
        ROOT / "openprogram/interfaces/server/routes/chat.py"
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
        ROOT / "openprogram/interfaces/server/routes/execution/lifecycle.py"
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
    run_control = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "openprogram/runtime/conversation/run_control").glob("*.py"))
    )
    assert "def pause_execution" not in run_control
    assert "def resume_execution" not in run_control
    assert "_pause_event" not in run_control
    assert "msg_id}_reply" not in composer
    assert '"execution_id": task.get("execution_id")' in read_server_source(ROOT)
    assert '"status_version": identity.get("status_version", task.get("status_version"))' in read_server_source(ROOT)
    assert "cancelling: true" not in composer
    stop_body = composer.split("export function stopSession", 1)[1]
    assert "stopOverlay" in stop_body
    assert 'setRunningTaskFor(targetSessionId, null, "always")' in stop_body
