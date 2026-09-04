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

async function putRecord(store, entry) {
  await store.mutate((snapshot) => {
    const drafts = [...snapshot.drafts.filter((record) => record.key !== entry.key), entry];
    return { drafts, indexes: rebuildDraftIndexes({ drafts, indexes: snapshot.indexes }) };
  });
}

async function removeKeys(store, keys) {
  await store.mutate((snapshot) => {
    const drafts = snapshot.drafts.filter((entry) => !keys.includes(entry.key));
    return { drafts, indexes: rebuildDraftIndexes({ drafts, indexes: snapshot.indexes }) };
  });
}

test("transactional fake supports refresh recovery, UTF-8 budget, and atomic quota failure", async () => {
  const store = new MemoryDraftStore();
  const first = record("p", "文件.txt", "改后的中文");
  await putRecord(store, first);
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
    putRecord(store, record("p", "other", "new")),
    DraftStoreQuotaError,
  );
  assert.deepEqual(await store.load(), before, "failed transaction keeps draft and index intact");
});

test("rename, delete, unlink, and close/reload failures preserve the old record", async () => {
  const store = new MemoryDraftStore();
  const old = record("p", "old/file.txt", "dirty");
  await putRecord(store, old);
  const renamed = record("p", "new/file.txt", old.draft, old.baselineContent);
  await store.mutate((snapshot) => {
    const drafts = [...snapshot.drafts.filter((entry) => entry.key !== old.key), renamed];
    return { drafts, indexes: rebuildDraftIndexes({ drafts, indexes: snapshot.indexes }) };
  });
  let snapshot = await store.load();
  assert.equal(snapshot.drafts.some((entry) => entry.key === old.key), false);
  assert.equal(snapshot.drafts[0].key, renamed.key);

  store.failNextWrite = true;
  await assert.rejects(removeKeys(store, [renamed.key]), DraftStoreQuotaError);
  assert.equal((await store.load()).drafts[0].key, renamed.key, "failed close/reload discard retains draft");

  await removeKeys(store, [renamed.key]);
  assert.equal((await store.load()).drafts.length, 0);
  const unlink = record("p", "folder/a.txt", "dirty");
  await putRecord(store, unlink);
  await removeKeys(store, [unlink.key]);
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
