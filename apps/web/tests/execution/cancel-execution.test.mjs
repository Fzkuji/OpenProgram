// A cancel goes out once. The Worker takes a stop at the execution's current
// version whatever version the page last saw, so there is no stale refusal
// to answer (lib/execution/cancel-execution.ts). Covers the sub-agent row's
// Cancel (WebSocket), the Activity controls (REST), and a repeated stop:
// "Stopped." waits for the execution to end, and a repeat that the Worker
// answers with the stop already in force leaves the drawn stop alone.
import assert from "node:assert/strict";
import { afterEach, describe, test } from "node:test";
import { installLoader } from "../_support/load.mjs";
import { installDom, render } from "../_support/dom.mjs";

installLoader({
  stubs: {
    "@/components/solar-icons": `import { createElement } from "react";
      export const SolarIcon = ({ name }) => createElement("i", { "data-glyph": name });`,
  },
});
installDom({ pathname: "/chat" });

class FakeSocket {
  readyState = 1;
  frames = [];
  send(raw) { this.frames.push(JSON.parse(raw)); }
}
globalThis.WebSocket ??= { OPEN: 1 };
globalThis.WebSocket.OPEN ??= 1;

const { createElement: h } = await import("react");
const { runtimeState, setSocket } = await import("../../lib/runtime-bridge/state.ts");
const { handleExecutionCommandUpdated } = await import("../../lib/runtime-bridge/session/chat-handlers.ts");
const { SubAgentStep } = await import("../../components/chat/messages/execution/execution-strip.tsx");
const { useExecutionDebugger } = await import("../../lib/execution/use-execution-debugger.ts");
const cancelModule = await import("../../lib/execution/cancel-execution.ts");
const { useSessionStore } = await import("../../lib/session-store/index.ts");

let view;
afterEach(async () => {
  await view?.unmount();
  view = undefined;
});

const shown = [];
window.addEventListener("op:toast", (event) => shown.push(String(event.detail?.message || "")));
const toasts = () => shown.slice();

describe("sub-agent Cancel", () => {
  test("a Cancel goes out once, whatever the Worker answers", async () => {
    const socket = new FakeSocket();
    setSocket(socket);
    view = await render(h(SubAgentStep, { card: {
      id: "card-1", role: "assistant", content: "", timestamp: 1_700_000_000_000,
      attach: { session_id: "s1", head_id: "head-1", label: "probe", status: "running", execution_id: "job-1", status_version: 1 },
    } }));
    const cancel = view.container.querySelector('button[aria-label="Cancel execution"]');
    await view.act(() => cancel.click());
    assert.equal(socket.frames.length, 1);
    assert.equal(socket.frames[0].action, "execution.cancel");

    handleExecutionCommandUpdated({ command: {
      command_id: socket.frames[0].command_id, execution_id: "job-1", status: "accepted", result_version: 4,
    } });

    assert.equal(socket.frames.length, 1);
  });
});

describe("Activity Cancel (REST)", () => {
  test("a cancel is posted once", async () => {
    const posts = [];
    globalThis.fetch = async (url, init) => {
      if (init?.method === "POST" && String(url).endsWith("/api/execution/cancel")) {
        const body = JSON.parse(init.body);
        posts.push(body);
        return new Response(JSON.stringify({ command: { command_id: body.command_id, status: "accepted" } }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      return new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "Content-Type": "application/json" } });
    };
    let controller;
    function Probe() { controller = useExecutionDebugger(false, "s1"); return null; }
    view = await render(h(Probe));
    const result = await controller.command({
      type: "execution.command", action: "execution.cancel", command_id: "rest-stop",
      execution_id: "exec-1", expected_version: 1, payload: {},
    });
    assert.deepEqual(posts.map((post) => [post.command_id, post.expected_version]), [["rest-stop", 1]]);
    assert.equal(result.status, "accepted");
  });
});

describe("the helper", () => {
  test("keeps no resend machinery", () => {
    assert.deepEqual(Object.keys(cancelModule).sort(), ["executionCancelCommand", "sendExecutionCancel", "sendRequestCancel"]);
  });

  test("a stop before the ack names the send's request, once", () => {
    const frames = [];
    assert.equal(cancelModule.sendRequestCancel((frame) => { frames.push(frame); return true; }, "s1", "req-1", "stop-1"), true);
    assert.deepEqual(frames, [{
      type: "execution.command", action: "execution.cancel", command_id: "stop-1",
      session_id: "s1", client_request_id: "req-1", payload: {},
    }]);
    assert.equal(cancelModule.sendRequestCancel(() => { throw new Error("closed"); }, "s1", "req-1", "stop-2"), false);
  });

  test("a transport that throws or refuses reports that nothing went out", () => {
    assert.equal(cancelModule.sendExecutionCancel(() => { throw new Error("closed"); }, "exec-2", 1, "a"), false);
    assert.equal(cancelModule.sendExecutionCancel(() => false, "exec-2", 1, "b"), false);
    const frames = [];
    assert.equal(cancelModule.sendExecutionCancel((frame) => { frames.push(frame); return true; }, "exec-2", 1, "c"), true);
    assert.equal(frames.length, 1);
  });
});

describe("a repeated stop", () => {
  test("an accepted repeat neither says Stopped. nor clears the drawn stop", () => {
    const sid = "s-repeat";
    const store = useSessionStore.getState();
    const changes = [];
    const apply = store.applyLocalChange;
    useSessionStore.setState({ applyLocalChange: (session, change) => { changes.push(change); return apply(session, change); } });
    try {
      store.setRunningTaskFor(sid, { session_id: sid, msg_id: "m", execution_id: "exec-r", status_version: 3 });
      runtimeState._optimisticCancels["second"] = {
        sessionId: sid, task: { session_id: sid, msg_id: "m", execution_id: "exec-r", status_version: 3 },
      };
      const before = toasts().filter((text) => text.includes("Stopped.")).length;
      // The Worker answers the repeat with the stop already in force.
      handleExecutionCommandUpdated({ command: {
        command_id: "first", execution_id: "exec-r", status: "applying", result_version: null,
        latest_snapshot: { execution_id: "exec-r", session_id: sid, status: "cancelling", status_version: 4 },
      } });
      handleExecutionCommandUpdated({ command: {
        command_id: "second", execution_id: "exec-r", status: "accepted", result_version: 4,
      } });
      handleExecutionCommandUpdated({ command: {
        command_id: "second", execution_id: "exec-r", status: "applied", result_version: 4,
      } });
      assert.equal(toasts().filter((text) => text.includes("Stopped.")).length, before);
      assert.equal(changes.filter((change) => change.kind === "overlay_command_rejected").length, 0);
      assert.ok(runtimeState._optimisticCancels["second"], "the stop waits for the execution to end");
    } finally {
      useSessionStore.setState({ applyLocalChange: apply });
      delete runtimeState._optimisticCancels["second"];
    }
  });

  test("a stop of an execution that already ended is not a failure", () => {
    const sid = "s-ended";
    runtimeState._optimisticCancels["late"] = {
      sessionId: sid, task: { session_id: sid, msg_id: "m", execution_id: "exec-e", status_version: 3 },
    };
    const before = toasts().filter((text) => text.includes("failed")).length;
    handleExecutionCommandUpdated({ command: {
      command_id: "late", execution_id: "exec-e", status: "already_terminal", result_version: 5,
      latest_snapshot: { execution_id: "exec-e", session_id: sid, status: "cancelled", status_version: 5 },
    } });
    assert.equal(toasts().filter((text) => text.includes("failed")).length, before);
    assert.equal(runtimeState._optimisticCancels["late"], undefined);
  });
});
