// The context panel's stale-while-revalidate cache: once a session branch has
// loaded, reopening its panel shows that value at once while a fresh request
// runs in the background, and late or older responses never overwrite it.
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
  hasContextBreakdown,
  readContextBreakdownCache,
  refreshContextBreakdown,
  warmContextBreakdown,
  writeContextBreakdownCache,
} from "../../../lib/chat/context-breakdown-cache.ts";

const signal = () => new AbortController().signal;
const response = (data) => ({ json: async () => data });
/** A fetcher whose single response the test resolves by hand. */
function deferredFetcher() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { fetcher: () => promise, resolve: (data) => resolve(response(data)) };
}

describe("context breakdown cache", () => {
  test("an unknown head falls back to the session-latest snapshot; another session has none", () => {
    const snapshot = { total_used: 12_345, window: 200_000 };
    writeContextBreakdownCache("session-a", "head-a", snapshot);
    assert.deepEqual(readContextBreakdownCache("session-a", "head-a"), snapshot);
    assert.deepEqual(readContextBreakdownCache("session-a", "head-b"), snapshot);
    assert.equal(readContextBreakdownCache("session-b", "head-a"), null);
  });

  test("a snapshot standing in for another branch is not that branch's own", () => {
    // The stand-in was counted against the other branch's model and window,
    // so the ring still reads this branch's.
    writeContextBreakdownCache("session-own", "head-a", { total_used: 5_000, window: 200_000 });
    assert.equal(hasContextBreakdown("session-own", "head-a"), true);
    assert.equal(hasContextBreakdown("session-own", "head-b"), false);
    assert.ok(readContextBreakdownCache("session-own", "head-b"));
    assert.equal(hasContextBreakdown("session-own", null), true);
    assert.equal(hasContextBreakdown("session-none", "head-a"), false);
  });

  test("a failed background refresh returns the last successful value", async () => {
    const snapshot = { total_used: 13_000, window: 200_000 };
    writeContextBreakdownCache("session-c", "head-c", snapshot);
    const result = await refreshContextBreakdown("session-c", "head-c", signal(), async () => {
      throw new Error("offline");
    });
    assert.deepEqual(result, snapshot);
  });

  test("the refresh asks the session context endpoint with the head id", async () => {
    let url;
    await refreshContextBreakdown("s/1", "h 1", signal(), async (requested) => {
      url = requested;
      return response({ total_used: 1 });
    });
    assert.equal(url, "/api/sessions/s%2F1/context?head_id=h%201");
  });

  test("an aborted request neither publishes nor caches its late result", async () => {
    const late = deferredFetcher();
    const controller = new AbortController();
    const pending = refreshContextBreakdown("session-stale", "head-old", controller.signal, late.fetcher);
    controller.abort();
    late.resolve({ total_used: 99_999, window: 200_000 });
    assert.equal(await pending, null);
    assert.equal(readContextBreakdownCache("session-stale", "head-old"), null);
  });

  test("an older same-key response is discarded and does not overwrite the newer value", async () => {
    const older = deferredFetcher();
    const newer = deferredFetcher();
    const olderResult = refreshContextBreakdown("session-race", "head-race", signal(), older.fetcher);
    const newerResult = refreshContextBreakdown("session-race", "head-race", signal(), newer.fetcher);
    newer.resolve({ total_used: 200, window: 1_000 });
    assert.equal((await newerResult).total_used, 200);
    older.resolve({ total_used: 100, window: 1_000 });
    assert.equal(await olderResult, null);
    assert.equal(readContextBreakdownCache("session-race", "head-race").total_used, 200);
  });

  test("the 33rd value evicts the least-recently-used entry, and a read promotes", () => {
    for (let i = 0; i < 32; i += 1) writeContextBreakdownCache(`lru-${i}`, null, { total_used: i });
    assert.equal(readContextBreakdownCache("lru-0", null).total_used, 0);
    writeContextBreakdownCache("lru-32", null, { total_used: 32 });
    assert.equal(readContextBreakdownCache("lru-1", null), null);
    assert.equal(readContextBreakdownCache("lru-0", null).total_used, 0);
    assert.equal(readContextBreakdownCache("lru-2", null).total_used, 2);
    assert.equal(readContextBreakdownCache("lru-31", null).total_used, 31);
    assert.equal(readContextBreakdownCache("lru-32", null).total_used, 32);
  });

  test("LRU eviction does not reset same-key request ordering", async () => {
    const older = deferredFetcher();
    const newer = deferredFetcher();
    const olderResult = refreshContextBreakdown("session-evicted-race", "head", signal(), older.fetcher);
    for (let i = 0; i < 32; i += 1) writeContextBreakdownCache(`race-fill-${i}`, null, { total_used: i });
    const newerResult = refreshContextBreakdown("session-evicted-race", "head", signal(), newer.fetcher);
    newer.resolve({ total_used: 400, window: 1_000 });
    assert.equal((await newerResult).total_used, 400);
    older.resolve({ total_used: 300, window: 1_000 });
    assert.equal(await olderResult, null);
    assert.equal(readContextBreakdownCache("session-evicted-race", "head").total_used, 400);
  });
});

test("opening a conversation reads once, and a finished turn still reads again", async () => {
  const original = globalThis.fetch;
  let reads = 0;
  globalThis.fetch = async () => { reads += 1; return response({ total_used: 1 }); };
  const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
  try {
    warmContextBreakdown("focus-session", "h1", { focus: true });
    await settle();
    warmContextBreakdown("focus-session", "h1", { focus: true });
    await settle();
    assert.equal(reads, 1);
    warmContextBreakdown("focus-session", "h1");
    await settle();
    assert.equal(reads, 2);
  } finally {
    globalThis.fetch = original;
  }
});
