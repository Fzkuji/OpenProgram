// Settings > Channels: an account's status read that failed is tried again
// quietly with a growing wait until it arrives; a 4xx is the server's answer
// and is not asked again.
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { installLoader, STUBS } from "../../_support/load.mjs";

installLoader({ stubs: { "@/lib/i18n": STUBS.i18n } });

const { readStatusQuietly } = await import("../../../components/settings/channels/index.tsx");
const { invalidateAllSettings, readCachedSettings } = await import("../../../lib/prefs/settings-cache.ts");

const realFetch = globalThis.fetch;
const realWarn = console.warn;
afterEach(() => {
  globalThis.fetch = realFetch;
  console.warn = realWarn;
  invalidateAllSettings();
});

const URL = "/api/channels/telegram/bot1/status";

test("a failing status read retries 1 s, 2 s, 4 s and keeps the answer once it arrives", async () => {
  console.warn = () => {};
  const replies = [
    () => { throw new TypeError("network"); },
    () => ({ ok: false, status: 503 }),
    () => ({ ok: true, status: 200, json: async () => ({ state: "connected" }) }),
  ];
  let calls = 0;
  globalThis.fetch = async () => replies[calls++]();
  const waits = [];
  const arrived = await readStatusQuietly(URL, () => false, async (ms) => { waits.push(ms); });
  assert.equal(arrived, true);
  assert.deepEqual(waits, [1000, 2000, 4000]);
  assert.deepEqual(readCachedSettings(URL), { state: "connected" });
});

test("a 4xx stops the retries", async () => {
  console.warn = () => {};
  let calls = 0;
  globalThis.fetch = async () => { calls += 1; return { ok: false, status: 404 }; };
  const arrived = await readStatusQuietly(URL, () => false, async () => {});
  assert.equal(arrived, false);
  assert.equal(calls, 1);
  assert.equal(readCachedSettings(URL), undefined);
});

test("closing the page stops the retries", async () => {
  let calls = 0;
  globalThis.fetch = async () => { calls += 1; return { ok: false, status: 503 }; };
  const arrived = await readStatusQuietly(URL, () => true, async () => {});
  assert.equal(arrived, false);
  assert.equal(calls, 0);
});
