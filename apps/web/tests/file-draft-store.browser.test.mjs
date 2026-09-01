import assert from "node:assert/strict";
import test from "node:test";

let playwright;
try {
  playwright = await import("playwright");
} catch {
  // The repository's browser gate may provide Playwright externally. Local
  // focused runs remain dependency-free when that gate is unavailable.
}

test("native IndexedDB draft transaction keeps concurrent contexts atomic", {
  skip: !playwright,
}, async () => {
  const browser = await playwright.chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.goto("data:text/html,<title>draft-store</title>");
    const result = await page.evaluate(async () => {
      const name = `openprogram-draft-browser-${Date.now()}`;
      const db = await new Promise((resolve, reject) => {
        const request = indexedDB.open(name, 1);
        request.onupgradeneeded = () => {
          request.result.createObjectStore("drafts", { keyPath: "key" });
          request.result.createObjectStore("project_index", { keyPath: "projectId" });
        };
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
      });
      const mutate = (operation) => new Promise((resolve, reject) => {
        const tx = db.transaction(["drafts", "project_index"], "readwrite");
        const draftsRequest = tx.objectStore("drafts").getAll();
        const indexesRequest = tx.objectStore("project_index").getAll();
        let drafts;
        let indexes;
        let next;
        const run = () => {
          if (!drafts || !indexes || next) return;
          try {
            next = operation({ drafts, indexes });
            tx.objectStore("drafts").clear();
            tx.objectStore("project_index").clear();
            for (const draft of next.drafts) tx.objectStore("drafts").put(draft);
            for (const index of next.indexes) tx.objectStore("project_index").put(index);
          } catch (error) {
            tx.abort();
            reject(error.message);
          }
        };
        draftsRequest.onsuccess = () => { drafts = draftsRequest.result; run(); };
        indexesRequest.onsuccess = () => { indexes = indexesRequest.result; run(); };
        tx.oncomplete = () => resolve(next);
        tx.onerror = () => reject(tx.error?.name);
        tx.onabort = () => reject(tx.error?.name);
      });
      const add = (key) => mutate(({ drafts }) => ({
        drafts: [...drafts, { key, projectId: "p", path: key, draft: key }],
        indexes: [{ projectId: "p", keys: [...drafts.map((draft) => draft.key), key], count: drafts.length + 1, bytes: 1 }],
      }));
      await Promise.all([add("a"), add("c")]);
      const snapshot = await mutate((current) => current);
      let aborted = false;
      try {
        await mutate(() => { throw new Error("QuotaExceededError"); });
      } catch {
        aborted = true;
      }
      const afterAbort = await mutate((current) => current);
      db.close();
      await new Promise((resolve) => {
        const request = indexedDB.deleteDatabase(name);
        request.onsuccess = request.onerror = request.onblocked = () => resolve();
      });
      return { keys: snapshot.drafts.map((draft) => draft.key).sort(), aborted, afterAbort };
    });
    assert.deepEqual(result.keys, ["a", "c"]);
    assert.equal(result.aborted, true);
    assert.deepEqual(result.afterAbort.drafts.map((draft) => draft.key).sort(), ["a", "c"]);
  } finally {
    await browser.close();
  }
});
