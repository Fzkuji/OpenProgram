import assert from "node:assert/strict";
import test from "node:test";

import {
  DraftStoreQuotaError,
  MemoryDraftStore,
  rebuildDraftIndexes,
} from "../lib/state/file-draft-store.ts";

const encoder = new TextEncoder();
const draftBytes = (key, draft, baseline) =>
  encoder.encode(key).byteLength
  + encoder.encode(draft).byteLength
  + encoder.encode(baseline).byteLength;

function record(projectId, path, draft = "修改", baseline = "原文") {
  const key = `${projectId}:${path}`;
  return {
    key, projectId, path, draft, baselineContent: baseline,
    baselineMtime: 1, bytes: draftBytes(key, draft, baseline), updatedAt: 1,
  };
}

test("transactional fake supports refresh recovery, UTF-8 budget, and atomic quota failure", async () => {
  const store = new MemoryDraftStore();
  const first = record("p", "文件.txt", "改后的中文");
  const index = { projectId: "p", keys: [first.key], count: 1, bytes: first.bytes };
  await store.put(first, index);
  const refreshed = await store.load();
  assert.equal(refreshed.drafts[0].draft, "改后的中文");
  assert.equal(first.bytes, draftBytes(first.key, first.draft, first.baselineContent));

  const eightMiB = 8 * 1024 * 1024;
  assert.ok(draftBytes("p:中文", "中".repeat(8), "基线") > encoder.encode("中".repeat(8)).byteLength);
  const entries = Array.from({ length: 32 }, (_, i) => record("full", `f-${i}`, "x", ""));
  assert.equal(entries.length, 32);
  assert.ok(entries.reduce((sum, entry) => sum + entry.bytes, 0) < eightMiB);

  const before = await store.load();
  store.failNextWrite = true;
  await assert.rejects(
    store.put(record("p", "other", "new"), { projectId: "p", keys: ["p:other"], count: 1, bytes: 1 }),
    DraftStoreQuotaError,
  );
  assert.deepEqual(await store.load(), before, "failed transaction keeps draft and index intact");
});

test("rename, delete, unlink, and close/reload failures preserve the old record", async () => {
  const store = new MemoryDraftStore();
  const old = record("p", "old/file.txt", "dirty");
  await store.put(old, { projectId: "p", keys: [old.key], count: 1, bytes: old.bytes });
  const renamed = record("p", "new/file.txt", old.draft, old.baselineContent);
  await store.move([{ oldKey: old.key, record: renamed }], {
    projectId: "p", keys: [renamed.key], count: 1, bytes: renamed.bytes,
  });
  let snapshot = await store.load();
  assert.equal(snapshot.drafts.some((entry) => entry.key === old.key), false);
  assert.equal(snapshot.drafts[0].key, renamed.key);

  store.failNextWrite = true;
  await assert.rejects(store.remove([renamed.key], { projectId: "p", keys: [], count: 0, bytes: 0 }), DraftStoreQuotaError);
  assert.equal((await store.load()).drafts[0].key, renamed.key, "failed close/reload discard retains draft");

  await store.remove([renamed.key], { projectId: "p", keys: [], count: 0, bytes: 0 });
  assert.equal((await store.load()).drafts.length, 0);
  const unlink = record("p", "folder/a.txt", "dirty");
  await store.put(unlink, { projectId: "p", keys: [unlink.key], count: 1, bytes: unlink.bytes });
  await store.clear([unlink.key], "p");
  snapshot = await store.load();
  assert.equal(snapshot.drafts.length, 0, "project unlink clears its drafts");
  assert.equal(snapshot.indexes.length, 0);
});

test("refresh index repair keeps drafts and removes ghost and empty indexes", () => {
  const draft = record("p", "kept.txt", "dirty");
  const indexes = rebuildDraftIndexes({
    drafts: [draft],
    indexes: [
      { projectId: "p", keys: [draft.key, "p:ghost.txt"], count: 2, bytes: draft.bytes },
      { projectId: "empty", keys: [], count: 0, bytes: 0 },
    ],
  });
  assert.deepEqual(indexes, [{ projectId: "p", keys: [draft.key], count: 1, bytes: draft.bytes }]);
});

test("adapter mutation serializes concurrent contexts and repair failure is atomic", async () => {
  const store = new MemoryDraftStore();
  const a = record("p", "a.txt", "A");
  const c = record("p", "c.txt", "C");
  await Promise.all([
    store.mutate((snapshot) => ({
      drafts: [...snapshot.drafts, a],
      indexes: rebuildDraftIndexes({ drafts: [...snapshot.drafts, a], indexes: snapshot.indexes }),
    })),
    store.mutate((snapshot) => ({
      drafts: [...snapshot.drafts, c],
      indexes: rebuildDraftIndexes({ drafts: [...snapshot.drafts, c], indexes: snapshot.indexes }),
    })),
  ]);
  assert.deepEqual((await store.load()).drafts.map((entry) => entry.key).sort(), [a.key, c.key]);
  const before = await store.load();
  store.failNextWrite = true;
  await assert.rejects(store.repair(), DraftStoreQuotaError);
  assert.deepEqual(await store.load(), before);
});
