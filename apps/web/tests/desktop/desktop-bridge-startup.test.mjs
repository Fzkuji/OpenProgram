import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import test from "node:test";
import { fileURLToPath } from "node:url";

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "@/lib/session-store") {
      return {
        url: new URL("../../lib/session-store/index.ts", import.meta.url).href,
        shortCircuit: true,
      };
    }
    if (specifier.startsWith("@/")) {
      return {
        url: new URL(`../../${specifier.slice(2)}.ts`, import.meta.url).href,
        shortCircuit: true,
      };
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

const listeners = new Map();
const storage = new Map();
globalThis.window = {
  addEventListener(type, handler) {
    listeners.set(type, handler);
  },
  dispatchEvent(event) {
    listeners.get(event.type)?.(event);
  },
  location: { pathname: "/s/origin", hash: "" },
};
globalThis.localStorage = {
  getItem: (key) => storage.get(key) ?? null,
  setItem: (key, value) => storage.set(key, String(value)),
  removeItem: (key) => storage.delete(key),
};
globalThis.WebSocket = { OPEN: 1 };

const { setSocket } = await import("../../lib/runtime-bridge/state.ts");
const { installDesktopMenuHandlers } = await import("../../lib/desktop/desktop-bridge.ts");

function transferStub() {
  const unsubscribe = () => {};
  return {
    onRemoveSource: () => unsubscribe,
    onUndoDestination: () => unsubscribe,
    onCommitted: () => unsubscribe,
    onRejected: () => unsubscribe,
    onRolledBack: () => unsubscribe,
    onFinalizeOrphaned: () => unsubscribe,
    onStageIncoming: () => unsubscribe,
    // Keep startup recovery pending. Registration must not wait for it.
    pendingTerminal: () => new Promise(() => {}),
    claimPending: async () => null,
  };
}

const sent = [];
window.openprogramDesktop = {
  isDesktop: true,
  windowId: "detached-window",
  openExternal() {},
  webTab: {
    onState: () => () => {},
    onPopup: () => () => {},
  },
  tabTransfer: transferStub(),
  updates: {},
};
setSocket({
  readyState: WebSocket.OPEN,
  send(payload) {
    sent.push(JSON.parse(payload));
  },
});

test("desktop window registers before startup transfer and page restoration finish", () => {
  installDesktopMenuHandlers();
  assert.deepEqual(sent, [{
    action: "webtab_register",
    window_id: "detached-window",
  }]);

  sent.length = 0;
  listeners.get("op:browser-connection")?.({
    type: "op:browser-connection",
    detail: { connected: true },
  });
  assert.deepEqual(sent, [{
    action: "webtab_register",
    window_id: "detached-window",
  }]);
});
