import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");
const sidebar = read("../components/right-sidebar/right-sidebar.tsx");
const sessionDebugger = read("../components/right-sidebar/session-debugger.tsx");
const running = read("../components/right-sidebar/running-panel.tsx");
const panel = read("../components/right-sidebar/debugger-panel.tsx");
const hook = read("../lib/use-execution-debugger.ts");
const client = read("../lib/net/execution-client.ts");
const ws = read("../lib/net/use-ws.ts");
const viewHost = read("../app/styles/right-dock/view-host.css");

test("running execution cards open the canonical debugger view", () => {
  assert.match(sidebar, /VIEW_DEBUGGER/);
  assert.match(sidebar, /<SessionDebugger/);
  assert.match(sessionDebugger, /<DebuggerPanel/);
  assert.match(sidebar, /onOpenExecution=\{openDebugger\}/);
  assert.match(running, /Open execution debugger/);
  assert.match(viewHost, /data-view="debugger"/);
});

test("debugger refreshes authorized conversation snapshots and persisted events", () => {
  assert.match(hook, /getSessionExecutions/);
  assert.match(hook, /getExecutionEvents/);
  assert.match(hook, /op:execution-update/);
  assert.match(client, /\/api\/execution\/\$\{encodeURIComponent\(executionId\)\}/);
  assert.match(client, /events\?after_sequence=/);
});

test("debugger renders its empty state before reading execution fields", () => {
  const emptyGuard = panel.indexOf("if (!snapshot)");
  const actionPayloads = panel.indexOf("const actionPayloads");
  assert.ok(emptyGuard >= 0, "debugger must guard an empty execution selection");
  assert.ok(actionPayloads >= 0, "debugger must build canonical action payloads");
  assert.ok(
    emptyGuard < actionPayloads,
    "empty execution state must return before action payloads read snapshot fields",
  );
  assert.match(panel, /No executions in this conversation/);
  assert.doesNotMatch(
    panel,
    /executions\.find\(\(execution\) => execution\.execution_id === selectedId\) \|\| executions\[0\]/,
    "an invalid explicit selection must not silently select another execution",
  );
});

test("debugger actions use the single execution command and wait clients", () => {
  assert.match(panel, /steerValue\.trim\(\) \? \{ message:/);
  assert.match(hook, /postExecutionCommand/);
  assert.match(hook, /postExecutionWait/);
  assert.match(hook, /postRevisionDraftCommand/);
  assert.match(client, /pathOperation/);
  assert.match(client, /wait\/\$\{operation\.slice/);
});

test("websocket execution updates are published to the debugger subscriber", () => {
  assert.match(ws, /new CustomEvent\("op:execution-update"/);
  assert.match(ws, /case "execution\.replay"/);
});
