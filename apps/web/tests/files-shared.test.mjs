import assert from "node:assert/strict";
import test from "node:test";
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import { fileURLToPath } from "node:url";

const webRoot = new URL("../", import.meta.url);
registerHooks({
  resolve(specifier, context, nextResolve) {
    const base = specifier.startsWith("@/")
      ? new URL(specifier.slice(2), webRoot)
      : specifier.startsWith(".") && !/\.[a-z]+$/i.test(specifier)
        ? new URL(specifier, context.parentURL)
        : null;
    if (base) {
      for (const suffix of [".ts", ".tsx", "/index.ts", "/index.tsx"]) {
        const url = `${base.href}${suffix}`;
        if (existsSync(fileURLToPath(url))) return { url, shortCircuit: true };
      }
    }
    return nextResolve(specifier, context);
  },
});

globalThis.window = {
  location: { pathname: "/chat" },
  addEventListener() {},
  removeEventListener() {},
  dispatchEvent() {},
};
globalThis.document = { addEventListener() {}, removeEventListener() {} };
globalThis.localStorage = { getItem() { return null; }, setItem() {}, removeItem() {} };
globalThis.WebSocket = { OPEN: 1 };

const {
  READ_CACHE_MAX_ENTRIES,
  cacheFileRead,
  fileReadCacheKey,
  getCachedFileRead,
  invalidateFileRead,
  latestFileMtime,
  noteFileMtime,
  readCache,
  hasDirtyDraftsForPath,
  persistFileDraft,
  clearFileDraftsForPath,
  discardFileDraft,
  moveFileDrafts,
  loadFileDraft,
  setDraftStoreAdapterForTests,
  getDraftPersistenceError,
  DRAFT_MAX_BYTES,
  fileDraftBytes,
  canPersistFileDraft,
  subscribeDraftPersistenceErrors,
  discardFileDraftsBeforeClose,
  collectDirtyFileTabs,
  runServerRenameWithDrafts,
} = await import("../lib/state/files-shared.ts");
const { MemoryDraftStore } = await import("../lib/state/file-draft-store.ts");

test("read cache is project-scoped, mtime-scoped, and LRU bounded", () => {
  readCache.clear();
  latestFileMtime.clear();
  const result = (project_id, path, mtime, content = "x") => ({
    project_id, path, mtime, size: content.length, content,
  });
  cacheFileRead(result("project-a", "same.txt", 1));
  cacheFileRead(result("project-b", "same.txt", 1, "other"));
  assert.equal(getCachedFileRead("project-a", "same.txt", 1)?.content, "x");
  assert.equal(getCachedFileRead("project-b", "same.txt", 1)?.content, "other");
  assert.equal(fileReadCacheKey("project-a", "same.txt", 1), "project-a:same.txt:1");

  for (let i = 0; i <= READ_CACHE_MAX_ENTRIES; i++)
    cacheFileRead(result("project-a", `file-${i}.txt`, 1));
  assert.equal(getCachedFileRead("project-a", "file-0.txt", 1), undefined);
  assert.equal(getCachedFileRead("project-a", `file-${READ_CACHE_MAX_ENTRIES - 1}.txt`, 1)?.content, "x");
  cacheFileRead(result("project-b", "same.txt", 1, "other"));
  noteFileMtime("project-a", "same.txt", 2);
  assert.equal(getCachedFileRead("project-a", "same.txt", 1), undefined);
  assert.equal(getCachedFileRead("project-b", "same.txt", 1)?.content, "other");
});

test("rename/delete cache invalidation is project-scoped and clears descendants", () => {
  readCache.clear();
  latestFileMtime.clear();
  const result = (project_id, path) => ({ project_id, path, mtime: 1, size: 1, content: "x" });
  cacheFileRead(result("p", "folder/file.txt"));
  cacheFileRead(result("p", "other.txt"));
  cacheFileRead(result("q", "folder/file.txt"));
  invalidateFileRead("p", "folder");
  assert.equal(getCachedFileRead("p", "folder/file.txt", 1), undefined);
  assert.equal(getCachedFileRead("p", "other.txt", 1)?.content, "x");
  assert.equal(getCachedFileRead("q", "folder/file.txt", 1)?.content, "x");
});

