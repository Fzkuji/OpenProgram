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

test("FileTree searches the complete project with serialized, generation-checked queries", () => {
  assert.match(source, /project_file_search/);
  assert.match(source, /setTimeout\(\(\) =>/);
  assert.match(source, /const generation = \+\+searchGeneration\.current/);
  assert.match(source, /generation !== searchGeneration\.current/);
  assert.match(source, /const queryQueue = useRef<Promise<unknown>>/);
  assert.match(source, /next = queryQueue\.current\.then\(\(\) =>/);
  assert.match(source, /canRun\(\) \? wsRequest/);
  assert.match(source, /\(\) => generation === queryGeneration\.current/);
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
