// The Settings read cache: a page shows the last answer at once, which stays
// fresh until the Worker says what it was read from changed; a failed read
// keeps that answer and is retried quietly while a page shows it; a change
// sends the next read to the network.
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { installLoader } from "../_support/load.mjs";
import { installDom, render } from "../_support/dom.mjs";

installLoader({});
installDom();

const { createElement: h } = await import("react");
const {
  cachedFetch, invalidate, invalidateAllSettings, readCachedSettings, refreshSettings, revalidateSettings, setCachedSettings, useSettingsData,
} = await import("../../lib/prefs/settings-cache.ts");

const realFetch = globalThis.fetch;
const realNow = Date.now;
const realWarn = console.warn;
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
afterEach(() => {
  globalThis.fetch = realFetch;
  Date.now = realNow;
  console.warn = realWarn;
  invalidateAllSettings();
  document.body.innerHTML = "";
});

test("a kept answer stays readable after it is old or invalidated; the next fetch goes to the network", async () => {
  let now = 1000, calls = 0;
  Date.now = () => now;
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ value: ++calls }) });
  assert.equal(readCachedSettings("/cache-test"), undefined);
  const first = await cachedFetch("/cache-test");
  assert.equal(readCachedSettings("/cache-test"), first);
  await cachedFetch("/cache-test");
  assert.equal(calls, 1, "fresh: no second request");
  now += 3_600_000;
  await cachedFetch("/cache-test");
  assert.equal(calls, 1, "age alone does not make it old");
  invalidate("/cache-test");
  assert.deepEqual(readCachedSettings("/cache-test"), first, "invalidated, still shown");
  await cachedFetch("/cache-test");
  assert.equal(calls, 2);
});

test("a change frame makes the answers read from its topic old, and a shown page reads again", async () => {
  const { dispatchChanged, dispatchReconnected } = await import("../../lib/net/ws/changes.ts");
  const calls = [];
  globalThis.fetch = async (url) => { calls.push(url); return { ok: true, json: async () => ({ n: calls.length }) }; };
  await cachedFetch("/api/providers/status");
  await cachedFetch("/api/voice/status");
  calls.length = 0;
  function Page() { const data = useSettingsData("/api/providers/status"); return h("p", null, data ? String(data.n) : ""); }
  const view = await render(h(Page));
  await settle();
  assert.deepEqual(calls, [], "a fresh answer is not read again on mount");
  dispatchChanged("routing", null);
  await settle();
  assert.deepEqual(calls, ["/api/providers/status"], "the shown page reads again; voice does not read routing");
  await cachedFetch("/api/voice/status");
  assert.equal(calls.length, 1);
  dispatchChanged("settings", null);
  await settle();
  assert.deepEqual(calls, ["/api/providers/status", "/api/providers/status"], "config changed: the shown page reads");
  await cachedFetch("/api/voice/status");
  assert.equal(calls.at(-1), "/api/voice/status", "and the unshown answer is read when next asked for");
  calls.length = 0;
  dispatchReconnected();
  await settle();
  assert.deepEqual(calls, ["/api/providers/status"], "a reconnect makes every answer old");
  view.unmount?.();
});

test("callers asking at the same moment share one request", async () => {
  let calls = 0;
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ n: ++calls }) });
  const [a, b] = await Promise.all([cachedFetch("/shared"), cachedFetch("/shared")]);
  assert.equal(calls, 1);
  assert.equal(a, b);
});

test("a page reading again as it opens joins a read on its way instead of sending a second one", async () => {
  let calls = 0;
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ n: ++calls }) });
  const opening = cachedFetch("/visit");
  assert.equal(await revalidateSettings("/visit"), true);
  await opening;
  assert.equal(calls, 1, "joined the read Settings opening started");
  assert.equal(await revalidateSettings("/visit"), true);
  assert.equal(calls, 2, "nothing on its way: reads again although the answer is fresh");
  assert.deepEqual(readCachedSettings("/visit"), { n: 2 });
});

test("a failed read keeps the last answer, is logged and never rejects a refresh", async () => {
  const warned = [];
  console.warn = (...args) => warned.push(args.join(" "));
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ v: 1 }) });
  await cachedFetch("/keep");
  globalThis.fetch = async () => ({ ok: false, status: 500, json: async () => ({ error: "OSError: catalogue unreadable" }) });
  assert.equal(await refreshSettings("/keep"), false);
  assert.deepEqual(readCachedSettings("/keep"), { v: 1 });
  assert.match(warned.join("\n"), /\/keep failed.*OSError: catalogue unreadable/s);
  await assert.rejects(cachedFetch("/keep"), /OSError: catalogue unreadable/);
});

function Probe({ url }) {
  const data = useSettingsData(url);
  return h("div", { "data-probe": data ? JSON.stringify(data) : "" });
}
const shown = (view) => view.container.querySelector("[data-probe]").getAttribute("data-probe");

test("a page mounting again shows the kept answer in its first render, with no request in between", async () => {
  let calls = 0;
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ name: "kept", n: ++calls }) });
  let view = await render(h(Probe, { url: "/page" }));
  await view.act(settle);
  assert.equal(shown(view), '{"name":"kept","n":1}');
  await view.unmount();

  // Remount: the answer is there before any request could answer.
  globalThis.fetch = () => new Promise(() => {});
  const started = performance.now();
  view = await render(h(Probe, { url: "/page" }));
  assert.equal(shown(view), '{"name":"kept","n":1}');
  assert.ok(performance.now() - started < 50, "drawn from memory");
  await view.unmount();
});

test("nothing is shown while the first read runs or after it fails; a quiet retry fills it in", async () => {
  console.warn = () => {};
  let fail = true;
  globalThis.fetch = async () => (fail
    ? { ok: false, status: 503, json: async () => ({}) }
    : { ok: true, json: async () => ({ ok: 1 }) });
  const view = await render(h(Probe, { url: "/retry" }));
  await view.act(settle);
  assert.equal(shown(view), "", "no data, no notice");
  assert.equal(view.container.textContent, "");
  fail = false;
  // The first retry waits one second.
  await view.act(() => new Promise((resolve) => setTimeout(resolve, 1100)));
  assert.equal(shown(view), '{"ok":1}');
  await view.unmount();
});

test("an answer a change returned redraws every page showing it", async () => {
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ v: 1 }) });
  const view = await render(h(Probe, { url: "/set" }));
  await view.act(settle);
  await view.act(() => setCachedSettings("/set", (prev) => ({ ...prev, v: 2 })));
  assert.equal(shown(view), '{"v":2}');
  await view.unmount();
});
