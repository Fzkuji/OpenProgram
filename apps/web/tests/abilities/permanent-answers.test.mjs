// A read that fails is retried quietly, but a permanent answer (HTTP 4xx) is
// a fact the page acts on, and one failing source does not hold back the rest.
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { installLoader } from "../_support/load.mjs";

installLoader({});

const { forgetKept, isPermanent, keep, lastKept, readUntilAnswered, retrySoon } = await import("../../lib/abilities/kept-read.ts");
const { readAgentModels, watchAgentModels } = await import("../../lib/agents/agent-models.ts");
const { MemoryDraft, memoryDraft } = await import("../../components/memory/data/autosave.ts");
const { HttpError } = await import("../../lib/net/fetch-client.ts");

const httpError = (status) => new HttpError(`HTTP ${status}`, status);
const quietly = async (run) => {
  const warn = console.warn;
  console.warn = () => {};
  try { return await run(); } finally { console.warn = warn; }
};
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

afterEach(() => forgetKept());

test("a 4xx other than timeout and rate limit is permanent", () => {
  for (const status of [400, 403, 404, 410, 422]) assert.equal(isPermanent(httpError(status)), true, String(status));
  for (const status of [408, 425, 429, 500, 503]) assert.equal(isPermanent(httpError(status)), false, String(status));
  assert.equal(isPermanent(new Error("offline")), false);
});

test("readUntilAnswered stops on a permanent answer and forgets what it kept", async () => {
  keep("item", { name: "old" });
  let reads = 0;
  await quietly(() => assert.rejects(
    readUntilAnswered("item", async () => { reads += 1; throw httpError(404); }),
    (cause) => cause.status === 404,
  ));
  assert.equal(reads, 1);
  assert.equal(lastKept("item"), undefined);
});

test("readUntilAnswered keeps trying a read that failed for now", async () => {
  let reads = 0;
  const value = await quietly(() => readUntilAnswered("list", async () => {
    reads += 1;
    if (reads === 1) throw httpError(503);
    return ["a"];
  }));
  assert.deepEqual(value, ["a"]);
  assert.equal(reads, 2);
});

test("retrySoon schedules nothing for a permanent answer", async () => {
  let runs = 0;
  await quietly(() => retrySoon("store", () => { runs += 1; }, httpError(400)));
  await wait(600);
  assert.equal(runs, 0);
  await quietly(() => retrySoon("store", () => { runs += 1; }, httpError(502)));
  await wait(600);
  assert.equal(runs, 1);
});

function modelSources(answers) {
  return {
    readSettings: async () => ({ chat: { provider: "a", model: "a1" } }),
    fetchJson: async (url) => {
      if (url === "/api/providers/list") return { providers: Object.keys(answers).map((id) => ({ id })) };
      const id = decodeURIComponent(url.split("/")[3]);
      const answer = answers[id]();
      if (answer instanceof Error) throw answer;
      return answer;
    },
  };
}

test("the model list holds the providers that answered", async () => {
  const list = await quietly(() => readAgentModels(modelSources({
    a: () => ({ models: [{ id: "a1", name: "A1" }] }),
    b: () => new Error("connection reset"),
    c: () => httpError(404),
  })));
  assert.deepEqual(list.models.map((model) => `${model.provider}/${model.id}`), ["a/a1"]);
  assert.deepEqual(list.unanswered, ["b"]);
  assert.deepEqual(list.inherited, { provider: "a", id: "a1" });
});

test("the model list is read again until every provider answered", async () => {
  let bReads = 0;
  const sources = modelSources({
    a: () => ({ models: [{ id: "a1", name: "A1" }] }),
    b: () => (++bReads === 1 ? new Error("timeout") : { models: [{ id: "b1", name: "B1" }] }),
  });
  const shown = [];
  const controller = new AbortController();
  await quietly(async () => {
    watchAgentModels((list) => shown.push(list), controller.signal, () => readAgentModels(sources));
    await wait(800);
  });
  controller.abort();
  assert.equal(shown.length, 2);
  assert.deepEqual(shown[0].models.map((model) => model.id), ["a1"]);
  assert.deepEqual(shown[1].models.map((model) => model.id), ["a1", "b1"]);
  assert.deepEqual(shown[1].unanswered, []);
});

test("a memory file that is not there is gone, not read again", async () => {
  let reads = 0;
  const draft = new MemoryDraft("/gone", async () => { reads += 1; return { ok: false, status: 404, json: async () => ({}) }; });
  await quietly(() => draft.load());
  await wait(600);
  assert.equal(reads, 1);
  assert.equal(draft.state.gone, true);
  assert.equal(draft.state.loaded, false);
});

test("a memory file found gone is asked about afresh when opened again", async () => {
  const first = memoryDraft("/api/memory/topics/again.md");
  first.publish({ gone: true });
  const second = memoryDraft("/api/memory/topics/again.md");
  assert.notEqual(second, first);
  assert.equal(second.state.gone, false);
});

test("a prefetch is asked once: a failure schedules no retry", async () => {
  const { prefetchSkills, useSkills } = await import("../../lib/abilities/skills-store.ts");
  const { prefetchPlugins, usePluginsStore } = await import("../../lib/abilities/plugins-store.ts");
  const fetchBefore = globalThis.fetch;
  let reads = 0;
  globalThis.fetch = async () => { reads += 1; return new Response("down", { status: 503 }); };
  try {
    await quietly(async () => { await prefetchSkills(); await prefetchPlugins(); await wait(700); });
    assert.equal(reads, 2);
    assert.equal(useSkills.getState().ready, false);
    assert.equal(usePluginsStore.getState().ready, false);
    globalThis.fetch = async () => new Response(JSON.stringify([{ name: "s" }]), { status: 200 });
    await prefetchSkills();
    assert.equal(useSkills.getState().ready, true);
    assert.deepEqual(useSkills.getState().skills.map((skill) => skill.name), ["s"]);
  } finally {
    globalThis.fetch = fetchBefore;
  }
});