test("quota failure keeps the live draft visible to delete protection", async () => {
  const store = new MemoryDraftStore();
  setDraftStoreAdapterForTests(store);
  const draft = (value) => ({ draft: value, baselineContent: "原文", baselineMtime: 1 });
  assert.equal((await persistFileDraft("p", "dirty.txt", draft("初稿"))).ok, true);
  let errorNotifications = 0;
  const unsubscribe = subscribeDraftPersistenceErrors(() => { errorNotifications += 1; });
  store.failNextWrite = true;
  const failed = await persistFileDraft("p", "dirty.txt", draft("扩大的未持久化稿件"));
  assert.equal(failed.code, "DRAFT_QUOTA_EXCEEDED");
  assert.ok(errorNotifications > 0);
  assert.match(getDraftPersistenceError("p:dirty.txt"), /last saved draft was retained/);
  assert.equal(await hasDirtyDraftsForPath("p", "dirty.txt"), true);
  store.failNextWrite = true;
  const failedClear = await clearFileDraftsForPath("p", "dirty.txt");
  assert.equal(failedClear.ok, false);
  assert.equal(failedClear.status, "recovery_required");
  assert.match(getDraftPersistenceError("p:dirty.txt"), /Unable to discard/);
  assert.equal(await hasDirtyDraftsForPath("p", "dirty.txt"), true);
  assert.equal((await clearFileDraftsForPath("p", "dirty.txt")).ok, true);
  assert.equal(await hasDirtyDraftsForPath("p", "dirty.txt"), false);
  unsubscribe();
});

test("quota failure for a new draft retains its live buffer and error status", async () => {
  const store = new MemoryDraftStore();
  setDraftStoreAdapterForTests(store);
  await loadFileDraft("p", "new.txt");
  store.failNextWrite = true;
  const value = { draft: "未持久化", baselineContent: "基线", baselineMtime: 1 };
  const failed = await persistFileDraft("p", "new.txt", value);
  assert.equal(failed.code, "DRAFT_QUOTA_EXCEEDED");
  assert.equal((await loadFileDraft("p", "new.txt")).draft, value.draft);
  assert.equal((await loadFileDraft("p", "new.txt")).save_status, "error");
  assert.equal(await hasDirtyDraftsForPath("p", "new.txt"), true);
});

test("persisted drafts retain baseline revision and save status across hydration", async () => {
  const store = new MemoryDraftStore();
  setDraftStoreAdapterForTests(store);
  const value = { draft: "改稿", baselineContent: "基线", baselineMtime: 3, baselineRevision: "rev-3" };
  assert.equal((await persistFileDraft("p", "revision.txt", value)).ok, true);
  const restored = await loadFileDraft("p", "revision.txt");
  assert.equal(restored.baselineRevision, "rev-3");
  assert.equal(restored.save_status, "persisted");
});

test("legacy draft records hydrate with an explicit persisted status", async () => {
  const store = new MemoryDraftStore();
  store.drafts.set("p:legacy.txt", {
    key: "p:legacy.txt", projectId: "p", path: "legacy.txt", draft: "改稿",
    baselineContent: "基线", baselineMtime: 3, bytes: 0, updatedAt: 1,
  });
  setDraftStoreAdapterForTests(store);
  const restored = await loadFileDraft("p", "legacy.txt");
  assert.equal(restored.save_status, "persisted");
  assert.equal(store.drafts.get("p:legacy.txt").save_status, "persisted");
});

test("discarding a failed live expansion preserves the exact A+C index", async () => {
  const store = new MemoryDraftStore();
  setDraftStoreAdapterForTests(store);
  const draft = (value) => ({ draft: value, baselineContent: "基线", baselineMtime: 1 });
  assert.equal((await persistFileDraft("p", "a.txt", draft("A"))).ok, true);
  assert.equal((await persistFileDraft("p", "c.txt", draft("C"))).ok, true);
  store.failNextWrite = true;
  assert.equal((await persistFileDraft("p", "a.txt", draft("A".repeat(1000)))).ok, false);
  assert.equal((await discardFileDraft("p", "a.txt")).ok, true);
  const c = store.drafts.get("p:c.txt");
  assert.deepEqual(store.indexes.get("p"), {
    projectId: "p", keys: ["p:c.txt"], count: 1, bytes: c.bytes,
  });
});

