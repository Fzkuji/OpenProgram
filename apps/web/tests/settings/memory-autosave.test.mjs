import test from "node:test";
import assert from "node:assert/strict";
import { MemoryDraft } from "../../components/memory/autosave.ts";

const storage = () => {
  const values = new Map();
  return { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
};
const reply = (content, extra = {}) => ({ ok: true, json: async () => ({ content, ...extra }) });
test("serializes saves and never marks newer typing saved by an earlier response", async () => {
  let resolve;
  const calls = [];
  const draft = new MemoryDraft("/note", async (url, init) => {
    if (!init) return reply("before");
    calls.push(JSON.parse(init.body));
    return new Promise(r => { resolve = r; });
  }, storage());
  await draft.load();
  draft.edit("first");
  const first = draft.flush();
  draft.edit("second");
  await draft.flush();
  assert.equal(calls.length, 1);
  resolve(reply("first"));
  await first;
  assert.equal(draft.state.content, "second");
  assert.equal(draft.state.base, "first");
  const second = draft.flush();
  assert.deepEqual(calls[1], { content: "second", base_content: "first", autosave: true });
  resolve(reply("second"));
  await second;
  assert.equal(draft.state.content, draft.state.base);
  assert.equal(draft.state.original, "before");
});
test("retains a rejected draft across reopening and exposes a Git warning", async () => {
  const local = storage();
  const draft = new MemoryDraft("/note", async (_url, init) => init
    ? { ok: false, json: async () => ({ error: "conflict" }) } : reply("before"), local);
  await draft.load();
  draft.edit("mine");
  await draft.flush();
  assert.equal(draft.state.error, "conflict");
  const reopened = new MemoryDraft("/note", async () => reply("elsewhere"), local);
  await reopened.load();
  assert.equal(reopened.state.content, "mine");
  assert.equal(reopened.state.base, "before");
  assert.match(reopened.state.error, /changed elsewhere/);
  const warned = new MemoryDraft("/other", async (_url, init) => reply(init ? "after" : "before", init ? { warning: "Git failed" } : {}), local);
  await warned.load();
  warned.edit("after"); await warned.flush();
  assert.equal(warned.state.warning, "Git failed");
});
test("Retry reloads after a failed initial read", async () => {
  let reads = 0;
  const draft = new MemoryDraft("/note", async () => ++reads === 1
    ? { ok: false } : reply("recovered"), storage());
  await draft.load();
  assert.equal(draft.state.loaded, false);
  draft.retry();
  await draft.loading;
  assert.equal(reads, 2);
  assert.equal(draft.state.content, "recovered");
  assert.equal(draft.state.error, "");
});
test("a late read never replaces editing or a newer completed save", async () => {
  let reads = 0, resolve;
  const draft = new MemoryDraft("/note", async (_url, init) => {
    if (init) return reply(JSON.parse(init.body).content);
    if (++reads === 1) return reply("before");
    return new Promise(r => { resolve = r; });
  }, storage());
  await draft.load();
  const refresh = draft.load();
  draft.edit("new edit");
  await draft.flush();
  resolve(reply("stale read"));
  await refresh;
  assert.equal(draft.state.content, "new edit");
  assert.equal(draft.state.base, "new edit");
});
test("browser fetch is invoked with its global receiver", async () => {
  const draft = new MemoryDraft("/note", async function (_url, init) {
    assert.equal(this, globalThis);
    return reply(init ? "after" : "before");
  }, storage());
  await draft.load();
  assert.equal(draft.state.loaded, true);
  draft.edit("after"); await draft.flush();
  assert.equal(draft.state.base, "after");
});
test("undo and redo survive automatic file saves", async () => {
  const draft = new MemoryDraft("/note", async (_url, init) => reply(init ? JSON.parse(init.body).content : "before"), storage());
  await draft.load();
  draft.edit("one"); await draft.flush();
  draft.edit("two"); await draft.flush();
  draft.undo(); await draft.flush();
  assert.equal(draft.state.base, "one");
  draft.undo(); await draft.flush();
  assert.equal(draft.state.base, "before");
  draft.redo(); await draft.flush();
  assert.equal(draft.state.base, "one");
});
