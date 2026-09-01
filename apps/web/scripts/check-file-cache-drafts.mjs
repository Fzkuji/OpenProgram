import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const shared = readFileSync(new URL("../lib/state/files-shared.ts", import.meta.url), "utf8");
const viewer = readFileSync(new URL("../components/files/file-viewer.tsx", import.meta.url), "utf8");
const pane = readFileSync(new URL("../components/center-tabs/file-tab-pane.tsx", import.meta.url), "utf8");
const lifecycle = readFileSync(new URL("../components/center-tabs/use-tab-lifecycle.ts", import.meta.url), "utf8");

assert.match(shared, /READ_CACHE_MAX_ENTRIES\s*=\s*64/);
assert.match(shared, /READ_CACHE_MAX_BYTES\s*=\s*16 \* 1024 \* 1024/);
assert.match(shared, /fileReadCacheKey\(projectId, path, mtime\)/);
assert.match(shared, /while \([\s\S]*readCache\.size >= READ_CACHE_MAX_ENTRIES/);
assert.match(shared, /TextEncoder/);
assert.match(shared, /indexedDB\.open\(DRAFT_DB_NAME/);
assert.match(shared, /createObjectStore\(DRAFT_STORE/);
assert.match(shared, /createObjectStore\(DRAFT_INDEX_STORE/);
assert.match(shared, /DRAFT_MAX_ENTRIES\s*=\s*32/);
assert.match(shared, /DRAFT_MAX_BYTES\s*=\s*8 \* 1024 \* 1024/);
assert.match(shared, /transaction\(\[DRAFT_STORE, DRAFT_INDEX_STORE\], "readwrite"\)/);
assert.match(shared, /QuotaExceededError/);
assert.match(shared, /moveFileDrafts/);
assert.match(shared, /clearProjectDrafts/);
assert.match(viewer, /getCachedFileRead\(projectId, path, knownMtime\)/);
assert.match(viewer, /cacheFileRead\(res\)/);
assert.match(pane, /loadFileDraft\(projectId, path\)/);
assert.match(pane, /persistFileDraft\(projectId, path, draft\)/);
assert.match(pane, /Local draft storage is full/);
assert.match(lifecycle, /discardFileDraft\(tab\.projectId, tab\.path\)/);

console.log("file cache and durable draft contracts: ok");