test("file close helper discards every dirty file and finds inactive durable drafts", async () => {
  const events = [];
  const discarded = await discardFileDraftsBeforeClose([
    { kind: "file", projectId: "p", path: "a.txt", dirty: false },
    { kind: "file", projectId: "p", path: "b.txt", dirty: false },
  ], async (projectId, path) => {
    events.push(`${projectId}:${path}`);
    return { ok: path === "a.txt" };
  }, async () => true);
  assert.equal(discarded, false);
  assert.deepEqual(events, ["p:a.txt", "p:b.txt"]);

  const inactive = await collectDirtyFileTabs(
    [{ kind: "file", projectId: "p", path: "inactive.txt", dirty: false }],
    async () => true,
  );
  assert.equal(inactive.length, 1, "persistent dirty state arms close confirmation even when tab.dirty is false");
});

test("canonical draft bytes include metadata in the quota decision", () => {
  const draft = { draft: "x".repeat(DRAFT_MAX_BYTES), baselineContent: "", baselineMtime: 1 };
  assert.ok(fileDraftBytes("p:file.txt", draft, 1) > DRAFT_MAX_BYTES);
  assert.notEqual(
    fileDraftBytes("p:file.txt", { ...draft, draft: "内容" }, 1),
    fileDraftBytes("p:file.txt", { ...draft, draft: "内容" }, 1000),
  );
  assert.equal(canPersistFileDraft("p:oversize.txt", draft), false);
});

test("repair failure reports a durable error and retries through the adapter", async () => {
  const store = new MemoryDraftStore();
  setDraftStoreAdapterForTests(store);
  store.failNextWrite = true;
  assert.equal(await hasDirtyDraftsForPath("p", "missing.txt"), false);
  assert.match(getDraftPersistenceError("__store__"), /retained for retry/);
  assert.equal(await hasDirtyDraftsForPath("p", "missing.txt"), false);
});

test("rename quota failure retains the old draft transactionally", async () => {
  const store = new MemoryDraftStore();
  setDraftStoreAdapterForTests(store);
  const large = { draft: "x".repeat(DRAFT_MAX_BYTES - 500), baselineContent: "", baselineMtime: 1 };
  assert.equal((await persistFileDraft("p", "old.txt", large)).ok, true);
  const longPath = `${"n".repeat(1000)}.txt`;
  const moved = await moveFileDrafts("p", "old.txt", longPath);
  assert.equal(moved.code, "DRAFT_QUOTA_EXCEEDED");
  assert.equal(await hasDirtyDraftsForPath("p", "old.txt"), true);
  assert.equal(await hasDirtyDraftsForPath("p", longPath), false);
  assert.equal(store.drafts.has("p:old.txt"), true);
});

test("rename preflight blocks the server when the target metadata exceeds quota", async () => {
  const store = new MemoryDraftStore();
  setDraftStoreAdapterForTests(store);
  const large = { draft: "x".repeat(DRAFT_MAX_BYTES - 500), baselineContent: "", baselineMtime: 1 };
  await persistFileDraft("p", "old.txt", large);
  let serverCalls = 0;
  const result = await runServerRenameWithDrafts(
    "p", "old.txt", `${"n".repeat(1000)}.txt`,
    async () => { serverCalls += 1; return { status: "ready" }; },
    async () => ({ status: "ready" }),
  );
  assert.equal(result.code, "DRAFT_QUOTA_EXCEEDED");
  assert.equal(serverCalls, 0);
  assert.equal(store.drafts.has("p:old.txt"), true);
});

test("server rename uses structured status and retains old draft on rejection", async () => {
  const store = new MemoryDraftStore();
  setDraftStoreAdapterForTests(store);
  const draft = { draft: "dirty", baselineContent: "base", baselineMtime: 1 };
  await persistFileDraft("p", "old.txt", draft);
  let reverseCalls = 0;
  const failed = await runServerRenameWithDrafts(
    "p", "old.txt", "new.txt",
    async () => ({ status: "error", error_code: "SERVER_RENAME_REJECTED", idempotency_key: "rename-reject" }),
    async () => { reverseCalls += 1; return { status: "ready" }; },
  );
  assert.equal(failed.ok, false);
  assert.equal(store.drafts.has("p:old.txt"), true);
  assert.equal(store.drafts.has("p:new.txt"), false);
  assert.equal(reverseCalls, 0);
  assert.equal(failed.status, "error");
  assert.equal(failed.error_code, "SERVER_RENAME_REJECTED");
  assert.equal(failed.idempotency_key, "rename-reject");
});

