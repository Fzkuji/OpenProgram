import test from "node:test";
import assert from "node:assert/strict";
import { MemoryDraft } from "../components/memory/autosave.ts";

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
  assert.deepEqual(calls[1], { content: "second", base_content: "first" });
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
