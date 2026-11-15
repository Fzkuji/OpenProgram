import assert from "node:assert/strict";
import test from "node:test";
import { createProgramsCache } from "../../components/programs/programs-cache.ts";

test("warm reads validate revision and retain identical data without rebuilding", async () => {
  const calls = [];
  const value = { entries: [{ path: "applications/a" }], revision: "r1" };
  const cache = createProgramsCache(async url => {
    calls.push(url);
    return calls.length === 1 ? value : { unchanged: true, revision: "r1" };
  });
  assert.equal(await cache.read("explorer", "applications"), value);
  assert.equal(cache.peek("explorer", "applications"), value);
  assert.equal(await cache.read("explorer", "applications"), value);
  assert.match(calls[1], /revision=r1/);
});

test("remount shares pending validation; aborted observer does not cancel another", async () => {
  let finish, requests = 0;
  const cache = createProgramsCache(() => { requests++; return new Promise(resolve => { finish = resolve; }); });
  const controller = new AbortController();
  const old = cache.read("logic", "a", controller.signal);
  controller.abort();
  const next = cache.read("logic", "a");
  finish({ revision: "r1", nodes: [] });
  await assert.rejects(old, { name: "AbortError" });
  assert.deepEqual(await next, { revision: "r1", nodes: [] });
  assert.equal(requests, 1);
});

test("failure preserves cache and a later changed result replaces only that entry", async () => {
  let response = { revision: "a", nodes: [1] };
  const cache = createProgramsCache(async () => { if (response instanceof Error) throw response; return response; });
  const original = await cache.read("logic", "a");
  response = new Error("offline");
  await assert.rejects(cache.read("logic", "a"));
  assert.equal(cache.peek("logic", "a"), original);
  response = { revision: "b", nodes: [2] };
  assert.equal(await cache.read("logic", "a"), response);
});
