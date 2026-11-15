import assert from "node:assert/strict";
import test from "node:test";
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import { fileURLToPath } from "node:url";

const webRoot = new URL("../../", import.meta.url);
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
  READ_CACHE_MAX_BYTES,
  LATEST_MTIME_MAX_ENTRIES,
  DRAFT_MAX_ENTRIES,
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
  loadFileDraftsForPath,
  setDraftStoreAdapterForTests,
  getDraftPersistenceError,
  DRAFT_MAX_BYTES,
  fileDraftBytes,
  canPersistFileDraft,
  subscribeDraftPersistenceErrors,
  discardFileDraftsBeforeClose,
  collectDirtyFileTabs,
  runServerRenameWithDrafts,
} = await import("../../lib/files/files-shared.ts");
const { MemoryDraftStore } = await import("../../lib/files/drafts/file-draft-store.ts");
const { flushFileDocumentsBeforeClose } = await import("../../lib/files/drafts/file-drafts.ts");
const { documentControllers, documentIdentityKey } = await import("../../lib/files/document/document-controller.ts");

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

test("latest mtimes are project-scoped and LRU bounded", () => {
  latestFileMtime.clear();
  readCache.clear();
  const result = (project_id, path, mtime, content = "x") => ({
    project_id, path, mtime, size: content.length, content,
  });
  cacheFileRead(result("project-mtime", "file-0.txt", 0));
  for (let i = 0; i <= LATEST_MTIME_MAX_ENTRIES; i += 1)
    noteFileMtime("project-mtime", `file-${i}.txt`, i);
  assert.equal(latestFileMtime.size, LATEST_MTIME_MAX_ENTRIES);
  assert.equal(latestFileMtime.has("project-mtime:file-0.txt"), false);
  assert.equal(getCachedFileRead("project-mtime", "file-0.txt"), undefined);
  cacheFileRead(result("project-mtime", "file-1.txt", 1));
  assert.equal(getCachedFileRead("project-mtime", "file-1.txt")?.content, "x");
  noteFileMtime("project-mtime", "file-1.txt", 999);
  assert.equal(latestFileMtime.get("project-mtime:file-1.txt"), 999);
  assert.equal(latestFileMtime.size, LATEST_MTIME_MAX_ENTRIES);
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

test("directory delete preparation enumerates every nested dirty draft", async () => {
  const store = new MemoryDraftStore();
  setDraftStoreAdapterForTests(store);
  const draft = (value) => ({ draft: value, baselineContent: "基线", baselineMtime: 1 });
  await persistFileDraft("p", "dir/a.txt", draft("A"));
  await persistFileDraft("p", "dir/nested/b.txt", draft("B"));
  assert.deepEqual(
    (await loadFileDraftsForPath("p", "dir")).map((entry) => [entry.path, entry.draft.draft]),
    [["dir/a.txt", "A"], ["dir/nested/b.txt", "B"]],
  );
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

test("rename target dirty draft is an explicit conflict and both records remain", async () => {
  const store = new MemoryDraftStore();
  setDraftStoreAdapterForTests(store);
  const draft = (value) => ({ draft: value, baselineContent: "base", baselineMtime: 1 });
  await persistFileDraft("p", "old.txt", draft("old"));
  await persistFileDraft("p", "new.txt", draft("new"));
  const result = await moveFileDrafts("p", "old.txt", "new.txt");
  assert.equal(result.code, "DRAFT_CONFLICT");
  assert.equal((await loadFileDraft("p", "old.txt")).draft, "old");
  assert.equal((await loadFileDraft("p", "new.txt")).draft, "new");
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

test("read cache evicts the oldest reads to stay within its UTF-8 byte budget", () => {
  readCache.clear();
  latestFileMtime.clear();
  assert.equal(READ_CACHE_MAX_ENTRIES, 64);
  assert.equal(READ_CACHE_MAX_BYTES, 16 * 1024 * 1024);
  const sixMiB = "x".repeat(6 * 1024 * 1024);
  const read = (path, content) => ({ project_id: "bytes", path, mtime: 1, size: content.length, content });
  cacheFileRead(read("a.txt", sixMiB));
  cacheFileRead(read("b.txt", sixMiB));
  cacheFileRead(read("c.txt", sixMiB));
  assert.equal(getCachedFileRead("bytes", "a.txt", 1), undefined, "the oldest read made room");
  assert.equal(getCachedFileRead("bytes", "b.txt", 1)?.content.length, sixMiB.length);
  assert.equal(getCachedFileRead("bytes", "c.txt", 1)?.content.length, sixMiB.length);

  // Multi-byte text counts by its encoded size, and one read larger than the
  // whole budget is never cached (nor does it evict anything).
  cacheFileRead(read("huge.txt", "中".repeat(6 * 1024 * 1024)));
  assert.equal(getCachedFileRead("bytes", "huge.txt", 1), undefined);
  assert.equal(getCachedFileRead("bytes", "c.txt", 1)?.content.length, sixMiB.length);

  // Errors and truncated reads are not cached.
  cacheFileRead({ ...read("partial.txt", "x"), truncated: true });
  cacheFileRead({ ...read("failed.txt", "x"), error: "denied" });
  assert.equal(getCachedFileRead("bytes", "partial.txt", 1), undefined);
  assert.equal(getCachedFileRead("bytes", "failed.txt", 1), undefined);
  readCache.clear();
});

test("the 33rd dirty draft is refused as over quota and the first 32 stay", async () => {
  const store = new MemoryDraftStore();
  setDraftStoreAdapterForTests(store);
  assert.equal(DRAFT_MAX_ENTRIES, 32);
  const draft = { draft: "dirty", baselineContent: "base", baselineMtime: 1 };
  for (let i = 0; i < DRAFT_MAX_ENTRIES; i += 1)
    assert.equal((await persistFileDraft("cap", `f-${i}.txt`, draft)).ok, true);
  assert.equal(canPersistFileDraft("cap:f-extra.txt", draft), false);
  const refused = await persistFileDraft("cap", "f-extra.txt", draft);
  assert.equal(refused.code, "DRAFT_QUOTA_EXCEEDED");
  assert.equal(store.drafts.size, DRAFT_MAX_ENTRIES);
  assert.equal(store.drafts.has("cap:f-extra.txt"), false);
  assert.equal((await loadFileDraft("cap", "f-extra.txt")).save_status, "error", "the refused buffer stays live");
});

test("a browser QuotaExceededError reads as quota; any other storage failure does not", async () => {
  const failing = (error) => {
    const store = new MemoryDraftStore();
    let armed = false;
    return {
      arm() { armed = true; },
      load: () => store.load(),
      repair: () => store.repair(),
      mutate(operation) {
        if (armed) { armed = false; return Promise.reject(error); }
        return store.mutate(operation);
      },
    };
  };
  const draft = { draft: "dirty", baselineContent: "base", baselineMtime: 1 };

  const quota = failing(Object.assign(new Error("full"), { name: "QuotaExceededError" }));
  setDraftStoreAdapterForTests(quota);
  await loadFileDraft("q", "a.txt");
  quota.arm();
  const full = await persistFileDraft("q", "a.txt", draft);
  assert.equal(full.code, "DRAFT_QUOTA_EXCEEDED");
  assert.match(full.message, /storage is full/);

  const broken = failing(new Error("disk I/O"));
  setDraftStoreAdapterForTests(broken);
  await loadFileDraft("q", "a.txt");
  broken.arm();
  const failed = await persistFileDraft("q", "a.txt", draft);
  assert.equal(failed.code, "DRAFT_PERSISTENCE_FAILED");
  assert.match(getDraftPersistenceError("q:a.txt"), /Unable to persist/);
});

test("closing file tabs loads and flushes the document of every dirty tab, mounted or not", async () => {
  const store = new MemoryDraftStore();
  setDraftStoreAdapterForTests(store);
  await persistFileDraft("p", "dirty.txt", { draft: "dirty", baselineContent: "base", baselineMtime: 1 });
  const calls = [];
  const identity = (path) => ({ kind: "project", projectId: "p", path });
  const fake = (path, { draft = null, flush } = {}) => ({
    identity: identity(path),
    getState: () => ({ status: draft ? "dirty" : "idle" }),
    currentDraft: () => draft,
    async load() { calls.push(`load:${path}`); },
    flush: flush ?? (async () => { calls.push(`flush:${path}`); }),
  });
  const key = (path) => documentIdentityKey(identity(path));
  // dirty.txt has only a durable draft; editing.txt only an open editor's buffer.
  documentControllers.set(key("dirty.txt"), fake("dirty.txt"));
  documentControllers.set(key("editing.txt"), fake("editing.txt", { draft: "typed" }));
  documentControllers.set(key("clean.txt"), fake("clean.txt"));
  try {
    const ok = await flushFileDocumentsBeforeClose([
      { projectId: "p", path: "dirty.txt" },
      { projectId: "p", path: "editing.txt" },
      { projectId: "p", path: "clean.txt" },
      { projectId: "p" },
    ]);
    assert.equal(ok, true);
    assert.deepEqual(calls, ["load:dirty.txt", "flush:dirty.txt", "load:editing.txt", "flush:editing.txt"]);

    documentControllers.set(key("dirty.txt"), fake("dirty.txt", {
      flush: async () => { throw new Error("save rejected"); },
    }));
    assert.equal(await flushFileDocumentsBeforeClose([{ projectId: "p", path: "dirty.txt" }]), false,
      "a failed save keeps the tab open");
    assert.equal(getDraftPersistenceError("__close__"), "save rejected");
  } finally {
    documentControllers.clear();
  }
});
