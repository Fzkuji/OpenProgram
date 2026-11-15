import assert from "node:assert/strict";
import test from "node:test";

import {
  matchingIndexes,
  visibleSearchPaths,
} from "../../components/files/explorer/explorer-search.ts";

test("fuzzy and exact matching return only matched character positions", () => {
  assert.deepEqual(matchingIndexes("paper_search", "pa", true), [0, 1]);
  assert.deepEqual(matchingIndexes("templates", "pa", true), [3, 5]);
  assert.equal(matchingIndexes("sample_pipeline", "pa", true), null);
  assert.deepEqual(matchingIndexes("paper_search", "paper", false), [0, 1, 2, 3, 4]);
  assert.equal(matchingIndexes("templates", "pa", false), null);
});

test("filter mode retains matching paths and their ancestors", () => {
  const paths = [
    "tools",
    "tools/web",
    "applications",
    "applications/paper_search",
    "applications/sample_pipeline",
  ];
  assert.deepEqual(
    [...visibleSearchPaths(paths, "pa", true)],
    ["applications", "applications/paper_search"],
  );
});