test("server rename exception and recovery status retain old draft", async () => {
  const store = new MemoryDraftStore();
  setDraftStoreAdapterForTests(store);
  const draft = { draft: "dirty", baselineContent: "base", baselineMtime: 1 };
  await persistFileDraft("p", "old.txt", draft);
  for (const serverRename of [
    async () => { throw new Error("timeout"); },
    async () => ({ status: "recovery_required", message: "uncertain" }),
  ]) {
    const result = await runServerRenameWithDrafts(
      "p", "old.txt", "new.txt", serverRename, async () => ({ status: "ready" }),
    );
    assert.equal(result.ok, false);
    assert.equal(store.drafts.has("p:old.txt"), true);
    assert.equal(store.drafts.has("p:new.txt"), false);
  }
});

test("local rename failure compensates the ready server rename", async () => {
  const store = new MemoryDraftStore();
  setDraftStoreAdapterForTests(store);
  await persistFileDraft("p", "old.txt", { draft: "dirty", baselineContent: "base", baselineMtime: 1 });
  store.failNextWrite = false;
  let reverseCalls = 0;
  const result = await runServerRenameWithDrafts(
    "p", "old.txt", "new.txt",
    async () => { store.failNextWrite = true; return { status: "ready", idempotency_key: "rename-1", operation_id: "op-1" }; },
    async (serverResult) => {
      reverseCalls += 1;
      assert.equal(serverResult.idempotency_key, "rename-1");
      return { status: "ready", idempotency_key: "rename-1-reverse", operation_id: "op-reverse" };
    },
  );
  assert.equal(result.ok, false);
  assert.equal(result.status, "error");
  assert.equal(result.error_code, "DRAFT_QUOTA_EXCEEDED");
  assert.equal(result.operation_id, "op-reverse");
  assert.equal(reverseCalls, 1);
  assert.equal(store.drafts.has("p:old.txt"), true);
  assert.equal(store.drafts.has("p:new.txt"), false);
});

test("failed compensation reports recovery and never creates an orphan", async () => {
  const store = new MemoryDraftStore();
  setDraftStoreAdapterForTests(store);
  await persistFileDraft("p", "old.txt", { draft: "dirty", baselineContent: "base", baselineMtime: 1 });
  const result = await runServerRenameWithDrafts(
    "p", "old.txt", "new.txt",
    async () => { store.failNextWrite = true; return { status: "ready" }; },
    async () => ({ status: "recovery_required", message: "reverse timeout" }),
  );
  assert.equal(result.ok, false);
  assert.equal(store.drafts.has("p:old.txt"), true);
  assert.equal(store.drafts.has("p:new.txt"), false);
  assert.match(getDraftPersistenceError("p"), /recovery is required/);
});

test("failed expansion stays in the live overlay until explicit discard", async () => {
  const store = new MemoryDraftStore();
  setDraftStoreAdapterForTests(store);
  const draft = (value) => ({ draft: value, baselineContent: "base", baselineMtime: 1 });
  await persistFileDraft("p", "a.txt", draft("A"));
  store.failNextWrite = true;
  assert.equal((await persistFileDraft("p", "a.txt", draft("B"))).ok, false);
  await persistFileDraft("p", "c.txt", draft("C"));
  assert.equal((await loadFileDraft("p", "a.txt")).draft, "B");
  assert.equal((await discardFileDraft("p", "a.txt")).ok, true);
  assert.equal(await loadFileDraft("p", "a.txt"), null);
  assert.equal((await loadFileDraft("p", "c.txt")).draft, "C");
});

test("rename uses the failed live overlay instead of the stale persisted record", async () => {
  const store = new MemoryDraftStore();
  setDraftStoreAdapterForTests(store);
  const draft = (value) => ({ draft: value, baselineContent: "base", baselineMtime: 1 });
  await persistFileDraft("p", "old.txt", draft("A"));
  store.failNextWrite = true;
  assert.equal((await persistFileDraft("p", "old.txt", draft("B"))).ok, false);
  const moved = await runServerRenameWithDrafts(
    "p", "old.txt", "new.txt",
    async () => ({ status: "ready" }),
    async () => ({ status: "ready" }),
  );
  assert.equal(moved.ok, true);
  assert.equal((await loadFileDraft("p", "new.txt")).draft, "B");
});
