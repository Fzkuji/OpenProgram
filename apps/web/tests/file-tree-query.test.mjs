import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const source = readFileSync(
  new URL("../components/files/file-tree.tsx", import.meta.url),
  "utf8",
);

test("FileTree pages directories with snapshot cursors and an accessible load-more row", () => {
  assert.match(source, /interface DirectoryPage/);
  assert.match(source, /nextCursor: string \| null/);
  assert.match(source, /project_file_tree/);
  assert.match(source, /cursor, snapshot_id/);
  assert.match(source, /Load more/);
  assert.match(source, /aria-label=\{text\("Load more entries"/);
  assert.match(source, /all\.findIndex\(\(candidate\) => candidate\.name === entry\.name\)/);
});

test("FileTree bounds search results and cancels generation-stale queries", () => {
  assert.match(source, /project_file_search/);
  assert.match(source, /setTimeout\(\(\) =>/);
  assert.match(source, /const generation = \+\+searchGeneration\.current/);
  assert.match(source, /generation !== searchGeneration\.current/);
  assert.match(source, /const MAX_SEARCH_RESULTS = 500/);
  assert.match(source, /const searchRows = useRef\(new Map<string, SearchResult>\(\)\)/);
  assert.match(source, /const searchCursor = useRef<string \| null>\(null\)/);
  assert.match(source, /const searchSnapshot = useRef<string \| null>\(null\)/);
  assert.match(source, /const searchLoadingGeneration = useRef<number \| null>\(null\)/);
  assert.match(source, /const searchControllers = useRef\(new Set<AbortController>\(\)\)/);
  assert.match(source, /for \(const controller of searchControllers\.current\) controller\.abort\(\)/);
  assert.match(source, /try \{[\s\S]*finally \{[\s\S]*searchLoadingGeneration\.current === generation/);
  assert.match(source, /fetchSearchPage\(searchGeneration\.current\)/);
  assert.doesNotMatch(source, /fetchedPages < Math\.min\(searchPage/);
  assert.match(source, /new AbortController\(\)/);
  assert.match(source, /filesWsRequest/);
  assert.doesNotMatch(source, /queryQueue/);
  assert.match(source, /\(\) => generation === queryGeneration\.current/);
});

test("file changes invalidate only the owning project tree", () => {
  assert.match(source, /detail\?\.project_id !== projectId/);
  assert.match(source, /detail: \{ project_id: projectId \}/);
  assert.match(source, /invalidateFileRead\(projectId, String\(payload\.path/);
});

test("file changes do not abort durable mutation requests", () => {
  assert.match(source, /const mutationControllers = useRef\(new Set<AbortController>\(\)\)/);
  assert.match(source, /mutationControllers\.current\.add\(operationController\)/);
  assert.match(source, /signal,\n\s+mutationControllers\.current/);
  assert.match(source, /Durable mutations must keep their own request lifecycle/);
});

test("FileTree teardown aborts requests but retains durable mutation keys", () => {
  assert.match(source, /function abortMutationRequests\(\)/);
  assert.doesNotMatch(source, /forgetWsMutation/);
  assert.match(source, /const mutationLifecycleGeneration = useRef\(0\)/);
  assert.match(source, /lifecycleGeneration !== mutationLifecycleGeneration\.current/);
  assert.match(source, /abortMutationRequests\(\);\n\s+queryGeneration\.current \+= 1/);
  assert.match(source, /mutationLifecycleGeneration\.current \+= 1/);
});

test("Filter and Highlight preserve their existing tree semantics", () => {
  assert.match(source, /filter\.trim\(\) && searchMode === "filter"/);
  assert.match(source, /searchMode === "highlight"/);
  assert.match(source, /async function locateTreePath/);
  assert.match(source, /role="listitem"/);
  assert.match(source, /renderDir\("", 0\)/);
});

test("search errors use the inline tree status and stale reveal state is cleared", () => {
  assert.match(source, /const \[searchError, setSearchError\]/);
  for (const code of ["LIMIT_EXCEEDED", "PERMISSION", "IO_ERROR", "INVALID_REQUEST"]) {
    assert.match(source, new RegExp(code));
  }
  assert.match(source, /revealTarget\.current = null/);
  assert.match(source, /revealScrollTimer\.current/);
  assert.match(source, /revealFlashTimer\.current/);
});

test("search results reveal and locate the real tree path instead of becoming a second tree", () => {
  assert.match(source, /async function revealSearchResult/);
  assert.match(source, /while \(/);
  assert.match(source, /loaded\?\.next_cursor/);
  assert.match(source, /setFilter\(""\)/);
  assert.match(source, /revealTarget\.current = path/);
});
