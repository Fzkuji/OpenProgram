import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import test from "node:test";
import { fileURLToPath } from "node:url";

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "@/lib/session-store") {
      return { url: new URL("../../lib/session-store/index.ts", import.meta.url).href, shortCircuit: true };
    }
    if (specifier.startsWith("@/")) {
      return { url: new URL(`../../${specifier.slice(2)}.ts`, import.meta.url).href, shortCircuit: true };
    }
    if (specifier.startsWith(".") && !/\.[a-z]+$/.test(specifier)) {
      const base = new URL(specifier, context.parentURL).href;
      const file = `${base}.ts`;
      const url = existsSync(fileURLToPath(file)) ? file : `${base}/index.ts`;
      return { url, shortCircuit: true };
    }
    return nextResolve(specifier, context);
  },
});

const { useSessionStore } = await import("@/lib/session-store");

test("a session load after the terminal frame doesn't mark the finished execution running again", () => {
  const store = useSessionStore.getState();
  store.setRunningTaskFor("s1", { session_id: "s1", msg_id: "m1", execution_id: "e1" }, "never");
  assert.ok(store.acceptExecutionUpdate("e1", 5, "completed", "s1", []));
  useSessionStore.getState().setRunningTaskFor("s1", null, "never");
  // The server still holds the session for a moment and reports e1 running.
  useSessionStore.getState().setRunningTaskFor("s1", { session_id: "s1", msg_id: "m1", execution_id: "e1" }, "never");
  assert.equal(useSessionStore.getState().runningTasks.s1, undefined);
  // A new execution in the same session still runs.
  useSessionStore.getState().setRunningTaskFor("s1", { session_id: "s1", msg_id: "m2", execution_id: "e2" }, "never");
  assert.equal(useSessionStore.getState().runningTasks.s1?.execution_id, "e2");
});
