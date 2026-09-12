import assert from "node:assert/strict";
import test from "node:test";
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import { fileURLToPath } from "node:url";

const root = new URL("../", import.meta.url);
registerHooks({ resolve(specifier, context, nextResolve) {
  const base = specifier.startsWith("@/") ? new URL(specifier.slice(2), root)
    : specifier.startsWith(".") ? new URL(specifier, context.parentURL) : null;
  if (base) for (const suffix of [".ts", ".tsx", "/index.ts"]) {
    const url = `${base.href}${suffix}`;
    if (existsSync(fileURLToPath(url))) return { url, shortCircuit: true };
  }
  return nextResolve(specifier, context);
} });

const { DocumentController } = await import("../lib/state/document-controller.ts");
const {
  clearFileDraftsForPath,
  hasDirtyDraftsForPath,
  runServerRenameWithDrafts,
  setDraftStoreAdapterForTests,
} = await import("../lib/state/file-drafts.ts");
const { MemoryDraftStore } = await import("../lib/state/file-draft-store.ts");

const revision = "a".repeat(64);
const store = () => ({
  values: new Map(),
  async get(key) { return structuredClone(this.values.get(key) ?? null); },
  async put(record) {
    const old = this.values.get(record.key);
    assert.equal(record.storageVersion, old?.storageVersion ?? 0);
    const version = (old?.storageVersion ?? 0) + 1;
    this.values.set(record.key, structuredClone({ ...record, storageVersion: version }));
    return version;
  },
  async delete(key, expectedVersion) {
    assert.equal(expectedVersion, this.values.get(key)?.storageVersion ?? 0);
    this.values.delete(key);
  },
});

test.beforeEach(() => setDraftStoreAdapterForTests(new MemoryDraftStore()));

test("lifecycle guards include an in-memory Blob draft", async () => {
  const records = store();
  const controller = new DocumentController({ projectId: "p", path: "notes.bin", draftStore: records });
  await controller.hydrate({ bytes: new Blob(["disk"]), revision });
  controller.update(new Blob(["unsaved"], { type: "application/octet-stream" }));

  assert.equal(await hasDirtyDraftsForPath("p", "notes.bin"), true);
  await controller.close().catch(() => undefined);
});

test("rename is blocked while a controller draft is dirty", async () => {
  const controller = new DocumentController({ projectId: "p", path: "old.bin", draftStore: store() });
  await controller.hydrate({ bytes: "disk", revision });
  controller.update(new Blob(["unsaved"]));
  let serverCalls = 0;
  const result = await runServerRenameWithDrafts("p", "old.bin", "new.bin", async () => {
    serverCalls++;
    return { status: "ready" };
  }, async () => ({ status: "ready" }));
  assert.equal(result.ok, false);
  assert.equal(result.code, "DRAFT_CONFLICT");
  assert.equal(serverCalls, 0);
  await controller.discard();
  await controller.close();
});

test("deletion cleanup discards the controller Blob and its durable record", async () => {
  const records = store();
  const controller = new DocumentController({ projectId: "p", path: "remove.bin", draftStore: records,
    fetchImpl: async () => new Response(JSON.stringify({ ok: true, status: "committed", revision: "b".repeat(64) })) });
  records.values.set("project:p:remove.bin", {
    key: "project:p:remove.bin", projectId: "p", path: "remove.bin", baselineRevision: revision,
    latestDraft: new Blob(["unsaved"]), generation: 1, editorId: "editor", updatedAt: 1, storageVersion: 1,
  });
  await controller.hydrate({ bytes: "disk", revision });
  assert.equal(await controller.currentDraft().text(), "unsaved");
  assert.equal(records.values.size, 1);
  assert.equal((await clearFileDraftsForPath("p", "remove.bin")).ok, true);
  assert.equal(records.values.size, 0);
  assert.equal(await hasDirtyDraftsForPath("p", "remove.bin"), false);
  await controller.close();
});
