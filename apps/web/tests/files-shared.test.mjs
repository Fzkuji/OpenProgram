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

globalThis.window = { addEventListener() {}, removeEventListener() {} };
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
} = await import("../lib/state/files-shared.ts");

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
