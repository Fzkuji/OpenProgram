import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { parseHTML } from "linkedom";

const webRoot = new URL("../../../", import.meta.url);
registerHooks({
  resolve(specifier, context, nextResolve) {
    const base = specifier.startsWith("@/")
      ? new URL(specifier.slice(2), webRoot).href
      : specifier.startsWith(".") && !/\.[a-z]+$/i.test(specifier) ? new URL(specifier, context.parentURL).href : null;
    if (base) {
      for (const suffix of [".ts", ".tsx", "/index.ts", "/index.tsx"]) {
        if (existsSync(fileURLToPath(base + suffix))) return { url: base + suffix, shortCircuit: true };
      }
    }
    return nextResolve(specifier, context);
  },
});
const { window } = parseHTML("<!doctype html><html><body></body></html>");
Object.assign(globalThis, { window, document: window.document, Event: window.Event, CustomEvent: window.CustomEvent });
globalThis.localStorage = { getItem() { return null; }, setItem() {}, removeItem() {} };
window.location = { pathname: "/chat", hash: "", search: "" };
window.history = { replaceState() {}, pushState() {} };

const { useSessionStore } = await import("../../../lib/session-store/index.ts");
const { dispatchChanged } = await import("../../../lib/net/ws/changes.ts");
const { activityReadIsFresh, activityReadStart, forgetActivityReads, lastActivityRead, rememberActivityRead } = await import("../../../lib/execution/activity-reads.ts");

test("a kept read stops being fresh when the Worker says the runs changed, but stays on screen", () => {
  useSessionStore.setState({ wsStatus: "open" });
  try {
    rememberActivityRead("runs", "session", { runs: ["a"] }, activityReadStart());
    assert.equal(activityReadIsFresh("runs", "session"), true);
    dispatchChanged("runs", ["session"]);
    assert.equal(activityReadIsFresh("runs", "session"), false);
    assert.deepEqual(lastActivityRead("runs", "session"), { runs: ["a"] });
  } finally { forgetActivityReads(); }
});
