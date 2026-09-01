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
  latestFileMtime,
  noteFileMtime,
  readCache,
  hasDirtyDraftsForPath,
  persistFileDraft,
  clearFileDraftsForPath,
  setDraftStoreAdapterForTests,
  reconcileProjectSnapshot,
  runAfterServerFileOperation,
  discardFileDraftsBeforeClose,
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

test("quota failure keeps the live draft visible to delete protection", async () => {
  const store = new MemoryDraftStore();
  setDraftStoreAdapterForTests(store);
  const draft = (value) => ({ draft: value, baselineContent: "原文", baselineMtime: 1 });
  assert.equal((await persistFileDraft("p", "dirty.txt", draft("初稿"))).ok, true);
  store.failNextWrite = true;
  const failed = await persistFileDraft("p", "dirty.txt", draft("扩大的未持久化稿件"));
  assert.equal(failed.code, "DRAFT_QUOTA_EXCEEDED");
  assert.equal(await hasDirtyDraftsForPath("p", "dirty.txt"), true);
  store.failNextWrite = true;
  assert.equal((await clearFileDraftsForPath("p", "dirty.txt")).ok, false);
  assert.equal(await hasDirtyDraftsForPath("p", "dirty.txt"), true);
  assert.equal((await clearFileDraftsForPath("p", "dirty.txt")).ok, true);
  assert.equal(await hasDirtyDraftsForPath("p", "dirty.txt"), false);
});

test("authoritative removal awaits cleanup and retries a failed cleanup", async () => {
  const calls = [];
  let fail = true;
  const cleanup = async (projectId) => {
    calls.push(projectId);
    if (fail) return { ok: false, code: "DRAFT_PERSISTENCE_FAILED", message: "blocked" };
    return { ok: true };
  };
  await reconcileProjectSnapshot(["project-kept", "project-gone"], cleanup);
  await reconcileProjectSnapshot(["project-kept"], cleanup);
  assert.deepEqual(calls, ["project-gone"]);
  fail = false;
  await reconcileProjectSnapshot(["project-kept"], cleanup);
  assert.deepEqual(calls, ["project-gone", "project-gone"]);
});

test("file mutation helpers enforce server, draft, and tab ordering", async () => {
  const events = [];
  assert.equal(await runAfterServerFileOperation(
    async () => { events.push("server"); return true; },
    async () => { events.push("drafts"); events.push("tabs"); return true; },
  ), true);
  assert.deepEqual(events, ["server", "drafts", "tabs"]);
  events.length = 0;
  assert.equal(await runAfterServerFileOperation(
    async () => { events.push("server-failed"); return false; },
    async () => { events.push("drafts"); return true; },
  ), false);
  assert.deepEqual(events, ["server-failed"]);

  const discarded = await discardFileDraftsBeforeClose([
    { kind: "file", projectId: "p", path: "a.txt", dirty: true },
    { kind: "file", projectId: "p", path: "b.txt", dirty: true },
  ], async (projectId, path) => {
    events.push(`${projectId}:${path}`);
    return { ok: path === "a.txt" };
  });
  assert.equal(discarded, false);
  assert.deepEqual(events, ["server-failed", "p:a.txt", "p:b.txt"]);
});
