import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import test from "node:test";
import { parseHTML } from "linkedom";

// Mount the production hook; replace only its session, transport and locale ports.
registerHooks({
  resolve(specifier, context, nextResolve) {
    const sources = {
      "../state/use-composer-settings": `export const useBoundComposerSettings = () => globalThis.permissionHarness.settings;
        export const useBoundSetComposerSettings = () => globalThis.permissionHarness.apply;`,
      "@/lib/session-store/session-scope": "export const useSessionScope = f => f({sid: globalThis.permissionHarness.sid});",
      "@/lib/net/ws-request": "export const wsRequest = (...args) => globalThis.permissionHarness.request(...args);",
      "@/lib/i18n": "export const useTranslation = () => ({text: en => en});",
    };
    if (specifier in sources) return { url: `data:text/javascript,${encodeURIComponent(sources[specifier])}`, shortCircuit: true };
    if (specifier === "@/lib/session-store/permission-state") return {
      url: new URL("../lib/session-store/permission-state.ts", import.meta.url).href, shortCircuit: true,
    };
    return nextResolve(specifier, context);
  },
});
const { window } = parseHTML("<html><body></body></html>");
globalThis.window = window;
globalThis.document = window.document;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { act, createElement } = await import("react");
const { createRoot } = await import("react-dom/client");
const { usePermissionMode } = await import("../components/chat/composer/controls/use-permission-mode.ts");

async function mounted(check, initial = { mode: "ask", version: 4 }) {
  const requests = [];
  let hook;
  const h = globalThis.permissionHarness = {
    sid: "session-one", settings: {},
    apply(patch) { Object.assign(h.settings, patch); },
    request(action, payload) {
      return new Promise(resolve => requests.push({ action, payload, resolve }));
    },
  };
  function Probe() { hook = usePermissionMode(); return null; }
  const host = document.createElement("div");
  const root = createRoot(host);
  try {
    await act(async () => root.render(createElement(Probe)));
    if (initial) await act(async () => requests.shift().resolve(initial));
    await check({ h, requests, hook: () => hook, root, Probe });
  } finally {
    await act(async () => root.unmount());
    delete globalThis.permissionHarness;
  }
}

test("confirmed selector sends bypass immediately and waits for write confirmation", async () => {
  await mounted(async ({ h, requests, hook }) => {
    await act(async () => hook().set("bypass"));
    assert.equal(requests.length, 1);
    assert.deepEqual(requests[0].payload, { session_id: "session-one", mode: "bypass", expected_version: 4 });
    assert.equal(hook().pending, true);
    assert.equal(hook().mode, "ask");
    await act(async () => requests.shift().resolve({ mode: "bypass", version: 5 }));
    assert.equal(hook().mode, "bypass");
    assert.equal(hook().pending, false);
    assert.equal(h.settings.permission_version, 5);
  });
});

test("a stale selector preserves the authoritative conflict without retrying", async () => {
  await mounted(async ({ requests, hook }) => {
    await act(async () => hook().set("bypass"));
    await act(async () => requests.shift().resolve({ mode: "plan", version: 5, error: "permission_version_conflict" }));
    assert.equal(hook().mode, "plan");
    assert.match(hook().error, /Another window/);
    assert.equal(requests.length, 0);
  });
});

test("unconfirmed sessions read before writing and ignore stale completion after switching sessions", async () => {
  await mounted(async ({ h, requests, hook, root, Probe }) => {
    await act(async () => hook().set("bypass"));
    assert.deepEqual(requests[1].payload, { session_id: "session-one" });
    await act(async () => requests.splice(1, 1)[0].resolve({ mode: "ask", version: 2 }));
    assert.equal(requests[1].payload.expected_version, 2);
    h.sid = "session-two";
    h.settings = {};
    await act(async () => root.render(createElement(Probe)));
    await act(async () => requests[1].resolve({ mode: "bypass", version: 3 }));
    assert.equal(hook().mode, "ask");
    assert.deepEqual(h.settings, {});
  }, null);
});

test("an unsent draft keeps its selection local", async () => {
  await mounted(async ({ requests, hook }) => {
    await act(async () => hook().set("bypass"));
    await act(async () => requests.shift().resolve({ error: "session_not_found" }));
    assert.equal(hook().mode, "bypass");
    assert.equal(hook().pending, false);
    assert.equal(requests.length, 0);
  }, { error: "session_not_found" });
});

test("a missing acknowledgement leaves the previous mode effective", async () => {
  await mounted(async ({ requests, hook }) => {
    await act(async () => hook().set("bypass"));
    await act(async () => requests.shift().resolve(null));
    assert.equal(hook().mode, "ask");
    assert.equal(hook().pending, false);
    assert.ok(hook().error);
  });
});
