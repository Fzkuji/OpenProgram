import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";
import { notifyDesktopSessionLoaded } from "../lib/self-update-reopen.ts";

// Execute the actual direct statements in the WS session_loaded case. Other
// cases and asynchronous branch/settings refreshes do not acknowledge loading.
function sessionLoadedEntry(notify, loadSessionData) {
  const source = readFileSync(new URL("../lib/net/use-ws.ts", import.meta.url), "utf8");
  const file = ts.createSourceFile("use-ws.ts", source, ts.ScriptTarget.Latest, true);
  let clause;
  function visit(node) {
    if (ts.isCaseClause(node) && ts.isStringLiteral(node.expression) && node.expression.text === "session_loaded") clause = node;
    ts.forEachChild(node, visit);
  }
  visit(file);
  assert.ok(clause);
  const statements = clause.statements.filter(ts.isExpressionStatement).map((node) => node.getText(file)).join("\n");
  const code = ts.transpileModule(`(function(d) { ${statements} })`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
  }).outputText;
  return vm.runInNewContext(code, {
    clearHydratedTreePaths() {}, clearSessionByMsgId() {}, loadSessionData,
    notifyDesktopSessionLoaded: notify,
  });
}

test("WS session_loaded notifies Desktop only after transcript loading", () => {
  const events = [];
  const entry = sessionLoadedEntry((id) => events.push(["ack", id]), () => events.push(["load"]));
  entry({ id: "p1", messages: [] });
  assert.deepEqual(events, [["load"], ["ack", "p1"]]);
});

test("a failed transcript load never reports a loaded session", () => {
  const events = [];
  const entry = sessionLoadedEntry((id) => events.push(id), () => { throw new Error("load failed"); });
  assert.throws(() => entry({ id: "p1" }), /load failed/);
  assert.deepEqual(events, []);
});

test("only the focused main-window session is reported to preload", async () => {
  const calls = [];
  globalThis.window = {
    location: { pathname: "/s/p1" },
    openprogramDesktop: { windowId: "main", selfUpdateReopen: {
      sessionLoaded: async (id) => { calls.push(id); },
    } },
  };
  try {
    notifyDesktopSessionLoaded(null);
    notifyDesktopSessionLoaded("p2");
    notifyDesktopSessionLoaded("../p1");
    assert.deepEqual(calls, []);
    window.openprogramDesktop.windowId = "detached";
    notifyDesktopSessionLoaded("p1");
    assert.deepEqual(calls, []);
    window.openprogramDesktop.windowId = "main";
    notifyDesktopSessionLoaded("p1");
    assert.deepEqual(calls, ["p1"]);
    window.location.pathname = "/settings/general";
    notifyDesktopSessionLoaded("p1");
    assert.deepEqual(calls, ["p1"]);
    delete window.openprogramDesktop.selfUpdateReopen;
    window.location.pathname = "/s/p1";
    notifyDesktopSessionLoaded("p1");
    window.openprogramDesktop.selfUpdateReopen = { sessionLoaded: async () => { throw new Error("lost IPC"); } };
    notifyDesktopSessionLoaded("p1");
    await new Promise((resolve) => setImmediate(resolve));
  } finally { delete globalThis.window; }
  notifyDesktopSessionLoaded("p1"); // SSR and ordinary Web remain unaffected.
});
