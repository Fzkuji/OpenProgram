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
});

test("search results reveal the real tree path instead of becoming a second tree", () => {
  assert.match(source, /async function revealSearchResult/);
  assert.match(source, /while \(loaded\?\.next_cursor/);
  assert.match(source, /setFilter\(""\)/);
  assert.match(source, /revealTarget\.current = path/);
});
