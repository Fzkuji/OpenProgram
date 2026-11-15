import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import test from "node:test";

// attention.ts reads the session store, the sidebar title helper and the
// preload bridge. Each is replaced by a small stand-in the test drives.
const stub = (source) => `data:text/javascript,${encodeURIComponent(source)}`;
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "@/lib/session-store") {
      return { url: stub(`
        const listeners = new Set();
        let state = { runningTasks: {}, conversations: { s1: { title: "Fix the login bug" } } };
        export const useSessionStore = {
          getState: () => state,
          setState(patch) { state = { ...state, ...patch }; for (const l of listeners) l(state); },
          subscribe(l) { listeners.add(l); return () => listeners.delete(l); },
        };`), shortCircuit: true };
    }
    if (specifier === "@/components/sidebar/sessions/helpers") {
      return { url: stub(`export const displayTitle = (c) => (c.title || "").trim();`), shortCircuit: true };
    }
    if (specifier === "@/lib/desktop/bridge/bridge-api") {
      return { url: stub(`export const desktopBridge = () => globalThis.window?.openprogramDesktop ?? null;`), shortCircuit: true };
    }
    if (specifier === "@/lib/i18n") {
      return { url: new URL("../../lib/i18n/index.ts", import.meta.url).href, shortCircuit: true };
    }
    return nextResolve(specifier, context);
  },
});

const notified = [];
const busy = [];
let openListener = null;
globalThis.window = {
  openprogramDesktop: {
    attention: {
      notify: (request) => notified.push(request),
      reportBusy: (value) => busy.push(value),
      onOpenSession: (cb) => { openListener = cb; return () => { openListener = null; }; },
    },
  },
};

const { useSessionStore } = await import("@/lib/session-store");
const attention = await import("../../lib/desktop/attention.ts");

test("a watched turn that completes notifies once, with the session title", () => {
  const opened = [];
  const stop = attention.startAttentionBridge((id) => opened.push(id));
  assert.deepEqual(busy, [false]);
  useSessionStore.setState({ runningTasks: { s1: { session_id: "s1", msg_id: "m", execution_id: "e1" } } });
  assert.deepEqual(busy, [false, true], "keep-awake follows the first running session");
  // The running-task clear may land before the terminal status frame.
  useSessionStore.setState({ runningTasks: {} });
  assert.deepEqual(busy, [false, true, false]);
  attention.reportExecutionStatus("e1", "s1", "completed");
  attention.reportExecutionStatus("e1", "s1", "completed");
  assert.equal(notified.length, 1);
  assert.equal(notified[0].title, "Fix the login bug");
  assert.equal(notified[0].sessionId, "s1");
  assert.equal(notified[0].key, "execution:e1:completed");
  openListener("s1");
  assert.deepEqual(opened, ["s1"], "a clicked notification opens its session");
  stop();
  assert.equal(openListener, null);
});

test("unwatched, cancelled and interrupted executions stay quiet", () => {
  const before = notified.length;
  attention.reportExecutionStatus("never-seen", "s1", "completed");
  const stop = attention.startAttentionBridge(() => {});
  useSessionStore.setState({ runningTasks: { s1: { session_id: "s1", msg_id: "m", execution_id: "e2" } } });
  attention.reportExecutionStatus("e2", "s1", "cancelled");
  attention.reportExecutionStatus("e2", "s1", "completed");
  assert.equal(notified.length, before, "after a Stop the same execution does not notify later");
  stop();
});

test("failed turns and questions carry what happened", () => {
  const stop = attention.startAttentionBridge(() => {});
  useSessionStore.setState({ runningTasks: { s1: { session_id: "s1", msg_id: "m", execution_id: "e3" } } });
  attention.reportExecutionStatus("e3", "s1", "failed");
  assert.equal(notified.at(-1).key, "execution:e3:failed");
  attention.reportAttention({ kind: "needs_input", sessionId: "s1", key: "question:q1", detail: "Deploy now?" });
  attention.reportAttention({ kind: "needs_input", sessionId: "s1", key: "question:q1", detail: "Deploy now?" });
  assert.equal(notified.at(-1).key, "question:q1");
  assert.match(notified.at(-1).body, /Deploy now\?/);
  assert.equal(notified.filter((n) => n.key === "question:q1").length, 1);
  attention.reportAttention({ kind: "system_access", sessionId: "unknown", key: "system-access:w1" });
  assert.equal(notified.at(-1).title, "OpenProgram", "a session without a loaded title falls back to the app name");
  stop();
});

test("outside the desktop shell nothing is reported", () => {
  const saved = globalThis.window.openprogramDesktop;
  globalThis.window.openprogramDesktop = undefined;
  const before = notified.length;
  attention.reportAttention({ kind: "done", sessionId: "s1", key: "outside" });
  const stop = attention.startAttentionBridge(() => {});
  stop();
  assert.equal(notified.length, before);
  globalThis.window.openprogramDesktop = saved;
});

test("an always-on report notifies with the Agent's name and the report title", () => {
  const before = notified.length;
  attention.reportAttention({ kind: "report", sessionId: "s1", key: "always-on-report:r1", title: "Scout", detail: "Found two broken links" });
  attention.reportAttention({ kind: "report", sessionId: "s1", key: "always-on-report:r1", title: "Scout", detail: "Found two broken links" });
  assert.equal(notified.length, before + 1, "a report notifies once");
  assert.equal(notified.at(-1).title, "Scout");
  assert.equal(notified.at(-1).body, "Found two broken links");
  assert.equal(notified.at(-1).sessionId, "s1");
});

test("a reminder notifies with its text even outside any conversation", () => {
  notified.length = 0;
  assert.equal(attention.reportAttention({ kind: "reminder", sessionId: "", key: "reminder:r1", title: "Reminder", detail: "Send the invoice" }), true);
  assert.equal(attention.reportAttention({ kind: "reminder", sessionId: "", key: "reminder:r1", title: "Reminder", detail: "Send the invoice" }), true,
    "a repeat still counts as shown, so the worker does not post it again");
  assert.equal(attention.reportAttention({ kind: "done", sessionId: "", key: "execution:x:completed" }), false);
  assert.deepEqual(notified, [{ key: "reminder:r1", title: "Reminder", body: "Send the invoice", sessionId: "", urgent: true }]);
});

test("an always-on report frame notifies unless the server says its kind does not", () => {
  const report = { id: "r1", session_id: "s1", title: "x", kind: "finding" };
  assert.equal(attention.reportNotifiesDesktop({ report }), true, "frames from before the rules notify");
  assert.equal(attention.reportNotifiesDesktop({ report, notify_desktop: false }), false);
  assert.equal(attention.reportNotifiesDesktop({ report, notify_desktop: true }), true);
  assert.equal(attention.reportNotifiesDesktop({ report, notify: { desktop: false, channels: true } }), false);
  assert.equal(attention.reportNotifiesDesktop({ report: { ...report, notify_desktop: false } }), false);
  assert.equal(attention.reportNotifiesDesktop(null), true);
});
