import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");
const sidebar = read("../components/right-sidebar/right-sidebar.tsx");
const running = read("../components/right-sidebar/running-panel.tsx");
const panel = read("../components/right-sidebar/debugger-panel.tsx");
const hook = read("../lib/use-execution-debugger.ts");
const client = read("../lib/net/execution-client.ts");
const ws = read("../lib/net/use-ws.ts");
const viewHost = read("../app/styles/right-dock/view-host.css");

test("running execution cards open the canonical debugger view", () => {
  assert.match(sidebar, /VIEW_DEBUGGER/);
  assert.match(sidebar, /<DebuggerPanel/);
  assert.match(sidebar, /onOpenExecution=\{openDebugger\}/);
  assert.match(running, /Open execution debugger/);
  assert.match(viewHost, /data-view="debugger"/);
});

test("debugger loads and recovers from authorized snapshots and cursor gaps", () => {
  assert.match(hook, /getRunningExecutions/);
  assert.match(hook, /getExecutionSnapshot/);
  assert.match(hook, /getExecutionEvents/);
  assert.match(hook, /reduceExecutionEvent/);
  assert.match(hook, /op:execution-update/);
  assert.match(client, /\/api\/execution\/\$\{encodeURIComponent\(executionId\)\}/);
  assert.match(client, /events\?after_sequence=/);
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
