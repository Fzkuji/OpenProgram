import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";

let playwright;
let esbuild;
try {
  playwright = await import("playwright");
  esbuild = await import("esbuild");
} catch {
  // Browser dependencies are supplied by the repository's optional browser
  // gate. Focused local runs remain dependency-free when they are absent.
}

test("browser uses the production IndexedDbDraftStore for atomic mutations", {
  skip: !playwright || !esbuild,
}, async () => {
  const entryPoint = fileURLToPath(new URL("../lib/state/file-draft-store.ts", import.meta.url));
  const bundle = await esbuild.build({
    entryPoints: [entryPoint],
    bundle: true,
    format: "iife",
    globalName: "DraftStoreBundle",
    platform: "browser",
    target: "es2022",
    write: false,
  });
  const script = new TextDecoder().decode(bundle.outputFiles[0].contents);
  const browser = await playwright.chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.goto("data:text/html,<title>draft-store</title>");
    await page.addScriptTag({ content: script });
    const result = await page.evaluate(async () => {
      const { IndexedDbDraftStore, DraftStoreQuotaError, rebuildDraftIndexes } = DraftStoreBundle;
      const dbName = IndexedDbDraftStore.databaseName;
      await new Promise((resolve) => {
        const request = indexedDB.deleteDatabase(dbName);
        request.onsuccess = request.onerror = request.onblocked = () => resolve();
      });
      const first = new IndexedDbDraftStore();
      const second = new IndexedDbDraftStore();
      const add = (key) => (snapshot) => {
        const drafts = [...snapshot.drafts, {
          key, projectId: "p", path: key, draft: key,
          baselineContent: "", baselineMtime: 1, bytes: 1, updatedAt: 1,
        }];
        return { drafts, indexes: rebuildDraftIndexes({ drafts, indexes: snapshot.indexes }) };
      };
      await Promise.all([first.mutate(add("a")), second.mutate(add("c"))]);
      const afterConcurrent = await first.load();
      let abortName = null;
      try {
        await first.mutate(() => { throw new DraftStoreQuotaError(); });
      } catch (error) {
        abortName = error.name;
      }
      const afterAbort = await second.load();
      await first.mutate((snapshot) => ({
        drafts: snapshot.drafts,
        indexes: [{ projectId: "ghost", keys: ["p:missing"], count: 1, bytes: 1 }],
      }));
      const repaired = await second.repair();
      return {
        keys: afterConcurrent.drafts.map((draft) => draft.key).sort(),
        abortName,
        retainedKeys: afterAbort.drafts.map((draft) => draft.key).sort(),
        repairedIndexes: repaired.indexes,
      };
    });
    assert.deepEqual(result.keys, ["a", "c"]);
    assert.equal(result.abortName, "QuotaExceededError");
    assert.deepEqual(result.retainedKeys, ["a", "c"]);
    assert.deepEqual(result.repairedIndexes, [{
      projectId: "p", keys: ["a", "c"], count: 2, bytes: 2,
    }]);
  } finally {
    await browser.close();
  }
});
